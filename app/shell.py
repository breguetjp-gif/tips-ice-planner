"""MainWindow 共通の殻（正本 engine/core/shell.py。各アプリへは sync_core.py で物理コピー配布）。

Phase 3c-1（2026-07-18）: tips-planner / eus-planner の MainWindow で**一字一句同一**のメソッドだけを
ShellWindow（QMainWindow 派生）に持ち上げた。各アプリの MainWindow は ShellWindow を継承し、
__init__・UI 構築・術式固有のメソッドを持つ（インスタンス属性の初期化はすべてアプリ側 __init__）。
ここのメソッドは self 経由でアプリ側の属性/メソッドに触るため、単体では動かない＝殻。

編集ルール: このファイルだけを編集し `python3 engine/tools/sync_core.py` で配布（アプリ側コピー直編集禁止）。
"""
from __future__ import annotations
import os
import sys
import numpy as np

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QSlider, QPushButton, QScrollBar,
    QFileDialog, QHBoxLayout, QVBoxLayout, QGridLayout, QSizePolicy, QFrame, QSplitter,
    QStackedWidget, QButtonGroup, QMessageBox, QDialog, QTextBrowser, QMenu, QCheckBox)
from PySide6.QtGui import (QImage, QPainter, QColor, QPen, QPolygonF, QBrush, QFont, QPixmap,
                           QDesktopServices, QNativeGestureEvent, QPainterPath, QFontMetrics, QKeySequence)
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, Signal, QUrl, QEvent, QUrlQuery, QTimer, QSettings

import tips_core as core
from tips_core import liver as liver_core
import dicom_io
import i18n
from i18n import L
import settings_store
from handle_control import HandleControl, SurfaceProbeControl
from preset import (URL_SCHEME, GITHUB_REPO, SPONSORS_URL, AUTHOR_LINE,
                    FEEDBACK_FORM_JA, FEEDBACK_FORM_EN, TIPS_EN_JA)
from panes import (TERRA, CYAN, GREENC, REDC, AMBER, NEEDLE_COL, ACTIVE, INACTIVE_FRAME,
                   STYLE, SS_ON, SS_OFF, _roll_xy, _log_click, _frame, _sep,
                   GestureBar, CannulaHubWidget, ImagePane, PaneCell, QuadPanes)
from pane3d import Pane3D


