#!/usr/bin/env python3
"""TotalSegmentator 橋渡しヘルパー（研究用venv `~/.tips_ts_venv` 側で実行する。torch/nibabel はここだけ）。

アプリ(純numpy)から:
    <ts_venv>/bin/python ts_helper.py --vol vol.npy --sx .. --sy .. --dz .. --out <dir> --total-rois liver,... [--task total|liver_vessels] [--device mps|cpu]

座標整合の担保:
  アプリの vol.array は (z=slice, y=row, x=col)、mm=(x*sx, y*sy, z*dz)。
  これを (x,y,z) に転置し affine=diag(sx,sy,dz,1) の NIfTI にして TS に渡す。
  TS の出力マスクは入力と同じボクセル格子で返るので、(x,y,z)→(z,y,x) に戻せば
  vol.array と 1:1 に一致する（DICOM の向き/反転の当て推量が不要）。
返り値: <out>/<name>.npy （bool, (z,y,x)）を構造ごとに保存し、標準出力に "OK <name>" を並べる。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys

# ── 自己防衛: このヘルパーはアプリ(bundle)内に同梱されるが、実行するのは外部の研究用venv。
#    Python はスクリプトの置き場所を sys.path[0] に自動で入れるため、そのままだと
#    venv 側の numpy / pydicom ではなく「アプリ同梱の(PyInstaller版・JPEGプラグインが壊れた)
#    ライブラリ」を掴んで落ちる。自分のディレクトリを sys.path から外し、必ず venv 側を使う。
#    （呼び側 ts_seg も PYTHONSAFEPATH=1 を渡すが、二重の保険としてここでも除去する）。
_self = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
sys.path[:] = [p for p in sys.path if p and os.path.realpath(p) != _self]

import numpy as np
import nibabel as nib

# total タスクで保存する構造は術式ごとに違うため、呼び側(ts_seg)が preset.py を読んで
# --total-rois で渡す（本ヘルパーは sys.path から自分の場所を外すので preset を import できない）。


def _phase(label=None):
    """処理の区切りごとに経過秒をログへ出す（どこで時間を食っているかを後から必ず追えるように）。
    先生から「初回が遅い、何が支配的か」の問いを受けて常設した計測（2026-07-21）。"""
    import time
    now = time.monotonic()
    if label is None or _phase.t0 is None:               # 計測開始
        _phase.t0 = _phase.last = now
        return
    print("TIME %-24s %6.1fs  (elapsed %6.1fs)" % (label, now - _phase.last, now - _phase.t0), flush=True)
    _phase.last = now


_phase.t0 = None
_phase.last = None


def _run_task(in_nii, out, task, device, roi_subset=None, want=None, z_keep=None):
    """1タスク実行→ want(集合) に含まれるラベルを (z,y,x) bool で out/<name>.npy に保存。保存名リストを返す。
    want=None なら全ラベル保存。既に .npy がある構造は上書きする（再計算のたび最新に）。"""
    from totalsegmentator.python_api import totalsegmentator
    seg_dir = os.path.join(out, "_seg_" + task)
    shutil.rmtree(seg_dir, ignore_errors=True)
    kw = dict(output=seg_dir, task=task, device=device, quiet=True)
    if roi_subset:
        kw["roi_subset"] = roi_subset
    totalsegmentator(in_nii, **kw)
    saved = []
    for f in sorted(os.listdir(seg_dir)):
        if not f.endswith(".nii.gz"):
            continue
        name = f[:-7]
        if want is not None and name not in want:
            continue
        m = nib.load(os.path.join(seg_dir, f)).get_fdata() > 0.5     # (x,y,z)
        m_zyx = np.ascontiguousarray(np.transpose(m, (2, 1, 0)))      # (z,y,x) = vol.array と一致
        m_zyx = _expand_z(m_zyx, z_keep)                              # 間引いていたら元のスライス数へ
        if int(m_zyx.sum()) == 0:
            continue
        np.save(os.path.join(out, name + ".npy"), m_zyx)
        saved.append(name)
        print("OK", name, int(m_zyx.sum()), flush=True)
    shutil.rmtree(seg_dir, ignore_errors=True)                       # 中間NIfTIは残さない
    return saved


def _seg_cmd(nii, seg_dir, task, device, roi_subset=None):
    """1タスクを別プロセスで走らせる python -c コマンド（並列実行用）。"""
    code = ("from totalsegmentator.python_api import totalsegmentator as T; "
            "T(%r, output=%r, task=%r, device=%r, quiet=True%s)" % (
                nii, seg_dir, task, device,
                (", roi_subset=%r" % list(roi_subset)) if roi_subset else ""))
    # universal binary が x86_64 スライスで立ち上がると numpy/torch(arm64のみ)を読めず落ちる → arm64 固定。
    pre = ["/usr/bin/arch", "-arm64"] if (sys.platform == "darwin" and platform.machine() == "arm64") else []
    return pre + [sys.executable, "-c", code]


def _expand_z(m_zyx, z_keep):
    """z を間引いて推論した場合に、マスクを元のスライス数へ戻す（最近傍）。

    間引いた側の index k は元の index k*step に対応するので、元の各 z について
    最も近い k を引く。AI が実際に計算した粒度は変わらないので情報は増減しない。
    """
    if not z_keep:
        return m_zyx
    nz_orig, step = z_keep
    idx = np.clip(np.rint(np.arange(nz_orig) / float(step)).astype(np.int64),
                  0, m_zyx.shape[0] - 1)
    return np.ascontiguousarray(m_zyx[idx])


def _collect(seg_dir, out, want, z_keep=None):
    """seg_dir の *.nii.gz を (z,y,x) bool に戻して out/<name>.npy へ保存。保存名リストを返す。
    z_keep が与えられたら、間引いて推論したマスクを元のスライス数へ戻してから保存する。"""
    saved = []
    if not os.path.isdir(seg_dir):
        return saved
    for f in sorted(os.listdir(seg_dir)):
        if not f.endswith(".nii.gz"):
            continue
        name = f[:-7]
        if want is not None and name not in want:
            continue
        m = nib.load(os.path.join(seg_dir, f)).get_fdata() > 0.5
        m_zyx = np.ascontiguousarray(np.transpose(m, (2, 1, 0)))
        m_zyx = _expand_z(m_zyx, z_keep)                  # 間引いていたら元のスライス数へ戻す
        if int(m_zyx.sum()) == 0:
            continue
        np.save(os.path.join(out, name + ".npy"), m_zyx)
        saved.append(name)
        print("OK", name, int(m_zyx.sum()), flush=True)
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--sx", type=float, required=True)
    ap.add_argument("--sy", type=float, required=True)
    ap.add_argument("--dz", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--task", default="total")           # total | liver_vessels
    ap.add_argument("--device", default="mps")           # mps | cpu | gpu
    ap.add_argument("--vessels", type=int, default=0)    # 1: 肝血管ツリー(liver_vessels=門脈+肝静脈)も抽出（要ライセンス）
    ap.add_argument("--orient", default="")              # 9 floats: dicom_io の orient 行列(列=rowd,cold,zdir・LPS)
    ap.add_argument("--total-rois", required=True)       # カンマ区切り: total タスクで保存する構造（術式差分・preset.py 由来）
    ap.add_argument("--extra-task", default="")          # 例: "lung_vessels"（EBUS）。totalに無いROIを別タスクで並列抽出
    ap.add_argument("--extra-rois", default="")          # カンマ区切り: extra-task から保存する構造
    # 解析の細かさ(mm)。TotalSegmentator は内部で 1.5mm に落として推論するので、それより薄い
    # スライスをそのまま渡すのは「大きく書き出して AI 側で捨ててもらう」だけの無駄になる。
    # 0 なら間引かない（従来どおり）。既定 0＝呼び側(preset/設定)が明示したときだけ効かせる。
    ap.add_argument("--z-step-mm", type=float, default=0.0)
    args = ap.parse_args()
    total_rois = [s for s in args.total_rois.split(",") if s]
    extra_rois = [s for s in args.extra_rois.split(",") if s]

    _phase()                                              # 計測開始
    os.makedirs(args.out, exist_ok=True)
    vol = np.load(args.vol)                               # (z,y,x) HU
    arr_xyz = np.ascontiguousarray(np.transpose(vol, (2, 1, 0)).astype(np.float32))  # (x,y,z)=(col,row,slice)
    # NIfTI は RAS+。dicom_io の向きは LPS。正しい解剖の向きで渡さないと TS が誤セグメントする
    # （+diag だと x,y が鏡像＝別人の向きに見え、肝/IVC がまともに取れない）。
    R = np.diag([-1.0, -1.0, 1.0])                        # LPS → RAS
    affine = np.eye(4, dtype=np.float64)
    if args.orient:
        o = np.array([float(t) for t in args.orient.split(",")], float).reshape(3, 3)  # 列=rowd,cold,zdir
        affine[:3, 0] = args.sx * (R @ o[:, 0])          # NIfTI x軸 = app col
        affine[:3, 1] = args.sy * (R @ o[:, 1])          # NIfTI y軸 = app row
        affine[:3, 2] = args.dz * (R @ o[:, 2])          # NIfTI z軸 = app slice
    else:                                                # 情報なし→標準 axial(HFS) 前提
        affine = np.diag([-args.sx, -args.sy, args.dz, 1.0]).astype(np.float64)
    dz_eff = float(np.linalg.norm(affine[:3, 2])) if args.orient else abs(float(args.dz))
    _phase("1_load_npy+transpose")
    # ★スライスが指定より薄いときは z を間引いてから渡す（推論の中身は変わらない＝AIは
    #   どのみち 1.5mm へ落とす）。出来たマスクは _collect で元のスライス数へ戻す。
    z_keep = None                                         # 間引いた場合: 元z数と採用したstep
    if args.z_step_mm > 0 and dz_eff > 0 and args.z_step_mm > dz_eff * 1.25:
        step = int(round(args.z_step_mm / dz_eff))
        if step >= 2:
            z_keep = (arr_xyz.shape[2], step)
            arr_xyz = np.ascontiguousarray(arr_xyz[:, :, ::step])
            affine[:3, 2] = affine[:3, 2] * step
            print("ZSTEP %d (%.2fmm -> %.2fmm, %d -> %d slices)"
                  % (step, dz_eff, dz_eff * step, z_keep[0], arr_xyz.shape[2]), flush=True)
    in_nii = os.path.join(args.out, "_input.nii.gz")
    nib.save(nib.Nifti1Image(arr_xyz, affine), in_nii)
    _phase("2_write_nifti(gzip)")

    if not args.vessels and not args.extra_task:
        # 追加タスク不要 → total だけ（同一プロセスで）。
        saved = _run_task(in_nii, args.out, "total", args.device,
                          roi_subset=total_rois, want=set(total_rois), z_keep=z_keep)
    elif args.vessels:
        # total と liver_vessels は独立 → 並列サブプロセスで実行（逐次の約1.6倍速）。
        # 逐次 total(55s)+liver_vessels(51s)=106s → 並列 wall-clock ≈65s（実測・M5 Max・836スライス）。
        seg_t = os.path.join(args.out, "_seg_total"); seg_v = os.path.join(args.out, "_seg_lv")
        shutil.rmtree(seg_t, ignore_errors=True); shutil.rmtree(seg_v, ignore_errors=True)
        pt = subprocess.Popen(_seg_cmd(in_nii, seg_t, "total", args.device, total_rois))
        pv = subprocess.Popen(_seg_cmd(in_nii, seg_v, "liver_vessels", args.device))
        rc_t = pt.wait(); rc_v = pv.wait()
        _phase("3_inference(total+lv)")
        saved = []
        if rc_t == 0:
            saved += _collect(seg_t, args.out, set(total_rois), z_keep)
            _phase("4a_collect_total")
        else:
            print("SKIP total: rc", rc_t, flush=True)
        if rc_v == 0:                                    # ライセンス未設定/失敗でも total は壊さない
            # liver_vessels タスクは肝血管ツリーと **肝腫瘍** を同時に出す。腫瘍は追加コスト
            # ゼロ（同じ推論の産物）なのに以前は捨てていた → 一緒に保存する（2026-07-18）。
            saved += _collect(seg_v, args.out, {"liver_vessels", "liver_tumor"}, z_keep)
            _phase("4b_collect_vessels")
        else:
            print("SKIP liver_vessels: rc", rc_v, flush=True)
        shutil.rmtree(seg_t, ignore_errors=True); shutil.rmtree(seg_v, ignore_errors=True)
    else:
        # total と extra_task（例: EBUS の lung_vessels）は独立 → 並列サブプロセスで実行。
        # extra_task にはライセンス不要（lung_vessels は Apache-2.0）なので失敗時も total は活かす。
        seg_t = os.path.join(args.out, "_seg_total"); seg_e = os.path.join(args.out, "_seg_extra")
        shutil.rmtree(seg_t, ignore_errors=True); shutil.rmtree(seg_e, ignore_errors=True)
        pt = subprocess.Popen(_seg_cmd(in_nii, seg_t, "total", args.device, total_rois))
        pe = subprocess.Popen(_seg_cmd(in_nii, seg_e, args.extra_task, args.device))
        rc_t = pt.wait(); rc_e = pe.wait()
        saved = []
        if rc_t == 0:
            saved += _collect(seg_t, args.out, set(total_rois), z_keep)
        else:
            print("SKIP total: rc", rc_t, flush=True)
        if rc_e == 0:
            saved += _collect(seg_e, args.out, set(extra_rois), z_keep)
        else:
            print("SKIP", args.extra_task, ": rc", rc_e, flush=True)
        shutil.rmtree(seg_t, ignore_errors=True); shutil.rmtree(seg_e, ignore_errors=True)
    try:
        os.remove(in_nii)
    except OSError:
        pass
    _phase("5_cleanup")
    print("DONE", len(saved), flush=True)


if __name__ == "__main__":
    sys.exit(main())
