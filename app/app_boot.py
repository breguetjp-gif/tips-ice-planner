"""起動部の正本（engine/core/app_boot.py。各アプリへは sync_core.py で物理コピー配布）。

macOS の FileOpen / URLスキーム受け、メニューバーのアプリ名修正、QApplication の起動を担う。
アプリごとの違いは preset.py（URL_SCHEME / APP_DISPLAY_NAME）から読む。
各アプリの main.py は `app_boot.run(MainWindow)` を呼ぶだけ。
"""
from __future__ import annotations
import os
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QUrl, QUrlQuery, QTimer

import preset


def path_from_open_event(qurl, qfile):
    """macOSの「開く」イベント(QFileOpenEvent)からローカルパスを取り出す。
    - <preset.URL_SCHEME>://open?dir=<urlencoded path>  → dir/path/file クエリを返す
    - ローカルファイル/フォルダの直接オープン             → そのパスを返す
    純粋関数（テスト容易）。受け取れなければ None。"""
    if qurl is not None and isinstance(qurl, QUrl) and qurl.scheme() == preset.URL_SCHEME:
        q = QUrlQuery(qurl)
        for key in ("dir", "path", "file", "folder"):
            v = q.queryItemValue(key, QUrl.ComponentFormattingOption.FullyDecoded)
            if v:
                return v
        return None
    if qfile:
        return qfile
    return None


class _OpenDispatcher:
    """「開く」要求のバッファ＆配送（QApplication非依存＝テスト可能）。
    ハンドラ設定前に届いた要求は溜め、設定時にまとめて流す。"""
    def __init__(self):
        self._handler = None
        self._buffer = []

    def dispatch(self, path):
        if not path:
            return
        if self._handler:
            self._handler(path)
        else:
            self._buffer.append(path)

    def set_handler(self, fn):
        self._handler = fn
        pending, self._buffer = self._buffer, []
        for p in pending:
            fn(p)


class EchoApp(QApplication):
    """FileOpen(=macOSの開く/URLスキーム)を捕まえて、用意ができ次第ハンドラへ渡す。
    起動直後(ウィンドウ未生成)に届いたイベントは _OpenDispatcher がバッファして後で流す。
    （旧名 TIPSApp。共通化に伴い術式に依らない名前へ変更）"""
    def __init__(self, argv):
        super().__init__(argv)
        self._open = _OpenDispatcher()

    def set_open_handler(self, fn):
        self._open.set_handler(fn)

    def event(self, e):
        if e.type() == QEvent.Type.FileOpen:
            url = e.url() if hasattr(e, "url") else None
            self._open.dispatch(path_from_open_event(url, e.file()))
            return True
        return super().event(e)


def _fix_mac_app_menu_name(name):
    """macOSメニューバー先頭（Appleマーク横の太字アプリ名）を name にする。
    .command で素の python を起動すると 'Python' と表示される問題対策（先生要望 2026-07-17）。
    QApplication 生成前に NSBundle の CFBundleName を書き換える。pyobjc 未導入環境では黙って無視。"""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
        b = NSBundle.mainBundle()
        if b is None:
            return
        info = b.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
    except Exception:
        pass


def run(MainWindowClass):
    """アプリを起動する（各アプリの main() 本体）。アプリ名・スキームは preset から。"""
    _fix_mac_app_menu_name(preset.APP_DISPLAY_NAME)   # メニューバー先頭 'Python'→アプリ名（QApplication前に必須）
    app = EchoApp(sys.argv)
    # 設定/カタログの保存先(QStandardPaths.AppDataLocation)はQCoreApplication.applicationName()に依存し、
    # 未設定だと実行環境(凍結アプリ/素のpython等)でexe名から暗黙に決まり、環境によってブレうる。
    # 明示指定して固定＝配布アプリの実際の保存先(~/Library/Application Support/<アプリ名>/…)と
    # 完全一致させ、更新のたびに寄付回答等の設定が読めなくなる事故を防ぐ。
    app.setApplicationName(preset.APP_DISPLAY_NAME)
    app.setStyle("Fusion")          # macOSネイティブはQSSのボタン背景を無視→Fusionで確実に描画（選択中ボタンが見える）
    if os.environ.get("TIPS_SELFTEST"):   # 凍結ビルド検証用：全モジュール/ネイティブlibの読込確認
        import dicom_io, catalog, database_view, bg, tips_core  # noqa
        print("SELFTEST OK pxmm=%s pydicom=%s" % (tips_core.PXMM, dicom_io.pydicom is not None))
        return
    from PySide6.QtGui import QIcon
    _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))   # 凍結ビルドにも対応
    _ic = os.path.join(_base, "icon_1024.png")
    if os.path.exists(_ic):
        app.setWindowIcon(QIcon(_ic))   # Dock/ウィンドウのアイコン
    win = MainWindowClass(); win.show()
    app.set_open_handler(win.open_external_path)             # Mieleプラグイン等からの「開く」を受け付ける
    for a in sys.argv[1:]:                                   # 新規起動時に渡されたフォルダ/ファイルも開く
        if not a.startswith("-") and os.path.exists(a):
            win.open_external_path(a); break
    # 起動時ダイアログ（免責→更新確認→今日のヒント→寄付）は、本体ウィンドウが画面に配置された後に
    # アプリの上へ重ねて出す。show()直後に同期実行すると本体配置前に画面中央へ浮くため、イベントループ
    # 開始後に発火する QTimer.singleShot 経由で遅延する（免責はWindowModal＝親ウィンドウに紐づく）。
    QTimer.singleShot(0, win._run_startup_prompts)
    sys.exit(app.exec())
