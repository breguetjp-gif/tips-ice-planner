"""TotalSegmentator 橋渡し（アプリ側・純numpy／torch・nibabel は呼ばない）。正本 = engine/core/ts_seg.py。

研究用venv `~/.tips_ts_venv` に TotalSegmentator が入っていれば、造影CTから解剖構造を抽出して
3D 連動パネルに色分けで重ねる。入っていなければ available()=False で、アプリは従来の
軽量ゴーストにフォールバックする（配布版は軽量のまま・後輩の環境で壊れない）。

座標整合は ts_helper.py 側が担保（アプリ体積を正しい向きの NIfTI にして TS へ渡し、
出力マスクを (z,y,x) に戻す＝vol.array と 1:1）。ここはその起動と、点群化だけを行う。

術式差（Phase 3 統一, 2026-07-18）は preset.py から読む：
  TOTAL_ROIS       抽出対象（ROIS/ORGANS になる。tips=肝/IVC/門脈 3構造・eus=腹部16構造）
  TS_ROIS_VERSION  抽出セットのバージョン（変えたら過去キャッシュは別キー＝自動作り直し）
  TS_SCENE_MODE    build_scene の流儀: "fixed3"=肝ゴースト+IVC/門脈/肝血管（tips）
                                      "organs"=腸管ゴースト+選択臓器の色分け点群（eus）
  TS_DEFAULT_SHOW  organs モードで既定表示ONにする構造（fixed3 では未使用）
ORGAN_COLORS / ORGAN_LABELS は解剖の普遍データなので術式によらずここに一本化。
"""
import os
import sys
import shutil
import subprocess
import platform

import numpy as np

import preset


def _arch_prefix():
    """Apple Silicon で universal binary の TS venv を *確実に arm64 で* 起動する接頭辞。
    アプリの python(venv)は arm64 専用だが、そこから universal(x86_64+arm64)の TS venv python を
    posix_spawn で起動すると x86_64 スライスで立ち上がることがあり（実測・M5 Max）、arm64 でしか
    ビルドされていない numpy/torch を読めず import で即クラッシュ→「AIが黙って動かない」になる。
    `arch -arm64` で arm64 スライスを明示指定して固定する。"""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return ["/usr/bin/arch", "-arm64"]
    return []

try:
    from tips_core import liver as _lv                    # _pack（マスク→surf/nrm 点群）を流用
except Exception:                                         # pragma: no cover
    _lv = None

TS_VENV = os.path.expanduser("~/.tips_ts_venv")           # 研究用venv（重いAIはここだけ・配布アプリと分離）
_BIN = "Scripts" if os.name == "nt" else "bin"
TS_VENV_PY = os.path.join(TS_VENV, _BIN, "python.exe" if os.name == "nt" else "python")
_PIP = os.path.join(TS_VENV, _BIN, "pip")
# 抽出対象は術式プリセットから（TS の roi_subset。total タスクが1回の推論で全部出す＝追加DL不要）
ROIS = tuple(preset.TOTAL_ROIS)
ORGANS = ROIS
# 抽出セットのバージョン。ここ（preset）を変えたら過去キャッシュ（旧セット）は別キーになり自動で作り直す。
ROIS_VERSION = preset.TS_ROIS_VERSION
# TOTAL_ROIS の一部が "total" タスクの class map に無い場合（例: EBUS の lung_airways/lung_arteries/
# lung_veins は "total" ではなく別タスク "lung_vessels" にしかない）、術式プリセットが
# TS_EXTRA_TASK（タスク名）＋TS_EXTRA_TASK_ROIS（そのタスクだけにあるROI）を宣言する。
# 未宣言（tips/eus）なら None/空＝以前と完全に同じ「total 単独」の挙動のまま。
EXTRA_TASK = getattr(preset, "TS_EXTRA_TASK", None) or None
EXTRA_TASK_ROIS = tuple(getattr(preset, "TS_EXTRA_TASK_ROIS", ()) or ())
# "完成キャッシュ" 判定のアンカー構造（tips/eusは暗黙に"liver"。EBUSのように肝臓を抽出しない
# 術式は preset.TS_ANCHOR_ORGAN で明示する。未宣言なら"liver"＝既存2アプリと同じ判定のまま）。
ANCHOR_ORGAN = getattr(preset, "TS_ANCHOR_ORGAN", "liver")
# 各構造の表示色 RGB（3D点群/ゴーストの色分け。解剖学的に自然な色）。全術式共通の普遍データ。
ORGAN_COLORS = {
    "pancreas": (232, 161, 92), "esophagus": (143, 208, 232), "stomach": (111, 177, 255),
    "duodenum": (255, 207, 111), "gallbladder": (95, 191, 127), "liver": (181, 101, 74),
    "spleen": (155, 111, 176), "kidney_right": (192, 138, 90), "kidney_left": (192, 138, 90),
    "adrenal_gland_right": (232, 209, 92), "adrenal_gland_left": (232, 209, 92),
    "aorta": (255, 93, 93), "inferior_vena_cava": (111, 143, 176),
    "portal_vein_and_splenic_vein": (74, 144, 217),
    "small_bowel": (224, 176, 144), "colon": (208, 160, 128),
    "bile_duct": (127, 208, 96),                         # 手描き胆管（AIには無いクラス）＝緑
    "pancreatic_duct": (255, 170, 60),                   # 手描き膵管（AIには無いクラス）＝橙
    "liver_tumor": (255, 105, 180),                      # 肝腫瘍（liver_vessels タスクの副産物）＝目立つピンク
    # ── EBUS（経気道）で使う胸部構造。tips/eus の ORGANS には現れないため無害な追加 ──
    "trachea": (143, 208, 232), "lung_airways": (120, 190, 220),
    "lung_arteries": (255, 59, 59), "lung_veins": (111, 183, 255),
    "pulmonary_vein": (126, 200, 255), "superior_vena_cava": (92, 157, 255),
    "brachiocephalic_trunk": (255, 154, 92), "heart": (255, 119, 200),
}
# 設定チェックリスト用の日英表示名（全術式共通）
ORGAN_LABELS = {
    "pancreas": ("Pancreas", "膵臓"), "esophagus": ("Esophagus", "食道"),
    "stomach": ("Stomach", "胃"), "duodenum": ("Duodenum", "十二指腸"),
    "gallbladder": ("Gallbladder", "胆嚢"), "liver": ("Liver", "肝臓"),
    "spleen": ("Spleen", "脾臓"), "kidney_right": ("Kidney (R)", "右腎"),
    "kidney_left": ("Kidney (L)", "左腎"), "adrenal_gland_right": ("Adrenal (R)", "右副腎"),
    "adrenal_gland_left": ("Adrenal (L)", "左副腎"), "aorta": ("Aorta", "大動脈"),
    "inferior_vena_cava": ("IVC", "下大静脈"),
    "portal_vein_and_splenic_vein": ("Portal/splenic vein", "門脈・脾静脈"),
    "small_bowel": ("Small bowel", "小腸"), "colon": ("Colon", "結腸"),
    "liver_tumor": ("Liver tumor", "肝腫瘍"),
    "trachea": ("Trachea", "気管"), "lung_airways": ("Bronchial tree", "気管支樹"),
    "lung_arteries": ("Pulmonary artery", "肺動脈"), "lung_veins": ("Pulmonary vein (peripheral)", "肺静脈(末梢)"),
    "pulmonary_vein": ("Pulmonary vein (central)", "肺静脈(中枢)"),
    "superior_vena_cava": ("SVC", "上大静脈"),
    "brachiocephalic_trunk": ("Brachiocephalic trunk", "腕頭動脈"), "heart": ("Heart", "心臓"),
}
# organs モードで既定表示ONにする構造（術式プリセットから。fixed3 モードでは未使用）
DEFAULT_SHOW = tuple(preset.TS_DEFAULT_SHOW)
HEPATIC = "liver_vessels"                                 # 肝血管ツリー（門脈枝＋肝静脈枝を一括・要ライセンス）
TUMOR = "liver_tumor"                                     # 肝腫瘍（liver_vessels タスクの副産物＝追加コストゼロ）
ALL_MASKS = ROIS + (HEPATIC, TUMOR)
# fixed3 モードで、主シーン（肝ゴースト+IVC/門脈/肝血管）に *加えて* 色分け表示する構造の *既定*。
# 追加抽出は total タスクの roi_subset を広げるだけ＝推論時間はほぼ増えない（同じ1回の推論から切り出す）。
EXTRA_ORGANS = tuple(getattr(preset, "TS_EXTRA_ORGANS", ()))
# fixed3 の主シーンが常に描く3構造（＝臓器チェックリストには出さない。消せると混乱するため）
FIXED3_CORE = ("liver", "inferior_vena_cava", "portal_vein_and_splenic_vein")


