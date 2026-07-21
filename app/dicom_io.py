"""DICOM シリーズ読込（Miele の代替）。フォルダ/ファイル列 → HUボリューム + spacing。

pydicom + GDCM/pylibjpeg で JPEG Lossless 等の圧縮も展開（デコーダは pip 導入時に自動登録）。
シリーズは ImagePositionPatient（スライス法線方向）でソート、RescaleSlope/Intercept で HU化。
"""
from __future__ import annotations
import os
import glob
import numpy as np

try:
    import pydicom
except Exception:                                   # pragma: no cover
    pydicom = None


# ボリューム保持上限（int16）。HUは整数なのでint16で十分＝float32の半分のメモリ。
_MAX_BYTES = 4_000_000_000          # ~4GB（int16）。512²なら約7600枚まで全読み


class FolderUnreadableError(RuntimeError):
    """フォルダの一覧取得が時間内に返らなかった（読めない場所を掴んで固まった）。

    macOS では他アプリのコンテナ（例: Miele の受け渡し用一時フォルダ）やクラウド同期フォルダを
    開こうとすると、`open()` がカーネル内で**戻ってこない**ことがある（プライバシー許可の確認待ち、
    同期プロバイダの無応答など）。進捗ダイアログの Cancel は協調キャンセルなので、システムコールで
    止まったスレッドには効かない＝アプリが永久に固まる。2026-07-20 に実機で発生（Miele からの送信）。
    """


def list_files_bounded(root, timeout=20.0):
    """root 以下のファイルを再帰列挙。timeout 秒で終わらなければ FolderUnreadableError。

    走査は **切り離せるデーモンスレッド** で行う。ブロックしたシステムコールは中断できないので、
    「待つのをやめてアプリを返す」ことだけが現実的な逃げ道になる（スレッドはOSが後片付けする）。
    """
    import threading
    out, err = [], []

    def work():
        try:
            out.extend(glob.glob(os.path.join(root, "**", "*"), recursive=True))
        except Exception as ex:                            # pragma: no cover - 実環境依存
            err.append(ex)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise FolderUnreadableError(_unreadable_message(root, timeout))
    if err:
        raise err[0]
    return list(out)


def _unreadable_message(root, timeout):
    en = (f"Could not read this folder within {timeout:.0f} seconds — it is still not responding:\n"
          f"  {root}\n\n"
          "This usually means macOS is withholding access to another app's data folder, or a "
          "cloud-sync folder (iCloud / Dropbox / Google Drive) is not responding.\n\n"
          "What to try:\n"
          "  1. System Settings → Privacy & Security → Files and Folders (and App Management), "
          "allow TIPS ICE Planner, then restart it\n"
          "  2. Or export the study from Miele to a normal folder (e.g. Desktop) and use "
          "“Open DICOM…” instead\n\n"
          "Nothing was imported. The app is still usable.")
    ja = (f"このフォルダを {timeout:.0f} 秒待っても読み取れませんでした（応答がありません）:\n"
          f"  {root}\n\n"
          "多くの場合、macOS が**他のアプリのデータフォルダ**へのアクセスを保留しているか、"
          "クラウド同期フォルダ（iCloud / Dropbox / Google Drive）が応答していないのが原因です。\n\n"
          "対処:\n"
          "  ① システム設定 → プライバシーとセキュリティ →「ファイルとフォルダ」「App管理」で "
          "TIPS ICE Planner を許可 → アプリを再起動\n"
          "  ② または Miele から通常のフォルダ（デスクトップ等）へ書き出し、"
          "「DICOMを開く…」から読み込む\n\n"
          "取り込みは行っていません。アプリはこのまま使えます。")
    return en + "@@JA@@" + ja


class StaleSeriesError(RuntimeError):
    """カタログの記録と、その場所にある実ファイルの中身が食い違う（＝別の検査に置き換わった）。

    患者リストの見出し（患者・モダリティ）と実際に開かれる画像が一致しない状態なので、
    **黙って開かず必ず止める**。2026-07-20 に実機で発生（MR のはずが別患者の CT）。
    """