class ShellWindow(QMainWindow):
    """両術式共通のメソッド群。属性は各アプリの MainWindow.__init__ が用意する。"""

    def _slider(self, lo, hi, val, cb, w=130):
        s = QSlider(Qt.Horizontal); s.setMinimum(lo); s.setMaximum(hi); s.setValue(val)
        s.setFixedWidth(w); s.valueChanged.connect(cb); return s

    def _reg(self, w, en, ja):
        """言語切替で貼り替えるテキストを登録（QPushButton/QLabel/QAction 共通）。"""
        self._i18n.append((w, en, ja)); w.setText(L(en, ja)); return w

    def _btn(self, text, cb, checkable=False, ja=None):
        b = QPushButton(); b.setCheckable(checkable); b.clicked.connect(cb)
        if ja is None:
            b.setText(text)
        else:
            self._reg(b, text, ja)
        return b

    def _acc(self, text, ja=None):
        l = QLabel(); l.setStyleSheet("color:#F08F69;")
        if ja is None:
            l.setText(text)
        else:
            self._reg(l, text, ja)
        return l

    def _lbl(self, en, ja):
        return self._reg(QLabel(), en, ja)

    def _current_patient_label(self):
        """今開いている患者の表示名（保存/復元ボタンのツールチップに使う）。未取得ならNone。"""
        if not self.current_study_uid:
            return None
        for st in self.catalog.studies():
            if st["study_uid"] == self.current_study_uid:
                return st.get("patient_name") or st.get("patient_id") or None
        return None

    def _refresh_toggles(self):
        for b, on in ((self.step1Btn, self.step == 0), (self.step2Btn, self.step == 1),
                      (self.entryBtn, self.ptMode == 0), (self.targetBtn, self.ptMode == 1),
                      (self.aimBtn, self.aimMode),
                      (self.iceBtn, self.viewMode == "ice"), (self.surfBtn, self.viewMode == "surface"),
                      (self.plotBtn, self.predict)):
            b.setStyleSheet(SS_ON if on else SS_OFF)
        if hasattr(self, "actInsFem"):                       # 挿入方向は設定メニューのチェック状態で表示
            self.actInsFem.setChecked(self.tipHighZ); self.actInsJug.setChecked(not self.tipHighZ)

    def _activate(self, pane):
        """マウスが入った画面をアクティブ（太枠）にし、他を非アクティブにする。"""
        for pn in self._panes:
            on = (pn is pane)
            if pn.active != on:
                pn.active = on; pn.update()

    # ---------- 上部メニュー（FAQ / 作成者 / Donation） ----------
    def _open_manual(self):
        """使い方説明書(PDF)を今のUI言語に合わせてOSの既定ビューアで開く（同梱リソース／凍結ビルド両対応）。"""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        name = "manual_ja.pdf" if i18n.lang() == "ja" else "manual_en.pdf"
        path = os.path.join(base, "docs", name)
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, L("User manual", "使い方説明書"),
                L("The manual PDF was not found in this build.", "この配布物には説明書PDFが同梱されていません。"))

    def _toggle_lang(self):
        """UI言語（英語⇄日本語）をワンボタン切替。選択は保存され、次回起動時も維持される。"""
        i18n.toggle()
        settings_store.store().setValue("ui_lang", i18n.lang())
        self._apply_language()

    def _apply_update(self, new_app, target, version):
        import updater
        try:
            updater.apply_update_and_relaunch(new_app, target)
        except Exception as ex:
            QMessageBox.warning(self, L("Update failed", "更新失敗"),
                L(f"Could not apply the update.\n{ex}", f"更新の適用に失敗しました。\n{ex}")); return
        QMessageBox.information(self, L("Updating", "更新中"),
            L(f"Applying v{version}.\nPress OK — the app will quit and relaunch automatically on the new version.",
              f"v{version} を適用します。\nOK を押すとアプリが終了し、新しいバージョンで自動的に再起動します。"))
        self._updating = True                                # closeEvent でのワーカー待ちを通常どおり行う
        QApplication.quit()

    def _info_dialog(self, title, html, open_url=None):
        dlg = QDialog(self); dlg.setWindowTitle(title); dlg.resize(640, 560)
        v = QVBoxLayout(dlg)
        tb = QTextBrowser(); tb.setOpenExternalLinks(True); tb.setHtml(html); v.addWidget(tb)
        row = QHBoxLayout(); row.addStretch(1)
        if open_url:
            b = QPushButton(L("Open in browser", "ブラウザで開く"))
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(open_url))); row.addWidget(b)
        c = QPushButton(L("Close", "閉じる")); c.clicked.connect(dlg.accept); row.addWidget(c)
        v.addLayout(row); dlg.exec()

    def _donation_url(self):
        """開発支援の窓口URL（言語別）。英語＝GitHub Sponsors／日本語＝note（先生指示 2026-07-21・tips）。
        術式によって日本語窓口(SPONSORS_URL_JA)を定義していない場合は SPONSORS_URL にフォールバック。"""
        import preset
        ja = getattr(preset, "SPONSORS_URL_JA", None)
        return ja if (i18n.lang() == "ja" and ja) else preset.SPONSORS_URL

    def _qr_html(self, size=160):
        """開発支援ページのQRコード（言語別・同梱リソース）をHTMLの<img>として埋め込む。無ければ空文字。
        日本語＝note の QR（note_qr.png）／英語＝GitHub Sponsors の QR（gh_qr.png）。"""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        name = "note_qr.png" if i18n.lang() == "ja" else "gh_qr.png"
        qr_path = os.path.join(base, "docs", name)
        if not os.path.exists(qr_path):
            return ""
        src = QUrl.fromLocalFile(qr_path).toString()
        return f'<p><img src="{src}" width="{size}" height="{size}"></p>'

    def _donation_prompt_due(self):
        status = settings_store.store().value("donation_status", "none")
        if status == "monthly":
            return False
        if status == "once":
            last = settings_store.store().value("donation_last_ack", "")
            if last:
                try:
                    from datetime import datetime, timedelta
                    if datetime.now() < datetime.fromisoformat(last) + timedelta(days=30):
                        return False
                except Exception:
                    pass
        return True

    def _maybe_show_tip_at_startup(self):
        if bool(settings_store.store().value("show_tips_on_startup", True, type=bool)):
            self._show_tip_dialog(startup=True)

    def _run_startup_prompts(self):
        """ウィンドウが画面に配置された後に呼ぶ起動時ダイアログ一式（アプリ本体の上に重ねて出す）。
        main() から show() 直後に同期実行すると、まだ本体が配置される前に出て画面中央に浮くため
        QTimer.singleShot 経由で遅延実行する。"""
        self._show_startup_disclaimer()
        self._check_updates(silent=True)                 # 配布フォルダの新版を自動チェック
        self._maybe_show_tip_at_startup()                # 今日のヒント

    def _open_series_files(self, files, study_uid=""):
        self.current_study_uid = study_uid or None; self._current_files = list(files)
        # 開く前に「カタログが主張している素性」を取り、読み込み時に実ファイルと照合させる。
        # 同じフォルダを別検査で使い回されるとパスはそのままに中身が入れ替わり、一覧の見出しと
        # 実際に開く画像が食い違う（2026-07-20 実機: MR のはずが別患者の CT が開いた）。
        expect = None
        try:
            expect = self.catalog.identity_for(list(files), study_uid or None)
        except Exception:
            expect = None
        import bg
        bg.run_with_progress(self, L(f"Loading series… ({len(files)} images)", f"シリーズを読み込み中…（{len(files)}枚）"),
            lambda prog: dicom_io.load_series_files(files, progress=prog, expect=expect),
            self._on_series_loaded,
            on_fail=lambda m: self._on_series_load_failed(m, expect))

    def _show_bilingual_error(self, msg, title, headline, extra_button=None):
        """`英文@@JA@@和文` 形式のメッセージをモーダルで見せる。押されたボタンを返す（無ければ None）。
        重要な失敗（取り違え・フォルダが読めない）はステータスバー1行では見落とすため必ず止める。"""
        en, ja = msg.split("@@JA@@", 1)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title); box.setText(headline)
        box.setInformativeText(L(en.strip(), ja.strip()))
        btn = box.addButton(extra_button, QMessageBox.DestructiveRole) if extra_button else None
        box.addButton(L("Close", "閉じる"), QMessageBox.RejectRole)
        box.exec()
        return btn if (btn is not None and box.clickedButton() is btn) else None

    def _on_series_load_failed(self, msg, expect=None):
        """読み込み失敗。中身の食い違い（別検査への置き換わり）は取り違え事故に直結するので、
        ステータスバーの一行ではなく **必ずモーダルで止めて** 何が起きたかを具体的に示す。"""
        if "@@JA@@" in (msg or ""):                          # StaleSeriesError（英日を1本にまとめてある）
            picked = self._show_bilingual_error(
                msg,
                L("This series does not match the catalogue", "記録と中身が一致しません（開いていません）"),
                L("⚠ Not opened — the images on disk are not this series.",
                  "⚠ 開いていません — この場所にあるのは、この検査の画像ではありません。"),
                extra_button=(L("Remove from patient list", "患者リストから削除")
                              if expect and expect.get("series_uid") else None))
            if picked is not None:
                try:
                    self.catalog.remove_series(expect["series_uid"])
                    self.db.reload()
                    self.statusBar().showMessage(
                        L("Removed the stale entry. Import the folder again to re-index it.",
                          "古い記録を削除しました。フォルダを取り込み直すと最新の内容で登録されます。"), 8000)
                except Exception as ex:
                    self.statusBar().showMessage(L("Could not remove: ", "削除できませんでした: ") + str(ex), 8000)
            return
        self.statusBar().showMessage(L("Load error: ", "読み込みエラー: ") + (msg or "").splitlines()[0], 8000)

    def _on_import_failed(self, msg):
        """取り込み失敗。フォルダが読めずに固まりかけた場合は、原因と対処をモーダルで案内する。"""
        if "@@JA@@" in (msg or ""):                          # FolderUnreadableError
            self._show_bilingual_error(
                msg,
                L("Could not read the folder", "フォルダを読み取れません"),
                L("⚠ Import stopped — the folder did not respond.",
                  "⚠ 取り込みを中止しました — フォルダが応答しません。"))
            return
        self.statusBar().showMessage(L("Import error: ", "取り込みエラー: ") + (msg or "").splitlines()[0], 8000)

    def _ensure_sample_ai_cache(self):
        """同梱サンプル(Patient_01)を開いたら、同梱の事前計算AIマスク(.npz)をTSキャッシュへ展開する。
        ＝TotalSegmentator未導入の環境でも Patient_01 の3D(肝/IVC/門脈)がそのまま出る。"""
        if self.current_study_uid != self._SAMPLE_UID:
            return
        # 展開先は **必ず _ts_cache_dir() から取る**（自前でハッシュを組むと、キャッシュキーの
        # 決め方を変えたとき（抽出セット版数の混入など）にアプリの探し先とズレ、同梱サンプルの
        # 3D解剖が黙って出なくなる。実際に 2026-07-18 の抽出セット拡張で再現した）。
        _, cache = self._ts_cache_dir()
        import ts_seg
        got = ts_seg.cached_masks(cache)
        if got is not None and "liver" in got:
            return                                           # 既にキャッシュ済み
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for cand in (os.path.join(base, "sample_data", "HCC048_portal_venous.ai.npz"),
                     os.path.join(base, "..", "sample_data", "HCC048_portal_venous.ai.npz")):
            if os.path.exists(cand):
                try:
                    os.makedirs(cache, exist_ok=True)
                    data = np.load(cand)
                    for n in data.files:
                        np.save(os.path.join(cache, n + ".npy"), data[n])
                except Exception:
                    pass
                break

    def _go_database(self):
        self.db.reload()                                     # 患者行のRestoreボタン(1/2/3)を最新の保存状態に更新
        self.stack.setCurrentWidget(self.db)

    def _open_dicom(self):
        d = QFileDialog.getExistingDirectory(self, L("Select a DICOM series folder", "DICOMシリーズのフォルダを選択"))
        if not d:
            return
        self.current_study_uid = None; self._current_files = []   # 患者リスト経由でないので作業保存は使えない
        import bg
        bg.run_with_progress(self, L("Loading folder…", "フォルダを読み込み中…"),
            lambda prog: dicom_io.load_series(d, progress=prog), self._on_series_loaded,
            on_fail=lambda m: self.statusBar().showMessage(L("Load error: ", "読み込みエラー: ") + m.splitlines()[0], 8000))

    def _open_npy(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open vol.npy", "", "NumPy (*.npy)")
        if p:
            self.current_study_uid = None; self._current_files = []
            try:
                self._set_volume(dicom_io.load_npy(p)); self.stack.setCurrentWidget(self.viewer_page)
            except Exception as ex:
                self.statusBar().showMessage(f"Load error: {ex}", 8000)

    def _import_external(self, src, progress=None):
        """src 内のDICOMをアプリ専用領域へコピー(=永久保存)し、カタログへ相ごとに取り込む。
        返り値 (追加シリーズ数, study_uid|None)。バックグラウンドWorkerから呼ばれる。"""
        import os, shutil, uuid
        # フォルダの一覧取得は **時間制限つき**。Miele の受け渡し先は他アプリのコンテナ内で、
        # macOS の許可待ち等で open() が戻らないことがある。ここで無制限に待つと、進捗ダイアログの
        # Cancel は協調キャンセルゆえ効かず、アプリが永久に固まる（2026-07-20 実機で発生）。
        paths = dicom_io.list_files_bounded(src, timeout=20.0)
        store = os.path.join(self.catalog.dir, "studies"); os.makedirs(store, exist_ok=True)
        dst = os.path.join(store, "import_" + uuid.uuid4().hex[:12]); os.makedirs(dst, exist_ok=True)
        files = [p for p in paths if os.path.isfile(p) and dicom_io.is_dicom(p)]
        study_uid = None
        for i, p in enumerate(files):
            if progress and i % 40 == 0:
                progress(i, max(len(files), 1))
            try:
                shutil.copy2(p, os.path.join(dst, f"{i:06d}_" + os.path.basename(p)))
            except Exception:
                continue
            if study_uid is None and dicom_io.pydicom is not None:   # 取り込んだスタディを後で選択するため
                try:
                    ds = dicom_io.pydicom.dcmread(p, stop_before_pixels=True)
                    study_uid = str(getattr(ds, "StudyInstanceUID", "") or "") or None
                except Exception:
                    pass
        added = self.catalog.add_folder(dst, progress=progress)
        if added == 0:                                   # 既に取り込み済み → 重複コピーを掃除
            shutil.rmtree(dst, ignore_errors=True)
        self._cleanup_handoff(src)                       # 受け渡しフォルダ（~/Downloads/TIPS-Handoff/…）は用済み
        return (added, study_uid)

    @staticmethod
    def _cleanup_handoff(src):
        """Miele プラグインの受け渡しフォルダなら削除する（取り込み後は用済み・Downloads を汚さない）。

        誤爆防止に **場所と名前の両方** を確認する：親フォルダ名が TIPS-Handoff、
        自身が tips_handoff_ で始まる場合だけ。それ以外（先生が手で選んだフォルダ等）は絶対に消さない。
        """
        import os, shutil
        try:
            src = os.path.abspath(src)
            if (os.path.basename(os.path.dirname(src)) == "TIPS-Handoff"
                    and os.path.basename(src).startswith("tips_handoff_")):
                shutil.rmtree(src, ignore_errors=True)
                try:
                    os.rmdir(os.path.dirname(src))       # 空になったら親(TIPS-Handoff)も畳む（中身があれば失敗＝残る）
                except OSError:
                    pass
        except Exception:
            pass

    def _sample_src(self):
        """同梱DICOMの場所。凍結ビルドは _MEIPASS/sample_data、開発時は app/sample_data か
        リポジトリ直下 sample_data（公開版レイアウト）。無ければ None。"""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for cand in (os.path.join(base, "sample_data", self.SAMPLE_DIR),
                     os.path.join(os.path.dirname(base), "sample_data", self.SAMPLE_DIR)):
            if os.path.isdir(cand):
                return cand
        return None

    def _sample_dst(self):
        import catalog
        return os.path.join(catalog.app_data_dir(), "sample_data", self.SAMPLE_DIR)

    def _sample_present(self):
        dst = self._sample_dst()
        return any(s.get("files") and s["files"][0].startswith(dst) for s in self.catalog.series)

    def _ensure_sample_async(self):
        """配布アプリに公開サンプルCT(HCC048)を常に1例入れておく。未登録なら背景でコピー＋取込。"""
        if os.environ.get("TIPS_NO_SAMPLE"):                 # テスト・レンダ用に自動読込を抑止
            return
        if self._sample_src() is None or self._sample_present():
            return
        import bg
        w = bg.Worker(lambda prog: self._ensure_sample_work(prog))
        self._sample_worker = w
        w.done.connect(self._on_sample_ready)
        w.failed.connect(lambda _m: None)                    # 失敗しても起動は妨げない
        w.start()

    def _ensure_sample_work(self, progress=None):
        """（背景スレッド）同梱DICOMを app_data 配下へ一度コピー（安定パス・更新をまたいで有効）し取り込む。"""
        import shutil, glob as _glob, catalog
        src = self._sample_src(); dst = self._sample_dst()
        if src is None:
            return 0
        if not (os.path.isdir(dst) and _glob.glob(os.path.join(dst, "*.dcm"))):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
            attr = os.path.join(os.path.dirname(src), "ATTRIBUTION.md")   # CC BY 4.0 の出典表示も同梱
            if os.path.exists(attr):
                try:
                    shutil.copy2(attr, os.path.join(os.path.dirname(dst), "ATTRIBUTION.md"))
                except Exception:
                    pass
        return self.catalog.add_folder(dst, progress=progress)   # add_folderはuidで重複スキップ＝冪等

    def _on_sample_ready(self, added):
        if added:
            self.db.reload()

    def _open_sample(self):
        """File ▸ サンプル症例を開く。未取込なら取り込み、患者リストで選択（相が1つなら自動で開く）。"""
        if self._sample_src() is None:
            self.statusBar().showMessage(L("Sample data is not bundled in this build.",
                                           "このビルドにはサンプルデータが同梱されていません。"), 8000)
            return
        import bg
        bg.run_with_progress(self, L("Loading sample case…", "サンプル症例を読み込み中…"),
            lambda prog: self._ensure_sample_work(prog), self._on_sample_opened,
            on_fail=lambda m: self.statusBar().showMessage(L("Sample load error: ", "サンプル読込エラー: ") + m.splitlines()[0], 8000))

    def _on_sample_opened(self, added):
        self.db.reload(); self.stack.setCurrentWidget(self.db)
        # 取り込んだサンプルのstudy_uidを探して選択（相が1つなら自動オープン）
        dst = self._sample_dst()
        uid = next((s.get("study_uid") for s in self.catalog.series
                    if s.get("files") and s["files"][0].startswith(dst)), None)
        if uid:
            self.db.select_study(uid, open_if_single=True)

    def _snap_undo(self):
        """点の設定/クリア等、状態を変える操作の直前に呼ぶ。直前の1状態だけを保持する
        （スライダー/Handleのドラッグ中の連続変化は対象外＝毎ピクセルで積むと実用的でないため）。"""
        if self.vol is None:
            return
        self._undo_snapshot = self._capture_state()
        self._set_undo_enabled(True)

    def _set_undo_enabled(self, on):
        if hasattr(self, "undoBtn"):
            self.undoBtn.setEnabled(on)
            self.undoBtn.setStyleSheet(SS_ON if on else SS_OFF)   # setEnabledだけでは押せそうに見えてしまうため明示
        if hasattr(self, "undoAction"):
            self.undoAction.setEnabled(on)

    def _undo(self):
        if self._undo_snapshot is None:
            return
        st = self._undo_snapshot; self._undo_snapshot = None
        self._set_undo_enabled(False)
        self._restore_state(st)
        self.statusBar().showMessage(L("Undid the last action.", "直前の操作を元に戻しました。"), 4000)

    def _save_slot(self, n, notify=True, confirm_overwrite=True):
        """状態保存スロットn(1/2/3)へ保存。notify=True（既定）なら保存後に「スロットN」を明示するポップアップを出す
        （先生要望：ステータスバーの一瞬のメッセージだと見落とすため、必ず気づく形にする）。
        テスト等で内部的に呼ぶ場合は notify=False でポップアップ(モーダル)を出さない。"""
        if self.vol is None or not self.current_study_uid:
            self.statusBar().showMessage(L("Open a patient from the list first.", "先に患者リストから患者を開いてください。"), 6000)
            return False
        patient = self._current_patient_label() or L("this patient", "この患者")
        if confirm_overwrite and self.catalog.has_session(self.current_study_uid, n):
            if QMessageBox.question(self, L("Overwrite?", "上書きの確認"),
                    L(f"Slot {n} already has a saved state for {patient}. Overwrite it?",
                      f"このスロット{n}には、{patient}の保存済み状態が既にあります。上書きしますか？"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
        self.catalog.set_session(self.current_study_uid, n, self._capture_state())
        if hasattr(self, "_mark_clean"):
            self._mark_clean()                               # 保存直後は『未保存の変更なし』（閉じる時に再度聞かない）
        self._update_save_buttons()
        if notify:
            QMessageBox.information(self, L("Saved", "保存しました"),
                L(f"Saved to slot {n} for {patient}.", f"{patient}のスロット{n}に保存しました。"))
        else:
            self.statusBar().showMessage(L(f"Saved to slot {n} for {patient}.", f"{patient}のスロット{n}に保存しました。"), 5000)
        return True

    def _pick_save_slot(self):
        """自動保存先を選ぶ：空いているスロット(1→2→3)を優先。全部埋まっていれば1
        （_save_slot側の上書き確認に委ねる）。"""
        for n in (1, 2, 3):
            if not self.catalog.has_session(self.current_study_uid, n):
                return n
        return 1

    def _delete_slot(self, n):
        if not self.current_study_uid or not self.catalog.has_session(self.current_study_uid, n):
            return
        patient = self._current_patient_label() or L("this patient", "この患者")
        if QMessageBox.question(self, L("Delete saved state?", "保存を削除しますか？"),
                L(f"Delete the saved state in slot {n} for {patient}? This cannot be undone.",
                  f"{patient}のスロット{n}の保存を削除しますか？元に戻せません。"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.catalog.clear_session(self.current_study_uid, n)
            self._update_save_buttons()
            if hasattr(self, "_mark_dirty_if_unsaved"):
                self._mark_dirty_if_unsaved()                # 削除で作業がどこにも保存されなくなったら未保存に戻す
            self.statusBar().showMessage(L(f"Slot {n} deleted.", f"スロット{n}を削除しました。"), 5000)

    def _restore_session_from_db(self, study_uid, slot):
        """患者リストの Restore ボタンから呼ばれる：保存済みスロットのシリーズを開き、作業状態を復元。"""
        state = self.catalog.get_session(study_uid, slot)
        if not state or not state.get("files"):
            self.statusBar().showMessage(L("Saved state not found.", "保存された作業状態が見つかりません。"), 6000)
            return
        self._pending_restore = state
        self._open_series_files(state["files"], study_uid)

    # ---------- 入力 ----------
    def _place_probe(self, plane, col, row):
        """経腹プローブを、クリックしたCT断面(0=Axi/1=Cor/2=Sag)の皮膚へ吸着して設置。
        接触点(world mm)・体内向き法線(world単位)・置いた断面を保存。クリック/ドラッグ共通。"""
        v = self.vol; nz, H, W = v.shape
        if plane == 0:                                       # Axial: sl[row=y, col=x]
            sl = v.array[int(self.cz)]
            (cc, rr), (nx, ny) = core.snap_to_skin(sl, col, row, v.sx, v.sy)
            self.contact = np.array([cc * v.sx, rr * v.sy, self.cz * v.dz])
            self.normal = np.array([nx, ny, 0.0])
        elif plane == 1:                                     # Coronal: 表示(col=x, row=nz-1-z), y=cy固定
            sl = v.array[::-1, int(self.cy), :]              # [row=表示z, col=x]（表示と同じ向き）
            (cc, rr), (nx, ny) = core.snap_to_skin(sl, col, row, v.sx, v.dz)
            self.contact = np.array([cc * v.sx, self.cy * v.sy, ((nz - 1) - rr) * v.dz])
            self.normal = np.array([nx, 0.0, -ny])           # 表示row+ = z- なので z成分は -ny
        else:                                                # Sagittal: 表示(col=y, row=nz-1-z), x=cx固定
            sl = v.array[::-1, :, int(self.cx)]              # [row=表示z, col=y]
            (cc, rr), (nx, ny) = core.snap_to_skin(sl, col, row, v.sy, v.dz)
            self.contact = np.array([self.cx * v.sx, cc * v.sy, ((nz - 1) - rr) * v.dz])
            self.normal = np.array([0.0, nx, -ny])
        self.surfPlane = plane; self._auto_orient_surface(); self._refresh()

    def _move_point_ice(self, pid, col, row):
        """ICE上でEntry/Target/実際の針先をドラッグ→新位置をworldに逆投影。"""
        world = self._ice_to_world(col, row)
        if world is None:
            return
        if pid == "entry":
            self.entry = world
        elif pid == "target":
            self.target = world
        elif pid == "aim_tip":
            self.aim_tip = world
        self._refresh()

    def _set_roll(self, v):
        self.iceRoll = float(v); self.rollVal.setText(f"{int(v)}°"); self._refresh()

    def _reset_roll(self):
        self._snap_undo()
        self.iceRoll = 0.0; self.sRoll.setValue(0); self.rollVal.setText("0°"); self._refresh()

    def _toggle_needletype(self):
        self.needleStraight = not self.needleStraight
        self.needleTypeBtn.setText("Needle: RUPS (straight)" if self.needleStraight else "Needle: Colapinto")
        self._refresh()

    def _set_advance(self, v):
        self.needleAdvance = float(v); self.advVal.setText(f"{int(v)} mm"); self._refresh()

    def _set_colaR(self, v):
        self.colaR = float(v); self.curveVal.setText(f"R {int(v)} mm"); self._refresh()

    def _toggle_plot(self):
        self.predict = not self.predict; self._update_step_ui(); self._refresh()

    def _toggle_predneedle(self):
        self.pred_curved = not self.pred_curved
        self.predNeedleBtn.setText("Pred: Colapinto" if self.pred_curved else "Pred: RUPS")
        self._refresh()

    def _clear_plots(self):
        self._snap_undo()
        self.obs = []; self._refresh()

    def _pred_world(self):
        """予測の幾何（track / 予測軌道 / 読み値 / 実測曲率）を world で返す。"""
        if not self.predict or self.entry is None or not self.obs:
            return None
        pts = [np.asarray(self.entry, float)] + [np.asarray(o, float) for o in self.obs]
        if len(pts) < 2:
            return None
        p_prev, p_tip = pts[-2], pts[-1]
        radius = core.fit_circle_radius(pts) if len(pts) >= 3 else None     # 3点以上→実測曲率
        R = radius if (radius and 10.0 < radius < 400.0) else self.colaR    # 妥当なら実測, 既定はスライダー値
        if self.pred_curved:
            pred = core.predict_curve(p_prev, p_tip, radius=R)
        else:
            pred = core.predict_straight(p_prev, p_tip)
        rd = core.predict_readout(p_prev, p_tip, self.target) if self.target is not None else None
        return dict(track=np.array(pts), pred=pred, rd=rd, radius=radius)

    def _adjust_wl(self, dWW, dWL):
        self.ww = max(1.0, self.ww + dWW); self.wl += dWL; self._refresh()

    def _scroll(self, plane, d):
        if self.vol is None:
            return
        nz, H, W = self.vol.shape
        if plane == 0:
            self.cz = int(np.clip(self.cz + d, 0, nz - 1))
        elif plane == 1:
            self.cy = int(np.clip(self.cy + d, 0, H - 1))
        else:
            self.cx = int(np.clip(self.cx + d, 0, W - 1))
        self._refresh()

    def _scroll_to(self, plane, v):
        if self.vol is None:
            return
        if plane == 0:
            self.cz = v
        elif plane == 1:
            self.cy = v
        else:
            self.cx = v
        self._refresh()

    def _spin_theta(self, d):
        if self.lock3:
            return
        self.theta = (self.theta + d * 5) % 360; self.sTheta.setValue(int(self.theta)); self._refresh()

    def _set_theta(self, v):
        if self.lock3:
            return
        self.theta = float(v); self._refresh()

    def _set_b2(self, v): self.b2 = float(v); self._refresh()
    def _zero_defl(self):
        self._snap_undo(); self.b1 = self.b2 = 0.0; self.sB1.setValue(0); self.sB2.setValue(0); self._refresh()
    def _toggle_flip(self): self.flip = not self.flip; self._refresh()

    def _set_viewmode(self, m):
        if m == self.viewMode:
            self._update_mode_ui(); self._refresh_toggles(); return
        self.viewMode = m
        self._update_mode_ui(); self._refresh_toggles(); self._refresh()

    def _toggle_viewmode(self):                               # 後方互換（テスト等）
        self._set_viewmode("surface" if self.viewMode == "ice" else "ice")

    def _clear_needle(self):
        self._snap_undo()
        self.entry = None; self.target = None; self.ptMode = 0
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0
        self._update_step_ui(); self._refresh()
    def _set_step(self, s): self.step = s; self._update_step_ui(); self._refresh()
    def _set_ptmode(self, m): self.ptMode = m; self._update_step_ui()

    def _nudge_torque(self, deg):
        """手元でカニューラを右/左に回した想定角度を進める。予測点線(2cm)の曲がる向きに反映。"""
        self._snap_undo()
        self.aim_torque = (self.aim_torque + deg + 180.0) % 360.0 - 180.0
        self.hubWidget.set_torque(self.aim_torque); self._refresh()

    def _reset_torque(self):
        """スティフニングカニューラの回旋を初期位置(0°)へ戻す。0°は正面ではなく設計上の基準向き。"""
        self._snap_undo()
        self.aim_torque = 0.0
        self.hubWidget.set_torque(0.0); self._refresh()
        self.statusBar().showMessage(L("Stiffening cannula returned to its home rotation (0°).",
                                       "スティフニングカニューラを初期位置（0°）に戻しました。"), 3000)

    def _set_insertion_default(self, fem):
        """Settings ▸ Default insertion route。施設の既定を保存しつつ今の患者にも即反映する。"""
        settings_store.store().setValue("insertion_default", "femoral" if fem else "jugular")
        self._set_insertion(fem)
        self.actInsFem.setChecked(fem); self.actInsJug.setChecked(not fem)

    # ---------- 再描画 ----------
    def _surf_plane_axis(self):
        """経腹モードの撮像面の法線（θ回転の基準）。置いたCT断面の法線／3D自由設置は
        ビームと鉛直を含む面。_geom と _auto_orient_surface で同一の基準を使うため共通化。"""
        if self.surfPlane in (0, 1, 2):
            return np.array([(0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)][self.surfPlane])
        n0 = self.normal / (np.linalg.norm(self.normal) + 1e-9)
        axis = np.cross(n0, [0.0, 0.0, 1.0])
        if np.linalg.norm(axis) < 1e-3:
            axis = np.array([1.0, 0.0, 0.0])
        return axis

    def _auto_orient_surface(self):
        """経腹モードでプローブを置いたら、接触点＋Entry＋Targetの3点が最もよく乗る断面へ
        扇を自動回転（θ）し、傾き/あおりは0に戻して『3点が映る断面』を初期表示する。
        Entry/Target 未設定なら何もしない（先生要望2026-07-14）。"""
        if self.viewMode != "surface" or self.contact is None or self.normal is None:
            return
        if self.entry is None or self.target is None:
            return
        best = core.best_surface_theta(self.contact, self.normal, self.entry, self.target,
                                       plane_axis=self._surf_plane_axis())
        self.theta = float(best); self.b1 = 0.0; self.b2 = 0.0
        for s, val in ((self.sTheta, self.theta), (self.sB1, self.b1), (self.sB2, self.b2)):
            s.blockSignals(True); s.setValue(int(round(val))); s.blockSignals(False)

    def _geom(self):
        if self.vol is None:
            return None
        if self.viewMode == "surface":                       # 経腹: 接触点から体内へ向く凸型扇
            if self.contact is None:
                return None
            return core.surface_geometry(self.contact, self.normal, self.theta, self.b1, self.b2,
                                         self.vol.sx, self.vol.sy, self.vol.dz, plane_axis=self._surf_plane_axis())
        if len(self.path) < 2:
            return None
        return core.ice_geometry(self.path, self.zP, self.theta, self.b1, self.b2,
                                 self.vol.sx, self.vol.sy, self.vol.dz, tip_high_z=self.tipHighZ)

    def _needle(self):
        """穿刺軌道＝Entry→Target を結ぶ直線（最小・確実）。
        曲げ系(RUPS固定カニューラ/Colapinto固有曲率)は一旦封印し、ここから再実装する。
        旧版: core.rups_path / core.colapinto_path（geometry.py に温存）。"""
        if self.entry is None or self.target is None:
            return None
        return core.straight_path(self.entry, self.target)

    def _draw_device(self, p, nd, to_pt):
        """機構dict（カニューラ=明色実線＋針=点線アンバー）を描く。to_pt: world点→QPointF。"""
        if not nd:
            return
        if nd.get("cannula") is not None:
            p.setPen(QPen(QColor(120, 220, 235), 3)); p.setBrush(Qt.NoBrush)   # カニューラ=明色実線
            p.drawPolyline(QPolygonF([to_pt(P) for P in nd["cannula"]]))
        p.setPen(QPen(AMBER, 2, Qt.DashLine)); p.setBrush(Qt.NoBrush)          # 針=点線
        p.drawPolyline(QPolygonF([to_pt(P) for P in nd["needle"]]))

    def _paint_pred(self, p, pw, w2, label=True):
        """観測プロット点・点線追跡・前方予測（RUPS直進/Colapinto頭側弧）を描く。"""
        track = pw["track"]; pred = pw["pred"]
        if len(track) >= 2:                                 # 観測の点線追跡（マゼンタ）
            p.setPen(QPen(QColor(235, 90, 200), 2, Qt.DotLine)); p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([w2(q) for q in track]))
        p.setBrush(QColor(235, 90, 200)); p.setPen(QPen(Qt.white, 1))
        for q in track:
            p.drawEllipse(w2(q), 3, 3)
        if len(pred) >= 2:                                  # 前方予測（直進=実線/曲線=破線・黄）
            p.setPen(QPen(QColor(255, 210, 70), 2, Qt.SolidLine if not self.pred_curved else Qt.DashLine))
            p.setBrush(Qt.NoBrush); p.drawPolyline(QPolygonF([w2(q) for q in pred]))
        if label and pw["rd"] is not None:
            rd = pw["rd"]; side = "cranial" if rd["side"] > 0 else "caudal"
            if self.pred_curved:
                txt = f"Colapinto (curves cranially)  Target is {side} → {'closing in' if rd['side'] > 0 else 'moving away'}  d={rd['perp']:.0f}mm"
            else:
                txt = f"RUPS (straight)  {rd['perp']:.0f}mm to target  Target is {side}"
            if pw["radius"]:
                txt += f"  measured R≈{pw['radius']:.0f}mm"
            f = QFont(); f.setPointSize(11); f.setBold(True); p.setFont(f)
            p.setPen(QColor(255, 210, 70)); p.drawText(8, 33, txt); p.setFont(QFont())

    # ---------- 肝臓ゴースト（3D・自動抽出） ----------
    def _toggle_liver(self):
        self.show_liver = self.liverBtn.isChecked()
        self.p3d.show_liver = self.show_liver; self.p3d.update()

    def _toggle_liver_mode(self):
        self.liver_mode = "surface" if self.liver_mode == "haze" else "haze"
        self._update_liver_btn()
        self.p3d.liver_mode = self.liver_mode; self.p3d.update()

    def _set_liver_opacity(self, v):
        self.liver_opacity = max(0.05, v / 100.0)
        self.p3d.liver_opacity = self.liver_opacity; self.p3d.update()

    def _compute_body(self):
        """読込時に体表シェルを背景で抽出（モーダル無し・経腹エコーの3D面）。"""
        if self.vol is None:
            return
        key = id(self.vol)
        if key == self._body_key:
            return
        if self._body_worker is not None and self._body_worker.isRunning():
            return
        self._body_key = key
        import bg
        vol = self.vol
        w = bg.Worker(lambda prog: liver_core.body_surface(vol.array, vol.sx, vol.sy, vol.dz))
        self._body_worker = w
        w.done.connect(self._on_body_done)
        w.failed.connect(lambda _m: None)
        w.start()

    def _on_body_done(self, body):
        self.body = body; self.p3d.body = body
        if self.viewMode == "surface":
            self.p3d.show_body = True; self.p3d.update()

    def _pick_surface_3d(self, idx):
        """3D体表上でプローブを設置/移動（idx=body['surf']の点）。撮像面はビーム＋鉛直を含む面。"""
        if self.body is None or idx < 0 or idx >= len(self.body["surf"]):
            return
        self.contact = np.asarray(self.body["surf"][idx], float)
        n_in = -np.asarray(self.body["nrm"][idx], float)      # 外向き法線→内向き
        nn = np.linalg.norm(n_in); self.normal = n_in / nn if nn > 1e-6 else np.array([0.0, 1.0, 0.0])
        self.surfPlane = -1                                    # 3D自由設置
        self._auto_orient_surface(); self._refresh()

    def _on_liver_done(self, res):
        self.liver = res; self.p3d.liver = res; self.p3d.update()
        self._maybe_compute_liver()                      # 計算中にパスが変わっていれば追従

    def _stop_workers(self):
        """走行中の背景スレッド(肝抽出/DICOM読込)に中断を頼み、終わるまで待つ。
        **止まらなかったスレッドの一覧**を返す（呼び側が終了方法を決められるように）。

        QThread が走行中に破棄されると Qt は qFatal→abort() する。closeEvent だけでは足りない:
        Cmd+Q と更新後の再起動は QApplication.quit() を通り、closeEvent を呼ばずに終了するため、
        aboutToQuit からも必ずここを通す（実際に SIGABRT を再現・修正を確認済み）。
        """
        stuck = []
        for w in [getattr(self, "_liver_worker", None), getattr(self, "_body_worker", None),
                  getattr(self, "_ts_worker", None), getattr(self, "_ts_install_worker", None)] \
                + list(getattr(self, "_bg_workers", [])):
            try:
                if w is not None and w.isRunning():
                    w.requestInterruption()
                    if not w.wait(3000):
                        stuck.append(w)
            except RuntimeError:                         # 既に C++ 側破棄済み等は無視
                pass
        return stuck

    def _shutdown(self):
        """終了時の後片付け。**止まらないスレッドが居たら、通常終了せずプロセスを即終了する。**

        フォルダ走査などが OS のシステムコール（opendir 等）で固まると、`requestInterruption` も
        `wait()` も効かない。その状態で Python/PySide の後片付けに進むと、Qt が走行中の QThread を
        破棄して `qFatal` → `abort()`＝**クラッシュ報告付きで落ちる**（2026-07-20 実機で発生・
        先生からクラッシュレポート受領）。ユーザーから見れば「固まった上に異常終了した」なので、
        ここで静かに幕を引く。設定・カタログは変更時に都度保存済みなので失うものはない。
        """
        stuck = self._stop_workers()
        if stuck:
            try:
                sys.stdout.flush(); sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)                                  # Qt の破棄処理を通さない＝abort() させない

    def _aim_3d(self):
        """実際の針(Entry→aim_tip)の3D表現：半透明の外形＋Colapinto想定2cm予測。無ければ(None,None)。"""
        if self.entry is None or self.aim_tip is None:
            return None, None
        try:
            outline = core.needle_glyph(self.entry, self.aim_tip)["outline"]
            pred = core.predict_curve(self.entry, self.aim_tip, radius=core.COLA_R,
                                      span_deg=np.degrees(20.0 / core.COLA_R), torque_deg=self.aim_torque)
            return outline, pred
        except Exception:
            return None, None

    def _metal_needle_3d(self):
        """TIPS金属針システム(外筒＋弯曲接続部＋Chiba針＋進行方向)の3D幾何。
        Entryだけでも外筒を立て、Targetが決まれば接続弧・針・進行方向を連続で足す。無ければ全てNone。
        返り値: dict(cannula, fillet, glyph, pred) 相当を辞書で。"""
        if self.entry is None:
            return {}
        try:
            asm = core.needle_assembly(self.entry, self.target)   # target=Noneなら外筒のみ
            return dict(cannula_rod=asm["cannula"], needle_body=asm.get("body"),
                        needle_tip=asm.get("tip"), needle_pred=asm.get("pred"))
        except Exception:
            return {}

    def _open_feedback_form(self):
        """フィードバック用Googleフォームを今のUI言語に合わせてブラウザで開く（日本語→日本語版／英語→英語版）。"""
        url = FEEDBACK_FORM_JA if i18n.lang() == "ja" else FEEDBACK_FORM_EN
        QDesktopServices.openUrl(QUrl(url))

    def _show_donation(self):
        qr = self._qr_html()
        url = self._donation_url()                            # 英語=GitHub Sponsors / 日本語=note（先生指示 2026-07-21）
        if i18n.lang() == "ja":
            html = f"""
        <h2>開発を支援</h2>
        <p>このツールは独立・自己資金で開発しています。開発には時間と費用がかかります。
        役に立ったと感じたら、寄付がオープンな開発の継続を支えます &mdash; ありがとうございます。</p>
        <p><b>note（作者の募金ページ）から:</b><br>
        <a href="{url}">{url}</a></p>
        {qr}
        <p style="color:#888">寄付はオープンソース開発の支援であり、医療機器の販売では<b>ありません</b>。
        保証もありません。作者: {AUTHOR_LINE}.</p>
        """
        else:
            html = f"""
        <h2>Support development</h2>
        <p>This tool is developed independently and is self-funded. Development takes time and money.
        If it is useful to you, a donation helps continue open development &mdash; thank you.</p>
        <p><b>Donate via GitHub Sponsors:</b><br>
        <a href="{url}">{url}</a></p>
        {qr}
        <p style="color:#888">A donation supports open-source development. It is <b>not</b> the sale of a medical device
        and carries no warranty. Author: {AUTHOR_LINE}.</p>
        """
        self._info_dialog(L("Support development (Donation)", "開発を支援（寄付）"), html, open_url=url)

    # ---------- 起動時の寄付リマインド（月額支援者は出さない／単発支援者は1か月後にまた） ----------
    def _show_tip_dialog(self, startup=False):
        s = settings_store.store()
        idx = int(s.value("next_tip_index", 0)) % len(TIPS_EN_JA)
        en, ja = TIPS_EN_JA[idx]
        s.setValue("next_tip_index", (idx + 1) % len(TIPS_EN_JA))

        dlg = QDialog(self); dlg.setWindowTitle(L("Tip of the Day", "今日のヒント")); dlg.resize(440, 200)
        v = QVBoxLayout(dlg)
        lbl = QLabel(L(en, ja)); lbl.setWordWrap(True); v.addWidget(lbl, 1)
        if startup:
            chk = QCheckBox(L("Show a tip at startup", "起動時にヒントを表示する"))
            chk.setChecked(bool(s.value("show_tips_on_startup", True, type=bool)))
            chk.toggled.connect(lambda checked: s.setValue("show_tips_on_startup", checked))
            v.addWidget(chk)
        row = QHBoxLayout(); row.addStretch(1)
        bNext = QPushButton(L("Next tip", "次のヒント")); bClose = QPushButton(L("Close", "閉じる"))
        row.addWidget(bNext); row.addWidget(bClose); v.addLayout(row)
        bNext.clicked.connect(lambda: (dlg.accept(), self._show_tip_dialog(startup=False)))
        bClose.clicked.connect(dlg.accept)
        dlg.exec()

