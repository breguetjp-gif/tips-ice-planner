"""入口のビュー（検査一覧＋サムネイル帯）。

  上: 検査ごとに1行だけのスタディ表（読みやすい濃紺ゼブラ・Comment 列は編集可）。
  下: 選択スタディのシリーズをサムネイルで横並び（説明 + N img）。
  開く = サムネイルをダブルクリック（または Open）→ openSeries 発火 → ビューアへ。
"""
from __future__ import annotations
import numpy as np
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QSplitter, QLineEdit, QFileDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QFrame, QDialog, QCheckBox)
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont
from PySide6.QtCore import Qt, Signal, QSize

import i18n
from i18n import L


def _sep():
    f = QFrame(); f.setFrameShape(QFrame.VLine); f.setStyleSheet("color:#33506e"); return f

# 検査=1行。シリーズ名は下のサムネ帯に出すので Description 列との二重表示を廃止。
# Restore列＝各行に埋め込みボタン(1/2/3)。患者ごとに直接紐づく＝「今どの行が選択中か」に依存しない(先生要望)。
COLS = ["Patient", "Patient ID", "Institution", "Age", "Modality", "Acquired", "Series", "Images", "Description", "Restore", "Comment"]
C_PATIENT, C_ID, C_INST, C_AGE, C_MOD, C_ACQ, C_NSER, C_NIMG, C_DESC, C_RESTORE, C_COMMENT = range(11)
ROLE_STUDY = Qt.UserRole + 1       # study dict
ROLE_STUDYUID = Qt.UserRole + 2
ROLE_FILES = Qt.UserRole + 3       # サムネ item: series files

TREE_QSS = (
    "QTreeWidget{background:#0c1a2a;alternate-background-color:#13283f;color:#e6eef7;"
    "  border:1px solid #1c3550;outline:0;}"
    "QTreeWidget::item{padding:6px 6px;border-bottom:1px solid #16293d;}"
    "QTreeWidget::item:selected{background:#2f6df0;color:#ffffff;}"
    "QHeaderView::section{background:#24405d;color:#e6eef7;padding:6px;border:0;"
    "  border-right:1px solid #16293d;font-weight:bold;}")
THUMB_QSS = (
    "QListWidget{background:#0a1622;color:#d8e3ef;border:1px solid #1c3550;}"
    "QListWidget::item{padding:6px;border:1px solid transparent;}"
    "QListWidget::item:selected{background:#19314c;border:1px solid #2f6df0;color:#ffffff;}")
# 復元ボタン(1/2/3)の有効/無効を明示（setEnabledの見た目だけだと区別しづらいため・main.pyのSS_ON/SS_OFFと同じ考え方）
_RESTORE_ON = "background:#F08F69;color:#15263a;font-weight:bold;border:1px solid #ffd0bb;border-radius:5px;padding:3px 9px;"
_RESTORE_OFF = "background:#16293d;color:#4c5c70;border:1px solid #1c3550;border-radius:5px;padding:3px 9px;"