def _stale_message(expect, other, n_files):
    """先生に見せる説明文（英日）。何が期待され、実際に何が入っていたかを具体的に示す。"""
    exp_mod = expect.get("modality") or "?"
    exp_pid = expect.get("patient_id") or "?"
    exp_desc = expect.get("series_desc") or ""
    lines_en, lines_ja = [], []
    for uid, (cnt, ds) in list(other.items())[:3]:
        mod = str(getattr(ds, "Modality", "?"))
        pid = str(getattr(ds, "PatientID", "?"))
        desc = str(getattr(ds, "SeriesDescription", "") or "")
        lines_en.append(f"  · {cnt} images: {mod}, patient ID {pid} {('(' + desc + ')') if desc else ''}")
        lines_ja.append(f"  ・{cnt}枚: {mod}／患者ID {pid} {('（' + desc + '）') if desc else ''}")
    en = ("The files recorded for this series no longer contain this series.\n\n"
          f"Catalogue says: {exp_mod}, patient ID {exp_pid} {('(' + exp_desc + ')') if exp_desc else ''}"
          f", {n_files} images\nActually on disk:\n" + "\n".join(lines_en) +
          "\n\nThe folder was most likely reused for a different study, so the stored paths now "
          "point at other images. Nothing was opened. Remove this entry from the patient list and "
          "import the folder again.")
    ja = ("このシリーズとして記録されているファイルの中身が、別の検査に置き換わっています。\n\n"
          f"カタログの記録: {exp_mod}／患者ID {exp_pid} {('（' + exp_desc + '）') if exp_desc else ''}"
          f"／{n_files}枚\n実際にあった画像:\n" + "\n".join(lines_ja) +
          "\n\n同じフォルダを別の検査で使い回したため、記録されたパスが別の画像を指しています。"
          "**取り違えを防ぐため開きませんでした。** 患者リストからこの検査を削除して、"
          "取り込み直してください。")
    return en + "\n@@JA@@" + ja

class Volume:
    """[nz, H, W] HU(int16既定) と物理 spacing(mm)。tips_core が期待する形。"""
    def __init__(self, array, sx, sy, dz, meta=None):
        self.array = np.ascontiguousarray(array)          # dtypeは呼び出し側に従う(int16/float32)
        self.sx = float(sx)          # 列ピッチ mm (x)
        self.sy = float(sy)          # 行ピッチ mm (y)
        self.dz = float(dz)          # スライス間隔 mm (z)
        self.meta = meta or {}

    @property
    def shape(self):
        return self.array.shape


def is_dicom(path):
    try:
        with open(path, "rb") as f:
            f.seek(128)
            if f.read(4) == b"DICM":
                return True
    except Exception:
        return False
    return path.lower().endswith(".dcm")


def slice_sort_key(ds):
    """ImagePositionPatient を法線へ射影（無ければ InstanceNumber）。"""
    ipp = getattr(ds, "ImagePositionPatient", None)
    iop = getattr(ds, "ImageOrientationPatient", None)
    if ipp is not None and iop is not None:
        ipp = np.array(ipp, float)
        r = np.array(iop[:3], float); c = np.array(iop[3:], float)
        return float(np.dot(ipp, np.cross(r, c)))
    return float(getattr(ds, "InstanceNumber", 0) or 0)


def _require():
    if pydicom is None:
        raise RuntimeError("pydicom 未導入。`pip install pydicom python-gdcm pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg`")