# 解析の細かさ（先生決裁 2026-07-21「設定で選べるように」）。
# TotalSegmentator は内部で 1.5mm へ落として推論するので、それより薄いスライスをそのまま渡すのは
# 「大きく書き出して AI 側で捨ててもらう」だけの無駄になる。z_step_mm はその「渡す前に間引く厚さ」。
# 実測(1151枚 0.5mm厚・M5 Max): 高精度 81s / 標準(1.5mm) 55s。臓器体積の差は 1.5% 以内。
QUALITY_PRESETS = {
    "high":     (0.0, ("High accuracy", "高精度")),      # 間引かない＝送ったCTの粒度のまま（従来）
    "standard": (1.5, ("Standard", "標準")),             # AI が実際に見る 1.5mm に合わせる（既定）
    "fast":     (3.0, ("Fast", "速い")),                 # さらに粗く＝大きな構造の確認向け
}
QUALITY_DEFAULT = "standard"


def quality_z_step(name):
    """精度名 → 間引く厚さ(mm)。未知の名前は既定へ倒す。"""
    return QUALITY_PRESETS.get(name, QUALITY_PRESETS[QUALITY_DEFAULT])[0]


def selectable_organs():
    """ユーザーが表示ON/OFFを選べる構造の一覧（術式モードで中身が変わる）。

    organs(eus) は抽出した全構造。fixed3(tips) は「主シーンの3つを除いた、既に抽出済みの構造」＝
    **AIを再実行せずに表示を増やせる範囲**（設計方針「抽出は広く・表示は絞る」）。
    """
    if preset.TS_SCENE_MODE == "organs":
        return list(ORGANS)
    return [n for n in ORGANS if n not in FIXED3_CORE] + [TUMOR]


def default_shown(name):
    """その構造を既定で表示ONにするか（チェックリストの初期値）。"""
    if preset.TS_SCENE_MODE == "organs":
        return name in DEFAULT_SHOW
    return name in EXTRA_ORGANS
# 無料の非商用ライセンス（肝血管ツリー/肝静脈に必要）の登録先。取得は先生ご自身の学術アカウントで。
LICENSE_URL = "https://backend.totalsegmentator.com/license-academic/"


def available():
    """研究用venvに TotalSegmentator 実行環境があるか（未導入なら軽量フォールバック）。"""
    return os.path.exists(TS_VENV_PY)


def _clean_env():
    """凍結ビルド(PyInstaller)が仕込む DYLD_*/PYTHON* を除いた素の環境。
    外部venvの python / pip を壊さずに起動するために使う。"""
    drop = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
            "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_INSERT_LIBRARIES",
            "LD_LIBRARY_PATH", "_PYI_APPLICATION_HOME_DIR")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    for k in ("DYLD_LIBRARY_PATH_ORIG", "DYLD_FRAMEWORK_PATH_ORIG", "LD_LIBRARY_PATH_ORIG"):
        if k in os.environ:                              # PyInstaller が退避した元の値があれば戻す
            env[k[:-5]] = os.environ[k]
    # ヘルパーはアプリ(bundle)内に置かれるため、Python が既定でその置き場所(=アプリ同梱ライブラリ)
    # を sys.path に入れ、外部venvの numpy/pydicom を隠して壊す。SAFEPATH でその自動追加を止める。
    env["PYTHONSAFEPATH"] = "1"
    return env