class ImportPickerDialog(QDialog):
    """取込前のプレビュー：見つかったシリーズをチェックボックスで選び、匿名化するかどうかを選ぶ。
    groups は catalog.scan_series() の返り値（list[dict]、各dictに series_uid/patient_name/
    patient_id/series_desc/modality/series_no/files を含む）。"""

    def __init__(self, parent, groups, already_uids):
        super().__init__(parent)
        self.setWindowTitle(L("Select series to import", "取り込むシリーズを選択"))
        self.resize(760, 440)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(L(f"Found {len(groups)} series. Choose which to import.",
                            f"{len(groups)} 件のシリーズが見つかりました。取り込むものを選んでください。")))
        self.tree = QTreeWidget(); self.tree.setStyleSheet(TREE_QSS)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels([L("Import", "取込"), L("Patient", "患者"), L("ID", "ID"),
                                   L("Series", "シリーズ"), L("Modality", "モダリティ"), L("Images", "枚数")])
        self.tree.setRootIsDecorated(False)
        self._rows = []                                          # (item, group)
        for g in sorted(groups, key=lambda x: (x.get("study_uid", ""), x.get("series_no", 0))):
            already = g["series_uid"] in already_uids
            desc = f'{g.get("series_no", 0)}: {g.get("series_desc", "")}'
            if already:
                desc += "  " + L("(already imported)", "（取込済み）")
            it = QTreeWidgetItem(["", g.get("patient_name", ""), g.get("patient_id", ""),
                                  desc, g.get("modality", ""), str(len(g.get("files", [])))])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked if already else Qt.Checked)   # 取込済みは既定オフ（重複防止）
            self.tree.addTopLevelItem(it)
            self._rows.append((it, g))
        for i in range(6):
            self.tree.resizeColumnToContents(i)
        v.addWidget(self.tree, 1)

        pick_row = QHBoxLayout()
        allBtn = QPushButton(L("Select all", "すべて選択"))
        allBtn.clicked.connect(lambda: self._set_all(True))
        noneBtn = QPushButton(L("Select none", "すべて解除"))
        noneBtn.clicked.connect(lambda: self._set_all(False))
        pick_row.addWidget(allBtn); pick_row.addWidget(noneBtn); pick_row.addStretch(1)
        v.addLayout(pick_row)

        self.anonChk = QCheckBox(L(
            "Anonymize patient info on import (name / ID / birth date replaced; a separate copy is made — "
            "original files are never modified)",
            "患者情報を匿名化して取り込む（氏名・ID・生年月日を置き換え／元ファイルは書き換えず別コピーを作成）"))
        v.addWidget(self.anonChk)

        btn_row = QHBoxLayout(); btn_row.addStretch(1)
        cancelBtn = QPushButton(L("Cancel", "キャンセル")); cancelBtn.clicked.connect(self.reject)
        okBtn = QPushButton(L("Import", "取り込む")); okBtn.clicked.connect(self.accept)
        okBtn.setDefault(True)
        btn_row.addWidget(cancelBtn); btn_row.addWidget(okBtn)
        v.addLayout(btn_row)

    def _set_all(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for it, _ in self._rows:
            it.setCheckState(0, state)

    def selected_groups(self):
        return [g for it, g in self._rows if it.checkState(0) == Qt.Checked]

    def anonymize(self):
        return self.anonChk.isChecked()


class DatabaseView(QWidget):
    openSeries = Signal(list, str)      # files of the chosen series, its study_uid
    restoreSession = Signal(str, int)   # study_uid, slot(1/2/3) — 保存済み作業状態の復元
    langToggled = Signal()              # 言語切替ボタンが押された（実処理はMainWindow._toggle_langに一本化）

    def __init__(self, catalog):
        super().__init__()
        self.cat = catalog
        self._filter = ""
        self.anon = False                                        # 匿名化（氏名・ID・年齢を隠す・表示のみ）
        self._restoreWidgets = {}                                 # study_uid -> その行のRestoreボタン群ウィジェット
        root = QVBoxLayout(self); root.setContentsMargins(6, 6, 6, 6); root.setSpacing(6)

        # --- ツールバー ---
        bar = QHBoxLayout()
        self.impBtn = QPushButton(); self.impBtn.clicked.connect(self._import)
        self.openBtn = QPushButton(); self.openBtn.clicked.connect(self._open_selected)
        self.delBtn = QPushButton(); self.delBtn.clicked.connect(self._remove)
        self.anonBtn = QPushButton(); self.anonBtn.setCheckable(True)
        self.anonBtn.clicked.connect(self._toggle_anon)
        self.search = QLineEdit()
        self.search.textChanged.connect(self._on_filter)
        self.searchLbl = QLabel()
        for w in (self.impBtn, self.openBtn, self.delBtn, self.anonBtn):
            bar.addWidget(w)
        bar.addWidget(_sep())
        # Restore(復元)は患者リストの各行に直接ボタンを持たせる（下のtreeで構築。ここには置かない＝先生要望）。
        self.langBtn = QPushButton(); self.langBtn.clicked.connect(self.langToggled.emit)
        self.langBtn.setToolTip("Switch UI language / 表示言語の切り替え")
        bar.addWidget(self.langBtn)
        bar.addStretch(1); bar.addWidget(self.searchLbl); bar.addWidget(self.search, 2)
        root.addLayout(bar)

        # --- 検査表（上） / サムネイル帯（下）---
        split = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget(); self.tree.setColumnCount(len(COLS))
        self.tree.setHeaderLabels(COLS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)                       # 子を持たない＝展開マーク不要
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)  # Comment 以外は編集させない
        self.tree.setStyleSheet(TREE_QSS)
        f = QFont(); f.setPointSize(13); self.tree.setFont(f)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemClicked.connect(self._on_click)             # Comment列は1クリックで入力開始（先生要望）
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.itemSelectionChanged.connect(self._on_select_study)
        split.addWidget(self.tree)

        bottom = QWidget(); bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(3)
        self.thumbHdr = QLabel("Series — select a study above; double-click a thumbnail to open")
        self.thumbHdr.setStyleSheet("color:#F08F69;font-weight:bold;padding:2px;")
        bl.addWidget(self.thumbHdr)
        self.thumbs = QListWidget(); self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setIconSize(QSize(132, 132)); self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setGridSize(QSize(160, 178)); self.thumbs.setSpacing(8)
        self.thumbs.setMovement(QListWidget.Static); self.thumbs.setWordWrap(True)
        self.thumbs.setStyleSheet(THUMB_QSS)
        self.thumbs.itemDoubleClicked.connect(self._on_thumb_double)
        bl.addWidget(self.thumbs, 1)
        split.addWidget(bottom); split.setSizes([460, 300])
        root.addWidget(split, 1)

        self.status = QLabel(""); self.status.setStyleSheet("color:#869bb2;")
        root.addWidget(self.status)
        self.retranslate()                                       # ツールバー文言＋reload()

    def retranslate(self):
        """現在のUI言語でツールバー等の文言を貼り替え、一覧を再構築（main._apply_language から呼ばれる）。"""
        self.impBtn.setText(L("Import DICOM folder…", "DICOMフォルダを取り込み…"))
        self.openBtn.setText(L("Open", "開く"))
        self.delBtn.setText(L("Remove study", "検査を削除"))
        self.anonBtn.setText(L("Anonymized", "匿名化 中") if self.anon else L("Anonymize", "匿名化"))
        self.anonBtn.setToolTip(L("Hide patient name / ID / age (for screen sharing, teaching)",
                                  "氏名・ID・年齢を隠す（画面共有・教育用）"))
        self.search.setPlaceholderText(L("Search patient / ID / institution / description / comment…",
                                         "患者名 / ID / 施設名 / 説明 / コメントで検索…"))
        self.searchLbl.setText(L("Search:", "検索:"))
        self.langBtn.setText("日本語" if i18n.lang() == "en" else "English")   # ボタンは「切替先」の言語名
        self.reload()

    # ---- 一覧再構築（検査=1行）----
    def reload(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._restoreWidgets = {}
        shown = 0
        flt = self._filter
        for st in self.cat.studies():
            if flt and flt not in " ".join([
                    st["patient_name"], st["patient_id"], st["study_desc"],
                    st["comment"], st["modality"], st.get("institution", "")]).lower():
                continue
            nser = st.get("n_series", len(st["series"]))
            name = "●●●" if self.anon else st["patient_name"]
            pid = "●●●" if self.anon else st["patient_id"]
            age = "" if self.anon else st["age"]
            uid = st["study_uid"]
            row = [""] * len(COLS)
            row[C_PATIENT] = name; row[C_ID] = pid; row[C_INST] = st.get("institution", "")
            row[C_AGE] = age; row[C_MOD] = st["modality"]
            row[C_ACQ] = st["study_date"]; row[C_NSER] = str(nser); row[C_NIMG] = str(st["n_images"])
            row[C_DESC] = st["study_desc"]; row[C_COMMENT] = st["comment"]
            it = QTreeWidgetItem(row)
            it.setData(0, ROLE_STUDY, st)
            it.setData(0, ROLE_STUDYUID, uid)
            it.setFlags(it.flags() | Qt.ItemIsEditable)          # Comment 列の編集に必要
            it.setTextAlignment(C_AGE, Qt.AlignCenter)
            it.setTextAlignment(C_MOD, Qt.AlignCenter)
            it.setTextAlignment(C_NSER, Qt.AlignRight | Qt.AlignVCenter)
            it.setTextAlignment(C_NIMG, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it); shown += 1
            w = self._make_restore_widget(uid)
            self.tree.setItemWidget(it, C_RESTORE, w)
            self._restoreWidgets[uid] = w
        for i in range(len(COLS)):
            self.tree.resizeColumnToContents(i)
        self.tree.setColumnWidth(C_DESC, max(220, self.tree.columnWidth(C_DESC)))
        self.tree.header().setStretchLastSection(True)           # Comment を残り幅に
        self.tree.blockSignals(False)
        self.thumbs.clear()
        self.thumbHdr.setText(L("Series — select a study above; double-click a thumbnail to open",
                                "シリーズ — 上で検査を選択し、サムネイルをダブルクリックで開く"))
        ncm = len(self.cat.comments)
        self.status.setText(L(f"{shown} studies shown / {len(self.cat.series)} series total / {ncm} commented",
                              f"{shown} 検査を表示 / 全 {len(self.cat.series)} シリーズ / コメント {ncm} 件"))

    def _make_restore_widget(self, study_uid):
        """患者リストの各行に埋め込むRestore(1/2/3)ボタン群。選択状態に依存せず、その行の患者に直接紐づく
        （先生要望：復元は患者リスト内に患者ごとのボタンとして配置）。保存済みスロットだけ色で分かる。"""
        box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(2, 0, 2, 0); h.setSpacing(2)
        for n in (1, 2, 3):
            saved = self.cat.has_session(study_uid, n)
            b = QPushButton(str(n)); b.setEnabled(saved)
            b.setStyleSheet(_RESTORE_ON if saved else _RESTORE_OFF)
            b.setFixedSize(24, 22)
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, uid=study_uid, nn=n: self._on_restore_delete(uid, nn))
            b.clicked.connect(lambda _checked=False, uid=study_uid, nn=n: self._on_restore_click(uid, nn))
            tip = L(f"Slot {n}: bring back the saved IVC path, Entry/Target, actual needle tip, "
                    f"and view settings for this patient. Right-click to delete." + ("" if saved else " [empty]"),
                    f"スロット{n}: この患者専用に保存済みのIVCパス・Entry/Target・実際の針先・表示設定を"
                    f"呼び戻します。右クリックで削除。" + ("" if saved else "【未保存】"))
            b.setToolTip(tip)
            h.addWidget(b)
        h.addStretch(1)
        return box

    def select_study(self, study_uid, open_if_single=True):
        """study_uid のスタディをツリーで選択しサムネ表示。相(シリーズ)が1つなら自動で開く。
        外部から取り込んだ直後に呼ぶ。見つかれば True。"""
        if not study_uid:
            return False
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, ROLE_STUDYUID) == study_uid:
                self.tree.setCurrentItem(it)                 # _on_select_study → サムネ表示
                st = it.data(0, ROLE_STUDY)
                if open_if_single and isinstance(st, dict) and len(st.get("series", [])) == 1:
                    self.openSeries.emit(st["series"][0]["files"], study_uid)
                return True
        return False

    def _on_filter(self, txt):
        self._filter = (txt or "").strip().lower(); self.reload()

    # ---- コメント編集 ----
    def _on_click(self, item, col):
        """Comment欄は1クリックでその場入力を開始（先生要望：ダブルクリックだと気づきにくかったため）。"""
        if col == C_COMMENT:
            self.tree.editItem(item, C_COMMENT)

    def _on_item_changed(self, item, col):
        if col == C_COMMENT:
            self.cat.set_comment(item.data(0, ROLE_STUDYUID), item.text(C_COMMENT))
            self.status.setText(L("comment saved", "コメントを保存しました"))

    # ---- 検査選択→サムネ表示 ----
    def _selected_study_item(self):
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _on_select_study(self):
        it = self._selected_study_item()
        if it is not None:
            st = it.data(0, ROLE_STUDY)
            self._fill_thumbs(st)

    def _on_restore_click(self, uid, n):
        if uid and self.cat.has_session(uid, n):
            self.restoreSession.emit(uid, n)

    def _on_restore_delete(self, uid, n):
        if not uid or not self.cat.has_session(uid, n):
            return
        st = next((s for s in self.cat.studies() if s["study_uid"] == uid), None)
        who = ((st.get("patient_name") or st.get("patient_id")) if isinstance(st, dict) else None) \
            or L("this patient", "この患者")
        if QMessageBox.question(self, L("Delete saved state?", "保存を削除しますか？"),
                L(f"Delete the saved state in slot {n} for {who}? This cannot be undone.",
                  f"{who}のスロット{n}の保存を削除しますか？元に戻せません。"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.cat.clear_session(uid, n)
            w = self._make_restore_widget(uid)
            it = self._find_item_by_uid(uid)
            if it is not None:
                self.tree.setItemWidget(it, C_RESTORE, w); self._restoreWidgets[uid] = w

    def _find_item_by_uid(self, uid):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, ROLE_STUDYUID) == uid:
                return it
        return None

    def _toggle_anon(self):
        """匿名化トグル：氏名・ID・年齢を隠す（表示のみ。データは不変）。"""
        self.anon = self.anonBtn.isChecked()
        self.anonBtn.setText(L("Anonymized", "匿名化 中") if self.anon else L("Anonymize", "匿名化"))
        self.reload()
        it = self._selected_study_item()
        if it is not None:
            self._fill_thumbs(it.data(0, ROLE_STUDY))

    def _fill_thumbs(self, st):
        self.thumbs.clear()
        if not isinstance(st, dict):
            return
        name = "●●●" if self.anon else st['patient_name']
        self.thumbHdr.setText(L(f"Series of  {name}  ·  {st['modality']}  ·  {st['study_date']}"
                                f"  ·  {st['study_desc']}   (double-click to open)",
                                f"{name} のシリーズ  ·  {st['modality']}  ·  {st['study_date']}"
                                f"  ·  {st['study_desc']}   （ダブルクリックで開く）"))
        for s in st["series"]:
            icon = QIcon()
            small = self.cat.thumbnail(s)
            if small is not None:
                h, w = small.shape
                qimg = QImage(np.ascontiguousarray(small).tobytes(), w, h, w, QImage.Format_Grayscale8)
                icon = QIcon(QPixmap.fromImage(qimg))
            desc = s["series_desc"] or "(series)"
            # 枚数(n img)は常に1行目に置く＝説明(desc)がどれだけ長くても折り返し/省略されるのは
            # 2行目以降のdescだけになり、肝心の枚数が隠れることがない（先生指摘の不具合対応）。
            item = QListWidgetItem(icon, f"{s['n']} img  ·  {s['modality']}\n{desc}")
            item.setData(ROLE_FILES, s["files"])
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.thumbs.addItem(item)

    # ---- 開く / 編集 ----
    def _on_double(self, item, col):
        if col == C_COMMENT:                                     # Comment はその場編集
            self.tree.editItem(item, C_COMMENT)
        else:                                                    # それ以外＝最初のシリーズを開く
            st = item.data(0, ROLE_STUDY)
            if isinstance(st, dict) and st["series"]:
                self.openSeries.emit(st["series"][0]["files"], st["study_uid"])

    def _on_thumb_double(self, item):
        it = self._selected_study_item()
        uid = it.data(0, ROLE_STUDYUID) if it is not None else ""
        self.openSeries.emit(item.data(ROLE_FILES), uid)

    def _open_selected(self):
        it = self._selected_study_item()
        if it is None:
            return
        st = it.data(0, ROLE_STUDY)
        if isinstance(st, dict) and st["series"]:
            self.openSeries.emit(st["series"][0]["files"], st["study_uid"])

    def _remove(self):
        it = self._selected_study_item()
        if it is None:
            return
        if QMessageBox.question(self, L("Remove study", "検査の削除"),
                                L("Remove this study from the catalog?\n(DICOM files on disk are kept.)",
                                  "この検査を一覧から削除しますか？\n（ディスク上のDICOMファイルは残ります）")) \
                == QMessageBox.Yes:
            self.cat.remove_study(it.data(0, ROLE_STUDYUID)); self.reload()

    # ---- 取込 ----
    def _import(self):
        d = QFileDialog.getExistingDirectory(self, L("Select a folder containing DICOM", "DICOMを含むフォルダを選択"))
        if not d:
            return
        import bg
        bg.run_with_progress(
            self, L("Scanning DICOM…", "DICOMをスキャン中…"),
            lambda prog: self.cat.scan_series(d, progress=prog),
            self._on_scanned)

    def _on_scanned(self, groups):
        """スキャン結果（シリーズ一覧）→選択ダイアログ→確定した分だけ取込。"""
        if not groups:
            self.status.setText(L("No DICOM series found in that folder.", "そのフォルダにDICOMシリーズが見つかりませんでした。"))
            return
        already = {s["series_uid"] for s in self.cat.series}
        dlg = ImportPickerDialog(self, groups, already)
        if dlg.exec() != QDialog.Accepted:
            return
        picked = dlg.selected_groups()
        if not picked:
            return
        anonymize = dlg.anonymize()
        import bg
        bg.run_with_progress(
            self, L("Anonymizing and importing…", "匿名化して取り込み中…") if anonymize
                  else L("Importing…", "取り込み中…"),
            lambda prog: self.cat.import_groups(picked, anonymize=anonymize, progress=prog),
            self._on_imported)

    def _on_imported(self, added):
        self.reload()
        self.status.setText(L(f"Imported {added} new series", f"新規 {added} シリーズを取り込みました"))