def _build_volume(files, meta=None, progress=None, expect=None):
    """ファイル列（同一シリーズ想定）→ Volume(int16 HU)。位置ソート + HU化。
    メモリ上限(_MAX_BYTES, int16)を超える時だけzを等間隔間引き（dzは実位置から再計算で正確）。

    expect が与えられたら、**実ファイルが本当にそのシリーズか**を照合し、違う画像は取り込まない。
    """
    _require()
    want = str((expect or {}).get("series_uid") or "")
    items = []
    other = {}                                      # 期待と違うシリーズ: uid → (件数, 代表ds)
    n = len(files)
    for j, p in enumerate(files):
        if progress and j % 100 == 0:
            progress(int(j * 0.15), n)              # メタ走査=前半15%
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            continue
        if "Rows" not in ds:
            continue
        if want:                                    # ★中身の照合（別検査への差し替わりを検出）
            got = str(getattr(ds, "SeriesInstanceUID", "") or "")
            if got != want:
                cnt, rep = other.get(got, (0, ds))
                other[got] = (cnt + 1, rep)
                continue
        items.append((p, slice_sort_key(ds), ds))
    if not items and other:
        raise StaleSeriesError(_stale_message(expect or {}, other, n))
    if not items:
        if files and not any(os.path.exists(p) for p in files):
            raise RuntimeError("この検査のファイルが見つかりません（移動・削除された可能性があります）。"
                               "\n最初のパス: " + str(files[0]))
        raise RuntimeError("読み取れる DICOM 画像がありません。")
    items.sort(key=lambda t: t[1])

    first = items[0][2]
    H = int(first.Rows); W = int(first.Columns)
    ps = getattr(first, "PixelSpacing", [1.0, 1.0])
    sy, sx = float(ps[0]), float(ps[1])             # PixelSpacing = [row(y), col(x)]

    # index軸 → 患者LPS の向き（3D方位キューブ用）。列=+x(col)/+y(row)/+z(slice) のLPS方向。
    orient = None
    iop = getattr(first, "ImageOrientationPatient", None)
    if iop is not None and len(iop) == 6:
        try:
            rowd = np.array(iop[0:3], float)            # +col(x)方向
            cold = np.array(iop[3:6], float)            # +row(y)方向
            zdir = np.cross(rowd, cold)                 # slice法線（位置昇順ソート=+z indexは+法線）
            orient = np.column_stack([rowd, cold, zdir]).tolist()
        except Exception:
            orient = None

    note = ""
    limit = max(200, _MAX_BYTES // (H * W * 2))      # int16=2byte/voxel。上限超のみ間引き
    if len(items) > limit:
        stride = (len(items) + limit - 1) // limit
        items = items[::stride]
        note = f"z downsampled x{stride} ({len(items)} slices, memory guard ~{_MAX_BYTES // 10**9}GB)"

    vol = np.empty((len(items), H, W), dtype=np.int16)
    zpos = []
    m = len(items)
    for i, (p, k, _) in enumerate(items):
        if progress and i % 20 == 0:
            progress(int(n * 0.15 + (i / max(m, 1)) * n * 0.85), n)   # 画素読込=後半85%
        ds = pydicom.dcmread(p)                      # ピクセル込み（デコーダ自動）
        arr = ds.pixel_array.astype(np.float32)
        if arr.shape != (H, W):                      # 寸法不一致スライスは無視できないので中断回避: リサイズせず弾く
            arr = np.full((H, W), -1000.0, np.float32)
        slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
        inter = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
        vol[i] = np.clip(np.rint(arr * slope + inter), -32768, 32767)   # → HU(int16)
        zpos.append(k)

    if len(zpos) >= 2:
        diffs = np.abs(np.diff(np.array(zpos, float))); diffs = diffs[diffs > 1e-3]
        dz = float(np.median(diffs)) if diffs.size else float(getattr(first, "SliceThickness", 1.0) or 1.0)
    else:
        dz = float(getattr(first, "SliceThickness", 1.0) or 1.0)
    if dz <= 0:
        dz = 1.0

    m = dict(n=len(items), modality=str(getattr(first, "Modality", "")))
    if orient is not None:
        m["orient"] = orient
    if note:
        m["note"] = note
    if meta:
        m.update(meta)
    return Volume(vol, sx=sx, sy=sy, dz=dz, meta=m)


def load_series_files(files, meta=None, progress=None, expect=None):
    """既知のシリーズ（ファイル列）を読む。catalog から呼ばれる。

    expect=dict(series_uid, patient_id, modality, …) を渡すと、**実ファイルの中身が本当に
    そのシリーズか**を読み込み時に照合する（違えば StaleSeriesError）。カタログはパスで
    覚えるので、同じフォルダを別検査で使い回されると中身だけ別患者に入れ替わる。
    """
    return _build_volume(files, meta, progress, expect=expect)


def load_series(folder, progress=None):
    """フォルダ内の最大シリーズを読み、Volume を返す（単発オープン用）。"""
    _require()
    files = [p for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True)
             if os.path.isfile(p) and is_dicom(p)]
    if not files:
        raise RuntimeError("DICOM ファイルが見つかりません: " + folder)
    series = {}
    for p in files:
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            continue
        if "Rows" not in ds:
            continue
        series.setdefault(getattr(ds, "SeriesInstanceUID", "?"), []).append(p)
    if not series:
        raise RuntimeError("読み取れる DICOM 画像がありません: " + folder)
    uid = max(series, key=lambda k: len(series[k]))
    return _build_volume(series[uid], progress=progress)


def load_npy(npy_path, meta_path=None):
    """開発用: real_local/vol.npy など既存ボリュームを読む。"""
    import json
    arr = np.load(npy_path)
    sx = sy = dz = 1.0
    if meta_path is None:
        meta_path = os.path.join(os.path.dirname(npy_path), "vol_meta.json")
    if os.path.exists(meta_path):
        m = json.load(open(meta_path))
        dz = float(m.get("dz", 1.0)); sy = float(m.get("dy", 1.0)); sx = float(m.get("dx", 1.0))
    return Volume(arr, sx=sx, sy=sy, dz=dz, meta=dict(source="npy"))