def _system_python3():
    """venv 作成に使える素の python3（凍結アプリ自身は使えないので外部を探す）。無ければ None。"""
    cands = [shutil.which("python3"), shutil.which("python"),
             "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def install(log_path=None):
    """研究用venvを作り TotalSegmentator を入れる（~2GB・数分・ネットワーク要）。希望者だけのオプトイン導入。
    進捗は log_path に追記（UIが読む）。成功で True。"""
    def emit(s):
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(s + "\n")
            except OSError:
                pass
    py = _system_python3()
    if py is None:
        emit("Python 3 が見つかりません。先に Python 3 を入れてください（例: Homebrew の python3）。")
        return False
    env = _clean_env()
    try:
        if not os.path.exists(TS_VENV_PY):
            emit("仮想環境を作成中…  " + TS_VENV)
            subprocess.run([py, "-m", "venv", TS_VENV], check=True, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        emit("pip を更新中…")
        subprocess.run([_PIP, "install", "--upgrade", "pip"], check=True, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        emit("TotalSegmentator をインストール中…（~2GB・数分かかります）")
        proc = subprocess.Popen([_PIP, "install", "TotalSegmentator"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            if line and ("Downloading" in line or "Installing" in line or "Successfully" in line
                         or "error" in line.lower() or "Collecting" in line):
                emit(line)
        proc.wait()
        ok = proc.returncode == 0 and available()
        emit("✅ 導入が完了しました。CTを開くとAI解剖が表示されます。" if ok
             else "インストールに失敗しました。ネットワークやディスク容量をご確認ください。")
        return ok
    except Exception as e:
        emit("エラー: " + str(e))
        return False


def _helper_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ts_helper.py")


def cached_masks(cache_dir):
    """cache_dir に保存済みマスク(.npy)があれば読む。無ければ None。肝血管(liver_vessels)も有れば読む。"""
    got = {}
    for name in ALL_MASKS:
        p = os.path.join(cache_dir, name + ".npy")
        if os.path.exists(p):
            try:
                got[name] = np.load(p)
            except Exception:
                pass
    return got or None


def _totalseg_dir():
    """TS の設定ディレクトリ（ライセンス番号が入る config.json の置き場）。"""
    d = os.environ.get("TOTALSEG_HOME_DIR")
    return d if d else os.path.expanduser("~/.totalsegmentator")


def get_license():
    """設定済みの無料ライセンス番号（無ければ ""）。設定ダイアログのプリフィル用。"""
    import json
    p = os.path.join(_totalseg_dir(), "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("license_number", "") or ""
    except Exception:
        return ""


def license_set():
    """肝血管ツリー用の無料ライセンスが設定済みか（config.json に license_number があるか）。"""
    return bool(get_license())


def set_license(num):
    """無料ライセンス番号を config.json に保存（オフラインで確実に書く）。空文字で解除。成功で True。"""
    import json
    num = (num or "").strip()
    d = _totalseg_dir()
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "config.json")
        cfg = {}
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
        if num:
            cfg["license_number"] = num
        else:
            cfg.pop("license_number", None)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        return True
    except Exception:
        return False


def _kill_tree(proc):
    """ts_helper とその子(並列TS＝孫)まで一括停止。新セッションで起動していればグループごと殺す。"""
    import signal
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(5)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def segment(vol_zyx, sx, sy, dz, orient, cache_dir, device="mps", timeout=1200,
            progress=None, should_stop=None, vessels=False, log_path=None, quality=None):
    """TS を実行してマスク {name: bool(z,y,x)} を返す。cache_dir に .npy が残る。
    vessels=True: 肝血管ツリー(liver_vessels=門脈+肝静脈・要ライセンス)も抽出。
    未導入 / 失敗 は None（呼び側は軽量フォールバック）。必要分が cache に揃っていればそれを返す。
    should_stop(): True を返したら子プロセスを止めて撤退（アプリ終了時に固まらない/落ちないため）。"""
    hit = cached_masks(cache_dir)
    # アンカー構造（既定"liver"・EBUS等は preset.TS_ANCHOR_ORGAN で明示）が有って初めて「完成」とみなす。
    # IVCだけ等の不完全キャッシュ（中断・旧バグ版の産物）は完成扱いせず、下で作り直す。
    if hit is not None and ANCHOR_ORGAN in hit and (not vessels or HEPATIC in hit):
        return hit                                       # 欲しい分が揃っている（vessels要求時は肝血管も）
    if not available():
        return None
    import time
    os.makedirs(cache_dir, exist_ok=True)
    vp = os.path.join(cache_dir, "vol.npy")
    np.save(vp, np.ascontiguousarray(np.asarray(vol_zyx).astype(np.int16)))
    o = orient if orient is not None else [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    ostr = ",".join("%.6f" % float(v) for row in o for v in row)
    # TOTAL_ROIS の中で "total" タスクに実在しない名前（EXTRA_TASK_ROIS）は --total-rois から除く。
    # 混ぜて渡すと TotalSegmentator が KeyError で落ちる（"total" の class map に無いため）。
    total_task_rois = [r for r in preset.TOTAL_ROIS if r not in EXTRA_TASK_ROIS]
    cmd = _arch_prefix() + [TS_VENV_PY, _helper_path(), "--vol", vp, "--sx", "%.6f" % sx, "--sy", "%.6f" % sy,
           "--dz", "%.6f" % dz, "--out", cache_dir, "--task", "total", "--device", device,
           "--vessels", "1" if vessels else "0", "--orient", ostr,
           "--total-rois", ",".join(total_task_rois),
           "--z-step-mm", "%.3f" % quality_z_step(quality or QUALITY_DEFAULT)]
    if EXTRA_TASK and EXTRA_TASK_ROIS:
        cmd += ["--extra-task", EXTRA_TASK, "--extra-rois", ",".join(EXTRA_TASK_ROIS)]
    env = _clean_env()                                   # 凍結アプリの DYLD_*/PYTHON* を除いて外部venvを起動
    # 子プロセスの stdout/stderr は *ファイル* に流す。PIPE のまま poll ループで読まずに放置すると、
    # TS/nnU-Net の出力が OS のパイプバッファ(~64KB)を埋めた瞬間に子が write でブロック→デッドロック
    # （timeout まで固まって「AIが動かない」に見える）。ファイルなら詰まらず、失敗時は原因がログに残る。
    logp = log_path or os.path.join(os.path.dirname(cache_dir) or cache_dir, "ts_last_run.log")
    proc = None
    logf = None
    try:
        # run ではなく Popen + ポーリング＝中断要求(アプリ終了)で確実に子プロセスを終わらせる。
        # subprocess.run だと呼び側スレッドがブロックし、終了時に QThread 破棄→SIGABRT になり得る。
        # ts_helper は total と liver_vessels を *並列サブプロセス* で回すため、中断時は
        # プロセスグループごと止める必要がある → 新セッションで起動し killpg で一掃する。
        logf = open(logp, "w")
        logf.write("CMD: %s\n\n" % " ".join(cmd)); logf.flush()
        kw = dict(stdout=logf, stderr=subprocess.STDOUT, env=env)
        if os.name != "nt":
            kw["start_new_session"] = True               # 新プロセスグループ＝孫(並列TS)まで一括停止できる
        proc = subprocess.Popen(cmd, **kw)
        end = time.monotonic() + timeout
        while proc.poll() is None:
            if (should_stop is not None and should_stop()) or time.monotonic() > end:
                _kill_tree(proc)
                logf.write("\n[ABORTED: stop-requested or timeout]\n"); logf.flush()
                return None
            time.sleep(0.3)
        if proc.returncode != 0:
            logf.write("\n[FAILED: return code %s — see above]\n" % proc.returncode); logf.flush()
            return None
    except Exception as e:
        if proc is not None:
            _kill_tree(proc)
        if logf is not None:
            logf.write("\n[EXCEPTION: %r]\n" % e); logf.flush()
        return None
    finally:
        if logf is not None:
            logf.close()
        try:
            os.remove(vp)                                 # 体積の一時コピーは残さない
        except OSError:
            pass
    return cached_masks(cache_dir)


def _dilate6(m):
    """6近傍1ボクセル膨張（純numpy）。"""
    d = m.copy()
    d[1:] |= m[:-1]; d[:-1] |= m[1:]
    d[:, 1:] |= m[:, :-1]; d[:, :-1] |= m[:, 1:]
    d[:, :, 1:] |= m[:, :, :-1]; d[:, :, :-1] |= m[:, :, 1:]
    return d


def _vein_labels(masks, sx, sy, dz, ds=(2, 3, 3), max_iter=1200):
    """肝血管ツリーを門脈系(P)/肝静脈系(H)へ推定分離した **ラベルボリューム** を返す。
    返り値 dict(V,P,H,ds,confident) — V/P/H は (z,y,x) bool、confident=分離が信頼できるか。
    種が揃わなければ None。**偏りが大きくても None にせず confident=False で返す**＝手動再指定で訂正するため。

    ── 精度向上（2026-07-21）──
    門脈相の単相CTでは肝静脈が造影されにくく、TotalSegmentator の liver_vessels は門脈枝が主体になる
    （別モデルは存在しない＝AI そのものの分離能には天井がある）。改善できるのは *種の取り方*：
      ・門脈種＝門脈本幹(肝門部)。肝外で少し離れるので届くまで膨張（従来どおり）。
      ・肝静脈種＝IVC と血管ツリーが **実際に接する所だけ**（最小膨張）。従来は最大10回膨張で
        IVC 沿いの門脈枝まで種に取り込み、肝静脈を過大評価していた。接触点だけに絞ると誤りが減る。
    それでも単相では限界がある＝**最終手段は手動再指定**（confident=False を UI が使う）。
    """
    lv = masks.get(HEPATIC)
    por = masks.get("portal_vein_and_splenic_vein")
    ivc = masks.get("inferior_vena_cava")
    if lv is None or por is None or ivc is None:
        return None
    fz, fy, fx = ds
    V = np.ascontiguousarray(lv[::fz, ::fy, ::fx])
    if not V.any():
        return None

    def _seed(base, max_dil=10):
        """base を血管ツリー V に届くまで膨張して種を作る（門脈本幹は肝外で離れるため）。"""
        m = base.copy()
        for _ in range(max_dil):
            s = m & V
            if s.any():
                return s
            m = _dilate6(m)
        return m & V

    def _contact_seed(base, reach=3):
        """base（IVC）が V に接する所だけを種にする。届くまで最小膨張し、届いた瞬間に止める＝
        IVC 沿いの門脈枝を巻き込まない（肝静脈の過大評価を防ぐ・2026-07-21）。"""
        m = base.copy()
        for _ in range(reach):
            s = m & V
            if s.any():
                return s
            m = _dilate6(m)
        return m & V

    pseed = _seed(por[::fz, ::fy, ::fx])                    # 門脈本幹の近傍 ∩ 血管
    hseed = _contact_seed(ivc[::fz, ::fy, ::fx])           # IVC が血管に接する所だけ（絞る）
    if not pseed.any() or not hseed.any():
        return None                                  # どちらかに届かなければ分離しない
    P = pseed & ~hseed; H = hseed.copy()                   # 接触点が両取りになった場合は肝静脈側を優先
    for _ in range(max_iter):                              # 競合成長: 同時到達は門脈優先
        addp = _dilate6(P) & V & ~P & ~H; P |= addp
        addh = _dilate6(H) & V & ~H & ~P; H |= addh
        if not addp.any() and not addh.any():
            break
    rest = V & ~P & ~H                                     # 種に連ならない断片→近い方の重心に寄せる
    if rest.any() and P.any() and H.any():
        pc_ = np.argwhere(P).mean(0); hc_ = np.argwhere(H).mean(0)
        ri = np.argwhere(rest)
        toP = ((ri - pc_) ** 2).sum(1) <= ((ri - hc_) ** 2).sum(1)
        P[ri[toP, 0], ri[toP, 1], ri[toP, 2]] = True
        H[ri[~toP, 0], ri[~toP, 1], ri[~toP, 2]] = True
    P &= V; H &= V; H &= ~P
    np_, nh = int(P.sum()), int(H.sum())
    if np_ + nh == 0:
        return None
    # 信頼度: 片方に極端に偏る（門脈相で肝静脈が造影されない等）＝自動色分けは当てにならない。
    # ただし None にはせず、ラベルは返す＝手動再指定の対象を必ず用意する（先生要望 2026-07-21）。
    confident = min(np_, nh) >= 0.15 * (np_ + nh)
    return dict(V=V, P=P, H=H, ds=ds, confident=confident)


def _flood6(seed_vox, region):
    """region(bool) 内で seed_vox から 6近傍で連結する成分を返す（純numpy・反復膨張∩region）。"""
    z, y, x = seed_vox
    if not region[z, y, x]:
        return np.zeros_like(region)
    comp = np.zeros_like(region); comp[z, y, x] = True
    while True:
        grown = _dilate6(comp) & region
        if int(grown.sum()) == int(comp.sum()):
            return comp
        comp = grown


def vein_branch_at(lab, world_pt, sx, sy, dz, max_mm=8.0):
    """world 点(mm・full res)に最も近い血管ボクセルの **枝**（基準ラベルで連結する成分）を返す。
    返り値 dict(comp=枝マスク(z,y,x bool), is_portal=基準が門脈か, center=枝の重心mm) / 近くに無ければ None。
    訂正モードのハイライト＆ピッカーが使う（先生要望 2026-07-21：クリックで枝が浮き出る）。"""
    if not lab:
        return None
    P, H, V, ds = lab["P"], lab["H"], lab["V"], lab["ds"]
    fz, fy, fx = ds
    zz, yy, xx = np.where(V)
    if len(zz) == 0:
        return None
    vmm = np.column_stack([xx * sx * fx, yy * sy * fy, zz * dz * fz]).astype(np.float32)
    d2 = ((vmm - np.asarray(world_pt, float)) ** 2).sum(1)
    j = int(np.argmin(d2))
    if float(d2[j]) ** 0.5 > max_mm:                      # クリックが血管から遠い＝枝なし
        return None
    v = (int(zz[j]), int(yy[j]), int(xx[j]))
    is_p = bool(P[v])
    comp = _flood6(v, (P if is_p else H))
    if not comp.any():
        return None
    ci = np.argwhere(comp).mean(0)
    center = np.array([ci[2] * sx * fx, ci[1] * sy * fy, ci[0] * dz * fz], float)
    return dict(comp=comp, is_portal=is_p, center=center)


def apply_vein_overrides(lab, overrides, sx, sy, dz):
    """ラベルボリューム lab(dict V/P/H/ds) に、手動再指定(override)を **枝ごと** 適用して返す。
    override = [dict(pt=[x,y,z]mm, to="portal"|"hepatic"), …]。

    「枝」は **基準ラベル（override 前）で連結する同色の成分**で定義する。クリック点の枝を、
    指定した種類(to)へ確定する（同じ枝への複数指定は後勝ち）。to が無ければ従来どおりトグル。
    純numpy・連結成分は _flood6。"""
    if not lab or not overrides:
        return lab
    baseP, baseH, V, ds = lab["P"], lab["H"], lab["V"], lab["ds"]
    fz, fy, fx = ds
    zz, yy, xx = np.where(V)
    if len(zz) == 0:
        return lab
    vmm = np.column_stack([xx * sx * fx, yy * sy * fy, zz * dz * fz]).astype(np.float32)
    acts = {}                                             # 枝キー → (最終ラベル 'portal'|'hepatic', その枝マスク)
    for ov in overrides:
        pt = np.asarray(ov.get("pt"), float)
        if pt.shape != (3,):
            continue
        j = int(np.argmin(((vmm - pt) ** 2).sum(1)))
        v = (int(zz[j]), int(yy[j]), int(xx[j]))
        base_is_p = bool(baseP[v])
        if not base_is_p and not baseH[v]:
            continue
        comp = _flood6(v, (baseP if base_is_p else baseH))  # ★基準ラベルで連結する枝（固定）
        if not comp.any():
            continue
        key = tuple(np.argwhere(comp).min(0))            # 枝の代表（最小index）＝同じ枝は同じキー
        to = ov.get("to")
        if to not in ("portal", "hepatic"):              # to 無し＝トグル（旧データ互換）
            prev = acts.get(key)
            base = "portal" if base_is_p else "hepatic"
            cur = prev[0] if prev else base
            to = "hepatic" if cur == "portal" else "portal"
        acts[key] = (to, comp)
    P, H = baseP.copy(), baseH.copy()
    for to, comp in acts.values():
        if to == "portal":
            H &= ~comp; P |= comp
        else:
            P &= ~comp; H |= comp
    return dict(V=V, P=P & V, H=H & V, ds=ds, confident=lab.get("confident", True))


def _vein_pts(mask, sx, sy, dz, ds):
    """ラベルの bool マスク → 滑らかな mm 点群（本体の血管と同じ smooth_points）。"""
    fz, fy, fx = ds
    if mask is None or not mask.any():
        return None
    if _lv is not None:
        try:
            pts = _lv.smooth_points(mask, sx * fx, sy * fy, dz * fz)
            if pts is not None and len(pts):
                return pts
        except Exception:
            pass
    zi, yi, xi = np.where(mask)
    if len(zi) == 0:
        return None
    return np.column_stack([xi * sx * fx, yi * sy * fy, zi * dz * fz]).astype(np.float32)


def split_liver_vessels(masks, sx, sy, dz, ds=(2, 3, 3), max_iter=1200, overrides=None):
    """肝血管ツリーを門脈系/肝静脈系へ推定分離し、点群 (portal_extra_pts, hepatic_pts) を返す。
    overrides を渡すと枝ごとの手動再指定を反映（apply_vein_overrides）。分離不能なら (None, None)。
    ※単相CTでは完全分離は原理的に不可能＝あくまで推定。手動再指定で訂正できる。"""
    lab = _vein_labels(masks, sx, sy, dz, ds=ds, max_iter=max_iter)
    if lab is None:
        return None, None
    if overrides:
        lab = apply_vein_overrides(lab, overrides, sx, sy, dz)
    return _vein_pts(lab["P"], sx, sy, dz, lab["ds"]), _vein_pts(lab["H"], sx, sy, dz, lab["ds"])


def _tract_mask(masks):
    """スコープが通る管腔のゴーストマスク（術式プリセット TS_GHOST_ORGANS の和集合、既定=EUSの腸管）。無ければ None。"""
    if masks is None:
        return None
    names = getattr(preset, "TS_GHOST_ORGANS", ("esophagus", "stomach", "duodenum"))
    parts = [np.asarray(masks.get(n)) for n in names if masks.get(n) is not None]
    if not parts:
        return None
    m = parts[0].astype(bool).copy()
    for p in parts[1:]:
        m = m | p.astype(bool)
    return m


def _build_scene_fixed3(masks, sx, sy, dz, ds=(1, 3, 3), split_veins=True, show_organs=None,
                        vein_overrides=None, force_vein_split=False):
    """fixed3（tips）: マスク群 → 3D描画用 dict。
      liver  : render_ghost(mode='surface') で描ける packed dict（surf/nrm/center/spacing）
      ivc    : Nx3 mm 点群（灰で splat）
      portal : Nx3 mm 点群（青で splat）＝門脈本幹(＋split_veins時は門脈枝の推定分)
      hepatic: Nx3 mm 点群（ローズ）＝肝血管ツリー全体、または split_veins時は肝静脈系の推定分のみ
    """
    if masks is None:
        return None
    fz, fy, fx = ds
    scene = {}
    lm = masks.get("liver")
    if lm is not None and _lv is not None:
        m = lm[::fz, ::fy, ::fx]
        m = _lv.largest_component(m)                       # 遠く離れた誤検出ブロブを除去（最大連結成分＝ひと繋がりの肝臓）
        m = _lv._fill_inplane_holes(m)                    # 肝内血管の穴を埋めて滑らかな面に
        packed = _lv._pack(m, sx * fx, sy * fy, dz * fz)
        if packed is not None:
            packed["spacing"] = max(sx * fx, sy * fy, dz * fz)
            scene["liver"] = packed

    def pc(name, step=(1, 2, 2), smooth=True):
        """マスク → 3D描画用の mm 点群。

        smooth=True では点をボクセル中心ではなく **平滑化した面の上** に置く（liver.smooth_points）。
        生のボクセル中心を splat で描くと輪郭が格子に量子化されて階段状に見えるため
        （解析の細かさを粗くするほど目立つ）。動く量は1ボクセル未満で、位置はむしろ正確になる。
        """
        mm = masks.get(name)
        if mm is None:
            return None
        gz, gy, gx = step
        s = mm[::gz, ::gy, ::gx]
        if not s.any():
            return None
        if smooth and _lv is not None:
            try:
                pts = _lv.smooth_points(s, sx * gx, sy * gy, dz * gz)
                if pts is not None and len(pts):
                    return pts
            except Exception:                             # 平滑化に失敗しても描画は止めない
                pass
        zi, yi, xi = np.where(s)
        if len(zi) == 0:
            return None
        return np.column_stack([xi * sx * gx, yi * sy * gy, zi * dz * gz]).astype(np.float32)

    scene["ivc"] = pc("inferior_vena_cava")
    portal = pc("portal_vein_and_splenic_vein")
    hepatic_all = pc(HEPATIC, step=(1, 1, 1))            # 肝血管ツリー全体（細いので密に）
    portal_extra = hepatic_only = None
    lab = _vein_labels(masks, sx, sy, dz) if split_veins else None
    if lab is not None and vein_overrides:
        lab = apply_vein_overrides(lab, vein_overrides, sx, sy, dz)
    if lab is not None:
        scene["vein_lab"] = lab                          # 手動再指定(CTクリック)が枝を拾うためのラベル
        # 表示する条件: 信頼できる / 手動再指定がある / 訂正モード(force)。単相で偏っていても
        # 訂正モードなら暫定分離を見せて直せるようにする（先生要望 2026-07-21）。
        show_split = lab.get("confident") or bool(vein_overrides) or force_vein_split
        if show_split:
            portal_extra = _vein_pts(lab["P"], sx, sy, dz, lab["ds"])
            hepatic_only = _vein_pts(lab["H"], sx, sy, dz, lab["ds"])
    if hepatic_only is not None:                          # 推定分離: 青=門脈系 / ローズ=肝静脈系
        scene["portal"] = np.vstack([a for a in (portal, portal_extra) if a is not None])             if (portal is not None or portal_extra is not None) else portal_extra
        scene["hepatic"] = hepatic_only
        scene["veins_split"] = True                      # 分離済み（UI表示用フラグ）
    else:
        # 分離不能/OFF: 門脈相では肝内血管ツリーの大半が門脈枝なので、**門脈系＝青**にまとめる
        # （以前は全血管をローズ＝肝静脈色にしていたため「門脈なのにピンク」に見えていた）。
        # ローズ（肝静脈）は手描きの肝静脈と、分離成功時の推定肝静脈だけに使う。
        scene["portal"] = np.vstack([a for a in (portal, hepatic_all) if a is not None]) \
            if (portal is not None or hepatic_all is not None) else None
        scene["hepatic"] = None
        scene["veins_split"] = False
    # 追加の色分け構造（回避目標の胆嚢・結腸、方位ランドマークの大動脈、肝腫瘍 …）。
    # Pane3D は fixed3 の 3 構造と organs 辞書を同時に描ける（Phase 3b の上位集合設計）。
    # show_organs=None は既定（preset の TS_EXTRA_ORGANS）。ユーザーがチェックリストで選んだ場合は
    # その集合を使う＝**キャッシュ済みマスクから切り出すだけなので AI 再実行は不要**。
    organs = {}
    for name in (EXTRA_ORGANS if show_organs is None else show_organs):
        pts = pc(name)
        if pts is not None:
            organs[name] = pts
    if organs:
        scene["organs"] = organs
    return scene or None


def _build_scene_organs(masks, sx, sy, dz, ds=(1, 3, 3), show_organs=None):
    """organs（eus）: マスク群 → 3D描画用 dict。
      liver  : 腸管(食道∪胃∪十二指腸)ゴーストの packed dict（render_ghost surface で描く）
      organs : {構造名: Nx3 mm点群}  表示ONの構造だけ。色は描画側が ORGAN_COLORS で引く。
    show_organs=None のときは DEFAULT_SHOW を表示。"""
    if masks is None:
        return None
    fz, fy, fx = ds
    scene = {}
    tm = _tract_mask(masks)                            # 腸管(食道∪胃∪十二指腸)を3Dゴースト表示
    if tm is not None and _lv is not None:
        m = tm[::fz, ::fy, ::fx]
        m = _lv._fill_inplane_holes(m)                    # 面内の穴を埋めて滑らかに（腸管は多連結なのでlargest_componentはしない）
        packed = _lv._pack(m, sx * fx, sy * fy, dz * fz)
        if packed is not None:
            packed["spacing"] = max(sx * fx, sy * fy, dz * fz)
            scene["liver"] = packed                        # ← Pane3Dの腸管ゴースト枠（属性名は歴史的に ts_liver）

    def pc(name, step=(1, 2, 2)):
        mm = masks.get(name)
        if mm is None:
            return None
        gz, gy, gx = step
        s = np.asarray(mm)[::gz, ::gy, ::gx]
        zi, yi, xi = np.where(s)
        if len(zi) == 0:
            return None
        return np.column_stack([xi * sx * gx, yi * sy * gy, zi * dz * gz]).astype(np.float32)

    # 表示ONの構造だけ点群化（色分けは描画側で ORGAN_COLORS を参照）。
    show = set(show_organs) if show_organs is not None else set(DEFAULT_SHOW)
    organs = {}
    for name in ORGANS:
        if name in show:
            pts = pc(name)
            if pts is not None:
                organs[name] = pts
    scene["organs"] = organs
    return scene or None


def build_scene(masks, sx, sy, dz, ds=(1, 3, 3), split_veins=True, show_organs=None,
                vein_overrides=None, force_vein_split=False, **_ignore):
    """マスク群 → 3D描画用 dict。流儀は preset.TS_SCENE_MODE で切替（引数は両流儀の和集合＝呼び側不変）。"""
    if preset.TS_SCENE_MODE == "organs":
        return _build_scene_organs(masks, sx, sy, dz, ds=ds, show_organs=show_organs)
    return _build_scene_fixed3(masks, sx, sy, dz, ds=ds, split_veins=split_veins,
                               show_organs=show_organs, vein_overrides=vein_overrides,
                               force_vein_split=force_vein_split)


def _organ_centerline(m, prev=None, sx=1.0, sy=1.0, dz=1.0, max_step_mm=26.0):
    """1臓器マスク(bool z,y,x)の管の中心線 [z,row,col] Nx3 を返す。
    ★各z・各連結成分の重心を *ノード* にして（C字ループは同一zに複数ノード＝折返しも表現）、口側ノードから
      貪欲に最近傍ノードを辿る（訪問済み除外・ステップ上限）。これで十二指腸を球部で止めず最後まで辿り、
      かつC字ループ中心(膵頭部)を通らない（スライス全体の単純重心の弱点を両方とも解消・先生指示）。"""
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None
    scale = np.array([dz, sy, sx])
    nodes = []
    for z in np.where(m.any(axis=(1, 2)))[0]:
        sl = m[z]
        if ndimage is not None:
            lab, n = ndimage.label(sl)
            if n == 0:
                continue
            for (r, cc) in ndimage.center_of_mass(sl, lab, range(1, n + 1)):
                nodes.append([float(z), float(r), float(cc)])
        else:
            yi, xi = np.where(sl); nodes.append([float(z), float(yi.mean()), float(xi.mean())])
    if not nodes:
        return None
    nodes = np.array(nodes, float)
    P = nodes * scale                                                     # world mm
    N = len(P)
    if N == 1:
        return nodes
    import heapq
    # 隣接グラフ：max_step 内のノード同士を結ぶ（管に沿った辺）
    adj = [[] for _ in range(N)]
    for i in range(N):
        d = np.linalg.norm(P - P[i], axis=1)
        for j in np.where((d > 0) & (d <= max_step_mm))[0]:
            adj[i].append((int(j), float(d[j])))

    def dijkstra(src):
        dist = np.full(N, np.inf); dist[src] = 0.0; pr = [-1] * N
        pq = [(0.0, src)]
        while pq:
            dd, u = heapq.heappop(pq)
            if dd > dist[u]:
                continue
            for v, w in adj[u]:
                nd = dd + w
                if nd < dist[v]:
                    dist[v] = nd; pr[v] = u; heapq.heappush(pq, (nd, v))
        return dist, pr

    # 端点を double-Dijkstra で：任意点→最遠A→最遠B、A→B の測地線が管の中心線（U字も端まで）
    s0 = int(np.argmin(np.linalg.norm((nodes - np.asarray(prev, float)) * scale, axis=1))) if prev is not None \
        else int(np.argmax(nodes[:, 0]))
    d0, _ = dijkstra(s0); A = int(np.argmax(np.where(np.isfinite(d0), d0, -1)))
    dA, prA = dijkstra(A); B = int(np.argmax(np.where(np.isfinite(dA), dA, -1)))
    idx = []; u = B
    while u != -1:
        idx.append(u); u = prA[u]
    idx.reverse()
    path = nodes[idx]
    # 向き：口側(prev または 高z)に近い端を先頭にする
    if prev is not None:
        ref = np.asarray(prev, float)
        if np.linalg.norm((path[0] - ref) * scale) > np.linalg.norm((path[-1] - ref) * scale):
            path = path[::-1]
    elif path[0, 0] < path[-1, 0]:
        path = path[::-1]
    return np.array(path, float)


def _resample_polyline(P, step_mm, sx, sy, dz):
    """[z,row,col] 列 P を world 弧長で step_mm 間隔に再標本化（[z,row,col] のまま返す）。"""
    if len(P) < 2:
        return P
    scale = np.array([dz, sy, sx])                        # [z,row,col]→mm 換算（弧長用）
    W = P * scale
    seg = np.linalg.norm(np.diff(W, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)]); L = float(s[-1])
    if L < 1e-6:
        return P
    n = max(2, int(L / step_mm) + 1)
    su = np.linspace(0.0, L, n)
    return np.column_stack([np.interp(su, s, P[:, k]) for k in range(3)])


def _smooth_polyline(P, win=3):
    """移動平均で滑らかに（両端は保持）。"""
    if len(P) < win or win < 3:
        return P
    k = win // 2
    out = P.copy()
    for i in range(k, len(P) - k):
        out[i] = P[i - k:i + k + 1].mean(0)
    return out


def _tract_centerline(masks, sx, sy, dz, step_mm=8.0):
    """食道→胃→十二指腸 を *臓器ごと* に中心線化し、口側(高z)→遠位 に連結・平滑化した [z,row,col] 列。
    臓器を別々に処理するので、食道と十二指腸が同一zに重なっても中心が混ざらない（旧・全体重心方式の弱点を解消）。
    連結は解剖順（食道→胃→十二指腸）で、各セグメントは前の終点に近い端が先頭になるよう必要なら反転する。"""
    segs = []
    prev = None
    for name in ("esophagus", "stomach", "duodenum"):     # 口側→遠位の解剖順
        m = masks.get(name)
        if m is None:
            continue
        m = np.asarray(m).astype(bool)
        if int(m.sum()) < 30:
            continue
        cl = _organ_centerline(m, prev=prev, sx=sx, sy=sy, dz=dz)   # 前臓器の終点から連続して管を辿る
        if cl is None or len(cl) < 2:
            continue
        segs.append(cl)
        prev = cl[-1]
    if not segs:
        return None
    path = np.vstack(segs)
    if len(path) < 2:
        return None
    path = _resample_polyline(path, step_mm, sx, sy, dz)
    for _ in range(3):                                    # 数回の移動平均で junction(幽門など)の折れを柔らげる
        path = _smooth_polyline(path, win=5)
    return path


def eus_autoplan(masks, sx, sy, dz):
    """AIで腸管(食道→胃→十二指腸)を認識し、その *中央線* を内視鏡トラクトにする（AI自動配置）。
    ・臓器ごとに中心線を作り口側→遠位に連結・平滑化＝綺麗なトラクト（先生指示 2026-07-16）。
    ・膵重心に最も近いトラクト点をプローブ位置(遠位)に、膵重心を標的に。
    返り値 dict(path=[[z,row,col]... 口→遠位], probe_frac(0-1), target=world[x,y,z]) or None。"""
    cl = _tract_centerline(masks, sx, sy, dz)
    if cl is not None and len(cl) >= 2:
        path = [[float(p[0]), float(p[1]), float(p[2])] for p in cl]
    else:
        # フォールバック: 臓器ラベルが揃わないとき旧・腸管全体の各zスライス重心
        tract = _tract_mask(masks)
        if tract is None or int(tract.sum()) < 100:
            return None
        tract = np.asarray(tract).astype(bool)
        pts = []
        for z in range(tract.shape[0] - 1, -1, -1):       # 高z(口/食道)→低z(肛門側)
            if tract[z].any():
                yi, xi = np.where(tract[z])
                pts.append([float(z), float(yi.mean()), float(xi.mean())])
        if len(pts) < 2:
            return None
        stepz = max(1, int(round(10.0 / dz)))
        path = pts[::stepz]
        if path[-1] != pts[-1]:
            path.append(pts[-1])
    pa = masks.get("pancreas")
    if pa is not None and int(np.asarray(pa).sum()) >= 50:
        pc = np.argwhere(np.asarray(pa)).mean(0)          # 膵重心 [z,y,x]
        pcw = np.array([pc[2] * sx, pc[1] * sy, pc[0] * dz])
        pw = np.array([[p[2] * sx, p[1] * sy, p[0] * dz] for p in path])
        seg = np.linalg.norm(np.diff(pw, axis=0), axis=1)
        s_arc = np.concatenate([[0.0], np.cumsum(seg)]); L = float(s_arc[-1])
        idx = int(np.argmin(np.linalg.norm(pw - pcw[None, :], axis=1)))
        frac = float(s_arc[idx] / L) if L > 1e-6 else 0.5
        target = [float(pc[2] * sx), float(pc[1] * sy), float(pc[0] * dz)]
    else:
        frac = 0.7; target = None
    return dict(path=path, probe_frac=frac, target=target)
