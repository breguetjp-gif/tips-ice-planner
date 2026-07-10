"""アプリ自己更新（macOS / Windows）。

方針: 配布フォルダ（build_release/build_windows_ci が毎回更新する `~/Desktop/TIPS ICE Planner 配布`）か、
任意のインターネットURL（version.json の "url"=zip直リンク）から新版を取得し、
実行中のアプリ本体をヘルパースクリプトで入れ替えて再起動する（Sparkle と同方式）。
mac=.app バンドル一式 ／ Windows=exeを含むonedirフォルダ一式、をそれぞれの流儀で入れ替える。

OS非依存の検出/取得ロジックと、UIは分離（main.py から呼ぶ）。
"""
from __future__ import annotations
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

IS_WIN = sys.platform == "win32"

# 配布フォルダ（ローカル更新元）。$HOME 配下のみ＝個人特定パスを埋め込まない（git安全）。
LOCAL_DIRS = ["~/Desktop/TIPS ICE Planner 配布"]
APP_NAME = "TIPS ICE Planner.exe" if IS_WIN else "TIPS ICE Planner.app"
# build_release(mac)/build_windows_ci(Win) がそれぞれ毎回更新する配布物（=更新元の実体）
DIST_ZIP = "TIPS-ICE-Planner-Windows.zip" if IS_WIN else "TIPS-ICE-Planner-Mac.zip"


def ver_tuple(s):
    return tuple(int(x) for x in str(s or "").split(".") if x.isdigit())


def current_app_bundle():
    """実行中のアプリ本体の絶対パス（凍結ビルド時）。開発(ソース)実行時は None。
      mac: .app バンドル／Windows: exe を含む onedir フォルダ（`_internal` 等と同階層）。"""
    if IS_WIN:
        if not getattr(sys, "frozen", False):          # PyInstaller未経由=ソース実行
            return None
        return os.path.dirname(os.path.abspath(sys.executable))
    exe = os.path.abspath(sys.executable)
    marker = ".app" + os.sep + "Contents" + os.sep + "MacOS" + os.sep
    i = exe.find(marker)
    if i == -1:
        return None
    return exe[:i + len(".app")]


def is_translocated(path):
    """macOSの隔離実行(App Translocation)で読み取り専用の一時領域から起動されているか。
      この状態だと自己置換しても本体が変わらず更新ループになるため、自動更新を抑止する。"""
    return "/AppTranslocation/" in (path or "")


def find_update(current_version, update_url=None, timeout=5):
    """新版を探す。現在より新しく、かつ最も新しいものを返す。
    返り値 dict(version, source='local'|'url', notes, app_path|zip_url) または None。"""
    cur = ver_tuple(current_version)
    best = None                                  # (ver_tuple, info)

    def consider(ver, info):
        nonlocal best
        vt = ver_tuple(ver)
        if vt > cur and (best is None or vt > best[0]):
            best = (vt, info)

    for d in LOCAL_DIRS:                          # ① ローカル配布フォルダ（ネット不要）
        d = os.path.expanduser(d)
        vj = os.path.join(d, "version.json")
        zp = os.path.join(d, DIST_ZIP)            # 実体は zip（build_release/build_windows_ciが毎回更新。展開済みフォルダは更新されない）
        if not (os.path.exists(vj) and os.path.exists(zp)):
            continue
        try:
            data = json.load(open(vj, encoding="utf-8"))
        except Exception:
            continue
        consider(data.get("version", ""),
                 dict(version=str(data.get("version", "")).strip(), source="local",
                      notes=data.get("notes", ""), zip_path=zp))

    if update_url:                               # ② インターネット（version.json の url=zip直リンク）
        try:
            with urllib.request.urlopen(update_url, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            zurl = (data.get("url") or "").strip()
            if zurl:
                consider(data.get("version", ""),
                         dict(version=str(data.get("version", "")).strip(), source="url",
                              notes=data.get("notes", ""), zip_url=zurl))
        except Exception:
            pass

    return best[1] if best else None


def stage_new_bundle(info, progress=None):
    """新版のアプリ本体をローカル一時領域に用意し、その絶対パスを返す
    （mac=.app バンドル／Windows=exeを含むonedirフォルダ）。
    local=配布フォルダのzipを展開／url=zipをDLして展開。常に最新のzipを実体とする。"""
    tmp = tempfile.mkdtemp(prefix="tips_update_")
    if info.get("source") == "local":
        zpath = info["zip_path"]
        if progress:
            progress(0, 1)
    else:                                        # url: zip をダウンロード
        zpath = os.path.join(tmp, "update.zip")

        def _hook(blocks, bs, total):
            if progress and total > 0:
                progress(min(blocks * bs, total), total)

        urllib.request.urlretrieve(info["zip_url"], zpath, _hook)
    if IS_WIN:
        # ditto は mac 専用。zipfile は symlink/xattr を保持しないため
        # mac 側（.app、フレームワーク内にsymlinkあり）は引き続き ditto を使う。
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
    else:
        subprocess.check_call(["/usr/bin/ditto", "-x", "-k", zpath, tmp])   # zip 展開
    hits = glob.glob(os.path.join(tmp, "**", APP_NAME), recursive=True)
    if not hits:
        raise RuntimeError("更新ファイルにアプリが見つかりませんでした。")
    if progress:
        progress(1, 1)
    return os.path.dirname(hits[0]) if IS_WIN else hits[0]   # Win=exeの親フォルダごと入替


def update_log_path():
    """更新処理の記録先（毎回追記）。うまくいかない時に原因を追えるように。"""
    try:
        import catalog
        d = os.path.join(catalog.app_data_dir(), "logs"); os.makedirs(d, exist_ok=True)
        return os.path.join(d, "update.log")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "tips_update.log")


def apply_update_and_relaunch(new_app, target_app):
    """new_app で target_app（実行中バンドル）を置き換えて再起動。
    自プロセス終了を待ってから swap するヘルパーを detached で起動する。呼び出し後にアプリを終了すること。
    以前は `set -e` + `[ -d "$TGT" ] && mv ...` の組み合わせで、条件を満たさないと
    ログを残さず無言でスクリプト全体が中断する不具合があった（先生報告：「OKを押してもバージョンが変わらない」）。
    各手順を明示的にチェックしてログに残し、失敗しても最後に必ず(旧|新)どちらかのアプリを開き直す。
    Windows では bash/ditto/xattr/open が無いため、同じロジックを PowerShell スクリプトで実装する
    （旧: current_app_bundle()が常にNoneを返し自己更新自体が不可能だった不具合の修正）。"""
    pid = os.getpid()
    log = update_log_path()
    if IS_WIN:
        _apply_update_and_relaunch_win(new_app, target_app, log, pid)
        return
    script = (
        "#!/bin/bash\n"
        'NEW="%s"\n'
        'TGT="%s"\n'
        'LOG="%s"\n'
        '{\n'
        '  echo "=== update begin $(date) pid=%d ==="\n'
        '  echo "NEW=$NEW"; echo "TGT=$TGT"\n'
        "  for i in $(seq 1 150); do kill -0 %d 2>/dev/null || break; sleep 0.1; done\n"
        "  sleep 0.3\n"
        '  if [ ! -d "$NEW" ]; then echo "FAIL: staged app not found: $NEW"; open "$TGT" 2>/dev/null; exit 1; fi\n'
        '  rm -rf "$TGT.new" "$TGT.old"\n'
        '  if ! /usr/bin/ditto "$NEW" "$TGT.new"; then echo "FAIL: ditto exit=$?"; open "$TGT" 2>/dev/null; exit 1; fi\n'
        '  xattr -cr "$TGT.new" 2>/dev/null\n'
        '  if [ -d "$TGT" ]; then\n'
        '    if ! mv "$TGT" "$TGT.old"; then echo "FAIL: mv old exit=$?"; open "$TGT" 2>/dev/null; exit 1; fi\n'
        '  fi\n'
        '  if ! mv "$TGT.new" "$TGT"; then\n'
        '    echo "FAIL: mv new exit=$?, rolling back"\n'
        '    [ -d "$TGT.old" ] && mv "$TGT.old" "$TGT"\n'
        '    open "$TGT" 2>/dev/null; exit 1\n'
        '  fi\n'
        '  rm -rf "$TGT.old"\n'
        '  echo "OK: swapped to new version"\n'
        '  open "$TGT"\n'
        '} >> "$LOG" 2>&1\n'
    ) % (new_app, target_app, log, pid, pid)
    d = tempfile.mkdtemp(prefix="tips_swap_")
    sh = os.path.join(d, "swap.sh")
    with open(sh, "w") as f:
        f.write(script)
    os.chmod(sh, 0o755)
    subprocess.Popen(["/bin/bash", sh], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _apply_update_and_relaunch_win(new_app, target_app, log, pid):
    """Windows版のswapヘルパー。bashの代わりにPowerShellスクリプトを生成しdetachedで起動する。
    ロジックはmac版と同一（自プロセスの終了待ち→ステージ先確認→コピー→旧退避→新配置→
    失敗時ロールバック→どのパスでも最後に必ずexeを開き直す）。"""
    exe_name = os.path.basename(APP_NAME)                    # "TIPS ICE Planner.exe"
    script = (
        '$New = "%s"\n'
        '$Tgt = "%s"\n'
        '$Log = "%s"\n'
        '$ProcId = %d\n'
        'function Log($m) { Add-Content -LiteralPath $Log -Value $m }\n'
        'function Relaunch {\n'
        '  $exe = Join-Path $Tgt "%s"\n'
        '  if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe | Out-Null }\n'
        '}\n'
        'Log "=== update begin $(Get-Date) pid=$ProcId ==="\n'
        'Log "NEW=$New"\n'
        'Log "TGT=$Tgt"\n'
        'while (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 200 }\n'
        'Start-Sleep -Milliseconds 500\n'
        'if (-not (Test-Path -LiteralPath $New)) {\n'
        '  Log "FAIL: staged app not found: $New"; Relaunch; exit 1\n'
        '}\n'
        'Remove-Item -LiteralPath "${Tgt}.new" -Recurse -Force -ErrorAction SilentlyContinue\n'
        'Remove-Item -LiteralPath "${Tgt}.old" -Recurse -Force -ErrorAction SilentlyContinue\n'
        'try {\n'
        '  Copy-Item -LiteralPath $New -Destination "${Tgt}.new" -Recurse -Force -ErrorAction Stop\n'
        '} catch {\n'
        '  Log "FAIL: copy exit: $_"; Relaunch; exit 1\n'
        '}\n'
        'if (Test-Path -LiteralPath $Tgt) {\n'
        '  try {\n'
        '    Move-Item -LiteralPath $Tgt -Destination "${Tgt}.old" -Force -ErrorAction Stop\n'
        '  } catch {\n'
        '    Log "FAIL: move old exit: $_"; Relaunch; exit 1\n'
        '  }\n'
        '}\n'
        'try {\n'
        '  Move-Item -LiteralPath "${Tgt}.new" -Destination $Tgt -Force -ErrorAction Stop\n'
        '} catch {\n'
        '  Log "FAIL: move new exit: $_, rolling back"\n'
        '  if (Test-Path -LiteralPath "${Tgt}.old") {\n'
        '    Move-Item -LiteralPath "${Tgt}.old" -Destination $Tgt -Force -ErrorAction SilentlyContinue\n'
        '  }\n'
        '  Relaunch; exit 1\n'
        '}\n'
        'Remove-Item -LiteralPath "${Tgt}.old" -Recurse -Force -ErrorAction SilentlyContinue\n'
        'Log "OK: swapped to new version"\n'
        'Relaunch\n'
    ) % (new_app, target_app, log, pid, exe_name)
    d = tempfile.mkdtemp(prefix="tips_swap_")
    ps1 = os.path.join(d, "swap.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write(script)
    # CREATE_NO_WINDOW/DETACHED_PROCESS は win32ビルドのsubprocessにしか無い属性。
    # 実機(Windows)では常に存在するが、テスト等でIS_WINを強制した非Windows環境でも
    # 属性エラーで落ちないようgetattrで安全にフォールバックする。
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1],
        creationflags=creationflags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, cwd=d)   # cwdをtarget_app外に固定＝Windowsは「誰かのcwd」なフォルダのrenameを拒否するため
