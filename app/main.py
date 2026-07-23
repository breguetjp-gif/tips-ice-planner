"""TIPS Planner — スタンドアロン版 (Win/Mac)。Miele プラグインからの機能移植版。

研究・教育用 / 医療機器ではない / 術中ナビではない / 最終判断は術者。
計算は tips_core（Mac プラグインと同一の正本）を再利用。
入口=Database → シリーズを開く → 4画面(Axi/Cor/Sag/ICE)+3D linkage。
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
                           QDesktopServices, QNativeGestureEvent, QPainterPath, QFontMetrics, QFontMetricsF, QKeySequence)
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, Signal, QUrl, QEvent, QUrlQuery, QTimer, QSettings

import tips_core as core
from tips_core import liver as liver_core
import dicom_io
import i18n
from i18n import L
import settings_store
from handle_control import HandleControl, SurfaceProbeControl

VERSION = "0.5.56"                                            # 配布のたびに上げる（build_release.commandが反映）
UPDATE_URL = "https://raw.githubusercontent.com/breguetjp-gif/tips-ice-planner/main/version.json"  # 配布フォルダ version.json の共有リンク(直接取得)


# 共通パレット・共通ウィジェット層・起動部は正本 engine/core/{panes,app_boot}.py から（Phase 3a, 2026-07-18）
from preset import (URL_SCHEME, GITHUB_REPO, SPONSORS_URL, SPONSORS_URL_JA, AUTHOR_LINE,  # アプリ固有定数は preset.py が正
                    FEEDBACK_FORM_JA, FEEDBACK_FORM_EN, TIPS_EN_JA)       # （テスト互換の再エクスポート兼用）
from panes import (TERRA, CYAN, GREENC, REDC, AMBER, NEEDLE_COL, ACTIVE, INACTIVE_FRAME,
                   STYLE, SS_ON, SS_OFF, _roll_xy, _log_click, _frame, _sep,
                   GestureBar, CannulaHubWidget, ImagePane, PaneCell, QuadPanes)
from app_boot import path_from_open_event, _OpenDispatcher, EchoApp as TIPSApp  # 再エクスポート（テスト互換）
import app_boot
from pane3d import Pane3D
from shell import ShellWindow


# 解析の細かさの説明（実測: 1151枚 0.5mm厚・M5 Max。臓器体積を高精度と比較した差）
_QUALITY_HELP = {
    "high": ("Send the CT at its own slice thickness (about 80 s for a 1151-slice study). "
             "Choose this when a tiny liver tumour or fine vessel branches matter — those are the "
             "only structures that differed from Standard in testing.",
             "送ったCTのスライス厚のまま解析します（1151枚の症例で約80秒）。"
             "微小な肝腫瘍や細い血管枝を厳密に見たいときはこちら。"
             "実測で標準と差が出たのはその2つだけでした。"),
    "standard": ("Match the 1.5 mm the AI actually works at (about 55 s — 30% faster). "
                 "Liver, IVC, portal vein, kidney, spleen, stomach all matched High accuracy "
                 "within 1.6% by volume in testing.",
                 "AIが実際に見ている 1.5mm に合わせます（約55秒＝3割速い）。"
                 "肝臓・IVC・門脈・腎・脾・胃は実測で高精度と体積差 1.6% 以内でした。"),
    "fast": ("Coarse 3 mm pass (about 47 s). For a quick look at large structures only — "
             "in testing the gallbladder was 12% smaller and fine vessels drifted, so do not "
             "use it to judge small structures.",
             "3mm の粗い解析（約47秒）。大きな構造の位置をざっと見る用です。"
             "実測では胆嚢が12%小さくなり細い血管もずれたので、小さい構造の判断には使わないでください。"),
}

# 手順の番号ボタン用＝左揃え（先生指示 2026-07-21：数字は左揃え）。toggle でも維持するため変種を持つ。
SS_ON_L = SS_ON + "text-align:left;padding-left:8px;"
SS_OFF_L = SS_OFF + "text-align:left;padding-left:8px;"

_BASE_FONT_PT = None                                          # OS標準の文字サイズ（プロセスで最初に1回だけ測る）


def _base_font_pt():
    """下部の操作帯・フッターを据え置く基準サイズ。

    「保存された文字サイズを適用したあと」に測ると、その値自体が基準になってしまい、
    2つ目以降のウィンドウで下部帯を据え置けない（＝画像が削られる）。プロセスで最初の1回だけ、
    まだ誰も触っていない QApplication の既定サイズを測って覚える。
    """
    global _BASE_FONT_PT
    if _BASE_FONT_PT is None:
        a = QApplication.instance()
        _BASE_FONT_PT = a.font().pointSize() if a is not None else 13
    return _BASE_FONT_PT


class MainWindow(ShellWindow):   # 共通メソッドは shell.py（正本 engine/core、Phase 3c-1）
    def __init__(self):
        super().__init__()
        settings_store.migrate_from_qsettings()              # 旧QSettingsの値を一度だけ取り込む（更新後も設定維持）
        i18n.set_lang(settings_store.store().value("ui_lang", "en"))
        self._i18n = []                                      # (widget/QAction, en, ja) — 言語切替で setText し直す
        self.setWindowTitle(f"TIPS ICE Planner  v{VERSION}  — research / education only")
        self.resize(1320, 920)
        self.vol = None
        self.cz = self.cy = self.cx = 0
        self.wl = core.WL_DEFAULT; self.ww = core.WW_DEFAULT
        self.theta = 180.0; self.b1 = 0.0; self.b2 = 0.0; self.zP = 0.0
        self.lock3 = False; self._lock3 = None      # 3点固定モードと、その残差(mm)
        self._lock3_key = None                       # 最後に「乗せ直し」した時の (点・パス・モード) の署名
        self._lock3_hold = False                     # ロック中に押し引き(経腹:あおり)を手動された＝その軸は術者へ返す
        self.path = []; self.flip = False
        self.hep_veins = []; self.hep_mode = False           # 手動で描いた肝静脈（各々=world mm点列）＋描画モード
        self.vein_overrides = []; self.vein_edit = False     # AI分離の手動再指定（枝ごと門脈⇔肝静脈）＋編集モード
        self._vein_lab = None; self._vein_sel = None         # 門脈/肝静脈ラベル と 選択中の枝（ハイライト用）
        self.step = 0; self.ptMode = 0; self.iceRoll = 0.0
        # 挿入方向の既定＝施設でほぼ固定（設定に永続化・Settingsメニューから変更可）
        self.tipHighZ = (settings_store.store().value("insertion_default", "femoral") != "jugular")
        self.entry = None; self.target = None; self.needleStraight = False
        self.needleAdvance = 90.0                            # 針の前進量(RUPS=throw / Colapinto=弧長)
        self.obs = []; self.predict = False; self.pred_curved = False   # Plot(予習)モード
        self._undo_snapshot = None                           # Undo＝直前の1状態のみ（多段履歴ではない）
        # 実際の針＝Entry(固定の刺入点)→クリックした点(針先)。ICEクリック/ドラッグで針先だけ設定→Targetとの位置関係
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0
        self._dash_phase = 0.0                              # 予測点線(2cm)の流れるアニメーション位相
        self.colaR = 45.0                                   # Colapinto弯曲半径(mm)・選択可。小=鋭く曲がる
        self._ice_geom = None; self._ice_wi = 0; self._ice_hi = 0
        self.liver = None; self.show_liver = True            # 肝臓ゴースト（IVCパス確定で自動計算）
        self.liver_mode = "haze"; self.liver_opacity = 0.5
        self._liver_key = None; self._liver_worker = None
        self.viewMode = "ice"                                # 'ice'(血管内) / 'surface'(経腹エコー)
        self.contact = None; self.normal = None              # 皮膚接触点(world mm) と 体内向き法線(world単位)
        self.surfPlane = 0                                   # プローブを置いたCT断面 0=Axi/1=Cor/2=Sag / -1=3D体表
        self.body = None; self._body_worker = None; self._body_key = None   # 体表シェル(3D・起動時抽出)
        self._ts_worker = None; self._ts_key = None          # TotalSegmentator（有れば読込時に肝/IVC/門脈を抽出）
        self._auto_path_pending = False                      # 「AIでIVCパス自動作成」実行中（AI完了後に軸を引く）
        self.current_study_uid = None; self._current_files = []   # 今開いている患者(検査)＝作業保存の紐付け先
        self._pending_restore = None                              # ロード完了後に流し込む保存済み作業状態
        self._clean_fp = None                                     # 「最後に保存/復元/開いた時」の状態指紋（未保存変更の判定用）
        # 2画面モード（供覧・教育用の"見せ方"切替。4分割のデータ・操作・状態は完全共通）
        # ICE像の見せ方（EUS版と同じ2つ・先生要望 2026-07-20）。設定に永続化＝次回起動でも維持。
        self._base_pt = _base_font_pt()                      # 下部帯を据え置く基準＝OS標準の文字サイズ
        self._font_pt = self._base_pt                         # 現在の文字サイズ（画像上のラベルもこれに追従・_set_font_sizeで更新）
        self.label_offsets = {}                              # (pane, ラベルid)→(dx,dy): 画像上の文字をドラッグで動かした量
        _st0 = settings_store.store()
        self.ct_echo_filter = bool(_st0.value("ice_echo_look", False, type=bool))   # CTをエコー風に加工して表示
        self.show_ice_organs = bool(_st0.value("ice_organs", False, type=bool))     # ICE像にAI構造を薄く重ねる
        self.two_pane = False                                # False=4分割 / True=2画面（左=CT/VR・右=ICE）
        self.two_left = "ax"                                 # 左ペインの中身 'ax'/'cor'/'sag'/'3d'（先生決裁: 初期=Axial）
        self._four_sizes = None                              # 2画面へ入る直前の4分割の枠サイズ（戻す時に完全復元）

        self.ax = ImagePane("Axial  (click = IVC path / Entry-Target)")
        self.cor = ImagePane("Coronal"); self.sag = ImagePane("Sagittal")
        self.ice = ImagePane("ICE view  (wheel = Rotate θ)")
        self.ice.placeholder = "Draw the IVC path on Axial (>= 2 clicks)"
        self.p3d = Pane3D()
        for pane, pl, pk in ((self.ax, 0, "ax"), (self.cor, 1, "cor"), (self.sag, 2, "sag")):
            pane.overlay_fn = lambda p, tw, _pl=pl: self._overlay(p, tw, _pl)
            pane.movePoint.connect(lambda pid, c, r, _pl=pl: self._move_point(pid, c, r, _pl))
            pane.moveLabel.connect(lambda lid, dx, dy, _pk=pk: self._on_move_label(_pk, lid, dx, dy))
        self.ice.overlay_fn = self._ice_overlay
        self.ice.moveLabel.connect(lambda lid, dx, dy: self._on_move_label("ice", lid, dx, dy))
        self.ax.clicked.connect(self._axial_click)
        self.cor.clicked.connect(lambda c, r: self._ortho_click(1, c, r))
        self.sag.clicked.connect(lambda c, r: self._ortho_click(2, c, r))
        self.ice.clicked.connect(self._ice_click)
        self.ice.movePoint.connect(self._move_point_ice)
        for pane in (self.ax, self.cor, self.sag, self.ice):
            pane.wlChanged.connect(self._adjust_wl)
        self.ice.wheelMoved.connect(self._spin_theta)

        self.cAx = PaneCell(self.ax, lambda v: self._scroll_to(0, v))
        self.cCor = PaneCell(self.cor, lambda v: self._scroll_to(1, v))
        self.cSag = PaneCell(self.sag, lambda v: self._scroll_to(2, v))
        self.cIce = PaneCell(self.ice, lambda v: self._probe_to(v))
        self.ax.wheelMoved.connect(lambda d: self._scroll(0, d))
        self.cor.wheelMoved.connect(lambda d: self._scroll(1, d))
        self.sag.wheelMoved.connect(lambda d: self._scroll(2, d))

        self._panes = [self.ax, self.cor, self.sag, self.ice, self.p3d]   # アクティブ枠の連動
        for pn in self._panes:
            pn.activated.connect(self._activate)
        self.ax.active = True                                            # 初期アクティブ画面

        self.iceInfo = QLabel(); self.iceInfo.setWordWrap(True)
        self.iceInfo.setTextFormat(Qt.RichText); self.iceInfo.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.iceInfo.setStyleSheet("background:#0a1622;color:#e2eaf3;padding:4px 8px;border-top:1px solid #24405d;")
        self.iceInfo.setFixedHeight(92)
        iceBox = QWidget(); iv = QVBoxLayout(iceBox); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(0)
        iv.addWidget(self.cIce, 1); iv.addWidget(self.iceInfo)
        self.iceBox = iceBox                                 # 2画面モードが「借りて返す」ために参照を保持

        # 4画面は自由な比率に変えられる（仕切りを掴む／中央の交差点で縦横同時／ダブルクリックで4等分）。
        # 3Dペインとの境目も掴めるようにした（以前は 3:1 固定だった）。
        self.quad = QuadPanes(self.cAx, self.cCor, self.cSag, iceBox)
        self.mainSplit = QSplitter(Qt.Horizontal)
        self.mainSplit.addWidget(self.quad); self.mainSplit.addWidget(self.p3d)
        self.mainSplit.setChildrenCollapsible(False); self.mainSplit.setHandleWidth(6)
        self.mainSplit.setStretchFactor(0, 3); self.mainSplit.setStretchFactor(1, 1)
        center = QWidget()
        cl = QHBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        cl.addWidget(self.mainSplit)
        # 2画面モードの器（普段は完全に隠れている＝既存4分割の構造・挙動には一切影響しない）
        cl.addWidget(self._build_two_pane())
        self.centerWrap = center
        self._init_two_btn(center)                           # 右上に浮かべる「◫ 2画面 ⇄ ⊞ 4分割」切替（縦スペース消費ゼロ）
        center.installEventFilter(self)                      # リサイズのたびに切替ボタンを右上へ置き直す
        # Handle操作パネル＝画像のすぐ下・全幅の横帯（先生指定：右上→画像下の広い帯へ）。ドラッグで操作。
        self.handleCtl = HandleControl()
        self.handleCtl.b1Changed.connect(lambda x: self.sB1.setValue(int(round(x))))
        self.handleCtl.b2Changed.connect(lambda x: self.sB2.setValue(int(round(x))))
        # 3点固定モード中、θ は「解かれる量」なので手では回せない（回してもすぐ上書きされ、
        # 壊れているように見えてしまう）。入口のここで黙って無視する。
        self.handleCtl.thetaChanged.connect(lambda x: None if self.lock3 else self.sTheta.setValue(int(round(x)) % 360))
        self.handleCtl.probeChanged.connect(lambda x: self.sProbe.setValue(int(round(x))) if self.sProbe.isEnabled() else None)
        # 経腹（体表）モード専用パネル＝同じ場所でHandleControlと表示を切り替える（先生指定2026-07-14）。
        self.surfCtl = SurfaceProbeControl()
        self.surfCtl.b1Changed.connect(lambda x: self.sB1.setValue(int(round(x))))
        self.surfCtl.b2Changed.connect(lambda x: self.sB2.setValue(int(round(x))))
        self.surfCtl.thetaChanged.connect(lambda x: self.sTheta.setValue(int(round(x)) % 360))

        root = QVBoxLayout(); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(center, 1)
        root.addWidget(self._bottom_strip())     # 凡例(左)＋Classic⇔Handle(中央)＋ロール/肝臓(右)を1帯に集約＝画像を最大化
        root.addWidget(self._footer())           # 左:Step2針操作(畳み可) / 右:研究・教育用の注記＝1帯に集約
        self.viewer_page = QWidget(); self.viewer_page.setLayout(root)

        self._dashTimer = QTimer(self); self._dashTimer.timeout.connect(self._tick_dash); self._dashTimer.start(60)

        import catalog as catmod
        from database_view import DatabaseView
        self.catalog = catmod.Catalog()
        self.db = DatabaseView(self.catalog)
        self.db.openSeries.connect(self._open_series_files)
        self.db.restoreSession.connect(self._restore_session_from_db)
        self.db.sessionDeleted.connect(self._on_session_deleted)   # 患者リストで削除→ビューアのボタン更新
        self.db.langToggled.connect(self._toggle_lang)
        self.p3d.surfacePicked.connect(self._pick_surface_3d)   # 3D体表ドラッグ→プローブ設置
        self.p3d.vesselBtn.clicked.connect(self._on_vessel_ai)  # 3Dパネル「構造AI」ボタン→生成/表示切替
        # ⟳(再描出)は手順グループ（下部帯の「自動構造描出」の隣）へ移設（先生指示 2026-07-18）。
        # Pane3D 側のボタンは互換のため残すが非表示（resizeEvent が move しても hidden のまま）。
        self.p3d.rerenderBtn.hide()
        self._update_vessel_btn()                               # 起動時は無効（CT未読込）
        self._build_organ_legend()                              # 3D右上『描出構造』＝AI臓器の選択（折りたたみ既定）
        self._init_ice_chips()                                  # ICE画像右上『エコー風 / 構造』＝表示領域を削らない
        self.stack = QStackedWidget(); self.stack.addWidget(self.db); self.stack.addWidget(self.viewer_page)
        self.setCentralWidget(self.stack); self.stack.setCurrentWidget(self.db)
        self.setStyleSheet(STYLE)
        self._build_menu()
        self._update_step_ui()
        self._apply_language()                               # 保存済み言語でUI文字列を確定（EN/JA）
        self._ensure_sample_async()                          # 配布アプリに公開サンプルCT(HCC048)を常に1例入れておく
        QApplication.instance().aboutToQuit.connect(self._shutdown)   # Cmd+Q は closeEvent を通らない

    # ---------- 下部コントロール ----------
    def _grp(self, text, ja=None):
        """複合帯グループの小見出し（10px固定＝『手順/エコーの文字が無駄に大きい』先生指摘 2026-07-18）"""
        l = self._acc(text, ja=ja); l.setAlignment(Qt.AlignCenter)
        l.setStyleSheet("color:#F08F69;font-size:10px;")
        return l

    def _controls(self):
        """Step2(穿刺針)専用の操作。患者リスト/DICOMを開く/言語/挿入方向は上部メニューバーに集約済み。
        Step/Echo/保存/クリアは _bottom_strip() 側（画像直下の複合帯）に置く。
        戻り値のneedleRowWは _footer() の左側（注記の左・同じ高さ）に配置され、専用の行を持たない
        （Step1では畳んで0幅＝CT/ICE画像を圧迫しない）。"""
        rN = QHBoxLayout(); rN.setContentsMargins(0, 0, 0, 0)
        # Entry/Target/実際の針先は手順列（_step_group）へ引き上げ済み（先生指示 2026-07-21）。
        # ここは封印中の Plot/曲げ系の内部保持だけ（非表示・状態と _update_step_ui が参照するため温存）。
        self.lblSet = self._lbl("Set:", "設定:")
        self.needleTypeBtn = self._btn("Needle: Colapinto", self._toggle_needletype)
        self.lblAdv = self._acc("Advance"); self.sAdvance = self._slider(20, 160, 90, self._set_advance, 100)
        self.advVal = QLabel("90 mm")
        self.lblCurve = self._acc("Colapinto curve"); self.sCurve = self._slider(25, 90, int(self.colaR), self._set_colaR, 100)
        self.curveVal = QLabel(f"R {int(self.colaR)} mm")
        self.plotBtn = self._btn("Plot tip", self._toggle_plot)
        self.predNeedleBtn = self._btn("Pred: RUPS", self._toggle_predneedle)
        for w in (self.lblSet, self.needleTypeBtn,
                  self.lblAdv, self.sAdvance, self.advVal, self.lblCurve, self.sCurve, self.curveVal,
                  self.plotBtn, self.predNeedleBtn):
            rN.addWidget(w)
        rN.addStretch(1)
        self.needleRowW = QWidget(); self.needleRowW.setLayout(rN)   # 中身は封印・常に畳んだ状態＝画像を圧迫しない
        self.lblSet.setVisible(False)                                # 「設定:」ラベルは孤立するので隠す（項目は手順列へ移設）
        return self.needleRowW

    def _bottom_strip(self):
        """画像直下の複合帯＝凡例(縦)/手順/エコー/Handle操作/ロール・肝臓/保存/クリアを1本に集約。
        患者リスト・DICOMを開く・言語・挿入方向は上部メニューバーへ移設済み（施設固定・毎回操作しないため）。
        従来の複数行(Row1+ハンドル帯+ロール行)を1本の帯に統合し、縦方向を節約してCT/ICE表示を最大化。
        操作方式はHandle（絵をドラッグ）に一本化済み（旧Classic=スライダー表示は廃止・先生指示2026-07-14）。
        ただしθ/偏向/プローブ位置のスライダー(sTheta/sB1/sB2/sProbe)自体は、Handle操作の値が実際に
        流れ込む先＝状態の一次保持先として内部的に温存する（表示はしない・_restore_state等が直接参照するため）。"""
        strip = QWidget(); strip.setStyleSheet("background:#14253a;")
        self.bottomStrip = strip                             # 高さ番兵テスト(test_bottom_ui_height_budget)が参照
        # 非表示ウィジェットの受け皿。親を持たないウィジェットはトップレベル窓になるので、
        # 温存する内部ウィジェット（スライダ・旧step1/2ボタン等）はこの隠し親にぶら下げる。
        # _step_group() より前に作る（step群が step1/step2 をここへ退避するため）。
        self._vestigial = QWidget(self); self._vestigial.hide()
        h = QHBoxLayout(strip); h.setContentsMargins(8, 2, 8, 2); h.setSpacing(8)
        self.gbar = GestureBar(vertical=True)
        # 先生指示 2026-07-21 の並び：凡例(拡大/階調/移動) → エコー(ICE/経腹・縦大) → 手順(番号列)
        h.addWidget(self.gbar); h.addWidget(_sep())
        h.addWidget(self._echo_group()); h.addWidget(_sep())
        h.addWidget(self._step_group()); h.addWidget(_sep())
        # --- 内部状態保持用ウィジェット（非表示。Handle操作の値の一次保持先／状態保存・復元・
        #     経腹⇔血管内のラベル読み替え(_update_mode_ui)が直接参照するため温存） ---
        self.sTheta = self._slider(0, 360, int(self.theta), self._set_theta)
        self.sProbe = self._slider(0, 100, 50, self._set_probe); self.sProbe.setEnabled(False)
        self.sB1 = self._slider(-80, 80, 0, self._set_b1); self.sB2 = self._slider(-80, 80, 0, self._set_b2)
        self.b1Val = QLabel("+0°"); self.b2Val = QLabel("+0°")   # _refresh()が更新（Handle上の度数表示は別描画）
        self.lblTheta = self._acc("Rotate θ")
        self.lblProbe = self._acc("Probe", ja="プローブ前後")
        self.probeFoot = self._lbl("foot", "足側"); self.probeHead = self._lbl("head", "頭側")
        self.lblAP = self._acc("Deflect A/P"); self.lblLR = self._acc("Deflect L/R")
        # 上の12個はどのレイアウトにも入れない。Qt では **親を持たないウィジェットはトップレベル
        # ウィンドウになる** ので、_update_mode_ui() の setVisible(True) がそのままデスクトップに
        # 小さな窓（"foot" / "head" / "Probe" / スライダ）を出していた。非表示の親に付けて封じる。
        # （_vestigial は _bottom_strip 冒頭で生成済み。step群が step1/2 を先に退避するため。）
        for _w in (self.sTheta, self.sProbe, self.sB1, self.sB2, self.b1Val, self.b2Val,
                   self.lblTheta, self.lblProbe, self.probeFoot, self.probeHead,
                   self.lblAP, self.lblLR):
            _w.setParent(self._vestigial)
        # ICEのモック（AcuNavハンドル）の**すぐ下**に3点固定モードのスイッチを置く（先生指定 2026-07-15）
        self.lock3Btn = self._btn("◎ 3-point lock: OFF", self._toggle_lock3, checkable=True,
                                  ja="◎ 3点固定: OFF")
        self.lock3Btn.setToolTip(L("Keep Entry and Target on the echo image plane by solving the rotation θ "
                                   "automatically — works in both intravascular ICE and transabdominal echo.",
                                   "回転θを自動で解いて Entry と Target を エコー画像面に乗せ続けます"
                                   "（血管内ICE・経腹エコーの両方で使えます）。"))
        self.handleBox = QWidget()
        hbv = QVBoxLayout(self.handleBox); hbv.setContentsMargins(0, 0, 0, 0); hbv.setSpacing(3)
        hbv.addWidget(self.handleCtl, 1)
        # ICE用(ハンドル)と経腹用(プローブ)を QStackedWidget で重ねる。並べて片方を隠す作りだと
        # 高さが変わって4画面が飛び跳ねる。スタックは常に最も高いページぶんの高さを確保するので不変。
        self.ctlStack = QStackedWidget()
        self.ctlStack.addWidget(self.handleBox)              # index 0 = 血管内ICE
        self.ctlStack.addWidget(self.surfCtl)                # index 1 = 経腹
        # 3点固定スイッチは stack の *外・下* に置く＝ICEでも経腹でも常に見える（経腹で消えていた件の修正）。
        # 回旋ハブは footer から移設し ICE ハンドルの右隣へ（footer の縦4段→画像領域に返す・2026-07-18）。
        # 偏向ゼロも 3点固定の隣へ集約＝ハンドル操作系が1ブロックにまとまり、右側の孤立ボタンを解消。
        self.torqueGroup, self.torqueLBtn, self.torqueRBtn, self.hubWidget = self._torque_group()
        ctlBox = QWidget()
        cbv = QVBoxLayout(ctlBox); cbv.setContentsMargins(0, 0, 0, 0); cbv.setSpacing(2)
        ctlRow = QHBoxLayout(); ctlRow.setSpacing(4)
        ctlRow.addWidget(self.ctlStack, 1)
        ctlRow.addWidget(self.torqueGroup, 0, Qt.AlignVCenter)
        cbv.addLayout(ctlRow, 1)
        lockRow = QHBoxLayout(); lockRow.setSpacing(8)
        lockRow.addStretch(1); lockRow.addWidget(self.lock3Btn)
        lockRow.addWidget(self._btn("Zero deflect", self._zero_defl, ja="偏向ゼロ")); lockRow.addStretch(1)
        cbv.addLayout(lockRow)
        h.addWidget(ctlBox, 2)
        h.addWidget(_sep())
        # --- 右ブロック: ロール／反転＋肝臓ゴースト（2行・常時表示） ---
        # ロール枠＝ロールと ICE左右反転のみ縦2段（先生指示 2026-07-18 第3版：他は削除して幅をハンドルへ）
        rl = QWidget(); g = QVBoxLayout(rl); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(2)
        r1 = QHBoxLayout(); r1.setSpacing(3)
        r1.addWidget(self._acc("Roll", ja="ロール")); self.sRoll = self._slider(-180, 180, 0, self._set_roll, 56)
        r1.addWidget(self.sRoll); self.rollVal = QLabel("0°"); r1.addWidget(self.rollVal)
        g.addLayout(r1)
        g.addWidget(self._btn("Flip ICE L/R", self._toggle_flip, ja="ICE左右反転"))
        g.addStretch(1)
        # 肝臓/肝臓:表示モード/不透明度/ロール0 は UI 撤去（先生指示）。状態保存・復元等が参照するため
        # 実体は非表示親(_vestigial)に温存する（親なしはトップレベル窓になる罠・上記コメント参照）。
        self.liverBtn = self._btn("Liver", self._toggle_liver, checkable=True, ja="肝臓"); self.liverBtn.setChecked(True)
        self.liverModeBtn = self._btn("Liver: Haze", self._toggle_liver_mode)
        self.sLiverOp = self._slider(10, 90, int(self.liver_opacity * 100), self._set_liver_opacity, 56)
        for _w in (self.liverBtn, self.liverModeBtn, self.sLiverOp):
            _w.setParent(self._vestigial)
        h.addWidget(rl); h.addWidget(_sep())
        # 手動肝静脈は縦グループ（横に4つ並べると ICEハンドルの幅を圧迫したため・先生指示 2026-07-15）
        h.addWidget(self._hep_group()); h.addWidget(_sep())
        h.addWidget(self._undo_group()); h.addWidget(self._save_group()); h.addWidget(self._clear_group())
        return strip

    def _hep_group(self):
        """手動肝静脈の操作を縦に並べたグループ（描く / ＋新しい血管 / 完了 / 消去）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._grp("Hepatic vein", ja="肝静脈"); v.addWidget(lbl)
        self.hepBtn = self._btn("Draw ✎", self._toggle_hep_mode, checkable=True, ja="描く ✎")
        self.hepBtn.setToolTip(L("Draw hepatic veins by hand on the CT (for cirrhotic livers the AI often misses them). "
                                 "Click along the vein on any pane; press Finish to smooth it.",
                                 "AIで写りにくい肝静脈をCT上で手描き（肝硬変では特に）。どの断面でも血管に沿ってクリック、"
                                 "「完了」で滑らかに整えます。"))
        self.hepNewBtn = self._btn("+ vein", self._hep_new_vein, ja="＋新しい血管")
        self.hepDoneBtn = self._btn("Finish ✓", self._hep_finish, ja="完了 ✓")
        self.hepDoneBtn.setToolTip(L("Finish drawing — dots disappear and the vein is redrawn as a smooth curve.",
                                     "描画を終了します。点が消え、なだらかな血管曲線に描き直されます。"))
        self.hepClrBtn = self._btn("Clear", self._clear_hep, ja="消去")
        # AI分離(門脈⇔肝静脈)の手動再指定。枝をクリックで切替＝分離ミスをその場で訂正（先生要望 2026-07-21）
        self.veinEditBtn = self._btn("Fix P/HV ✓", self._toggle_vein_edit, checkable=True, ja="門脈/肝静脈 訂正")
        self.veinEditBtn.setToolTip(L(
            "Correct the AI's portal/hepatic split: click a branch on any CT pane to flip it "
            "between portal (blue) and hepatic (rose). Click again to undo. Reset with the Clear column.",
            "AIの門脈/肝静脈の分けを訂正: 断面で枝をクリックすると門脈（青）⇔肝静脈（ローズ）が切替。"
            "もう一度で元に戻ります。右クリックで訂正を全部取り消し。"))
        from PySide6.QtCore import Qt as _Qt
        self.veinEditBtn.setContextMenuPolicy(_Qt.CustomContextMenu)
        self.veinEditBtn.customContextMenuRequested.connect(lambda _p: self._clear_vein_overrides())
        for b in (self.hepBtn, self.hepNewBtn, self.hepDoneBtn, self.hepClrBtn, self.veinEditBtn):
            b.setMaximumHeight(20); v.addWidget(b)
        v.addStretch(1)   # 縦連＝横幅を節約しハンドル帯へ譲る（先生指示 2026-07-18・2026-07-15 の縦指定にも復帰）
        return box

    def _step_group(self):
        """手順を縦に並べたグループ（エコーの右）。先生指示 2026-07-21 の並び：
          1. 自動構造抽出（構造AI＋⟳）／2. ICEトラクト自動抽出／3. 穿刺設定
          （一段さげて）穿刺点 → ターゲット → 実際の針先。
        穿刺点/ターゲット/実際の針先はもと footer にあった設定行を、この手順列の下段へ引き上げた。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._grp("Step", ja="手順"); v.addWidget(lbl)
        # 1. 自動構造抽出（workflow の最初）。⟳（再描出）は縦を増やさないよう同じ行の右に置く
        self.vesselFlowBtn = self._btn("1. 🧠 Auto structures", self._on_vessel_ai, ja="1. 🧠 自動構造抽出")
        self.vesselFlowBtn.setToolTip(L(
            "Run the structure AI (liver / IVC / portal vein / hepatic vessel tree, plus the organs you "
            "picked). First click analyzes; afterwards it toggles the 3D display on/off.",
            "AIで肝臓・IVC・門脈・肝血管ツリー（＋選んだ臓器）を描出します。初回は解析を実行し、"
            "以後は3D表示のON/OFFを切り替えます。"))
        self.rerenderFlowBtn = QPushButton("⟳"); self.rerenderFlowBtn.setFixedWidth(24)
        self.rerenderFlowBtn.clicked.connect(self._rerender_ts)
        self.rerenderFlowBtn.setToolTip(L(
            "Re-render the AI view with the current settings (rebuild from cache; re-run AI only if needed).",
            "今の設定でAI表示を作り直します（キャッシュから再構築・必要な時だけAI再解析）。"))
        vrw = QWidget(); vr = QHBoxLayout(vrw); vr.setContentsMargins(0, 0, 0, 0); vr.setSpacing(2)
        vr.addWidget(self.vesselFlowBtn, 1); vr.addWidget(self.rerenderFlowBtn)
        # 2. ICEトラクト自動抽出（構造AIのIVC検出→ICE軸を自動生成）
        self.autoPathBtn = self._btn("2. ⚡ Auto ICE tract", self._auto_ivc_path, ja="2. ⚡ ICEトラクト自動抽出")
        self.autoPathBtn.setToolTip(L(
            "Auto-draw the ICE tract (IVC path = the ICE axis) from the AI-detected IVC — a draft you can drag to "
            "adjust. Clear it from the Clear column (IVC path) if it looks off. Runs the structure AI first if needed.",
            "AIが検出したIVCから、ICEのトラクト（IVCパス＝ICEの軸）を自動で引きます。下書きなので点をドラッグで"
            "微調整できます。イマイチなら右の『クリア→IVCパス』で消せます。AI未実行なら先に構造AIを実行します。"))
        # 3. 穿刺設定＝トグル。ON で穿刺モード（穿刺点/ターゲットが有効）、OFF で ICEセットアップ（パスを描く）
        self.punctureBtn = self._btn("3. Puncture setup", self._toggle_puncture, checkable=True, ja="3. 穿刺設定")
        self.punctureBtn.setToolTip(L(
            "Enter puncture setup: enables the puncture point and target below. Turn off to go back to "
            "ICE setup (draw / adjust the tract).",
            "穿刺設定に入ります（下の穿刺点・ターゲットが有効に）。OFF にすると ICEセットアップ"
            "（トラクトを描く/調整する）に戻ります。"))
        # 互換のため step1/step2 ボタンは内部に温存（テスト・状態が参照）。UI には出さず punctureBtn で切替。
        self.step1Btn = self._btn("1. ICE setup", lambda: self._set_step(0), ja="1. ICEセットアップ")
        self.step2Btn = self._btn("2. Needle", lambda: self._set_step(1), ja="2. 穿刺針")
        for _b in (self.step1Btn, self.step2Btn):
            _b.setParent(self._vestigial); _b.hide()
        # 番号ボタン(1./2./3.)は左揃え（先生指示 2026-07-21）。autoPathBtn は非トグルなので直接、
        # vesselFlowBtn/punctureBtn は toggle 側(_update_vessel_btn/_update_step_ui)で _L 変種を使う。
        self.vesselFlowBtn.setStyleSheet(SS_OFF_L)
        self.autoPathBtn.setStyleSheet(SS_OFF_L)
        self.punctureBtn.setStyleSheet(SS_OFF_L)
        for b in (vrw, self.autoPathBtn, self.punctureBtn):
            b.setMaximumHeight(20); v.addWidget(b)
        # 一段さげて＋インデントを一つ下げて：穿刺点 → ターゲット → 実際の針先（先生指示 2026-07-21）
        v.addSpacing(6)
        self.entryBtn = self._btn("Entry (puncture)", lambda: self._set_ptmode(0), ja="穿刺点")
        self.targetBtn = self._btn("Target", lambda: self._set_ptmode(1), ja="ターゲット")
        self.aimBtn = self._btn("Actual tip (click ICE)", self._toggle_aim, checkable=True, ja="実際の針先（ICE）")
        indent = QWidget(); iv = QVBoxLayout(indent); iv.setContentsMargins(14, 0, 0, 0); iv.setSpacing(1)
        for b in (self.entryBtn, self.targetBtn, self.aimBtn):
            b.setMaximumHeight(20); iv.addWidget(b)         # 左マージン14px＝一段インデント
        v.addWidget(indent)
        v.addStretch(1)   # 上詰め＝他グループとラベル高さを揃える
        return box

    def _toggle_puncture(self):
        """穿刺設定トグル：ON=穿刺モード(step1) / OFF=ICEセットアップ(step0)。"""
        self._set_step(1 if self.punctureBtn.isChecked() else 0)

    def _echo_group(self):
        """エコーモード(血管内ICE/経腹)を縦に並べたグループ。凡例(拡大/階調/移動)のすぐ右に置き、
        少し大きめのアイコン付きボタンで見やすくする（先生指示 2026-07-21）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(3)
        lbl = self._grp("Echo", ja="エコー"); v.addWidget(lbl)
        self.iceBtn = self._btn("◔ ICE", lambda: self._set_viewmode("ice"), ja="◔ 血管内ICE")
        self.iceBtn.setToolTip(L("Intravascular ICE (a virtual ICE probe inside the IVC).",
                                 "血管内ICE（IVC内の仮想ICEプローブ）。"))
        self.surfBtn = self._btn("⌒ Surface", lambda: self._set_viewmode("surface"), ja="⌒ 経腹エコー")
        self.surfBtn.setToolTip(L("Transabdominal echo (a convex probe on the skin).",
                                  "経腹エコー（体表のコンベックスプローブ）。"))
        for b in (self.iceBtn, self.surfBtn):
            b.setMinimumHeight(30); b.setMaximumHeight(30)   # 少し大きめ＝手順ボタン(20px)より目立つ
            b.setStyleSheet("text-align:left; padding-left:8px; font-size:12px;")
            v.addWidget(b)
        v.addStretch(1)
        return box

    def _sync_handle(self):
        """現在の θ/b1/b2/probe を、今表示中の操作ウィジェットの絵へ反映（スライダー操作や状態復元と同期）。
        経腹（体表）モードではSurfaceProbeControl、それ以外ではHandleControlを更新する。"""
        if not hasattr(self, "handleCtl"):
            return
        if self.viewMode == "surface":
            self.surfCtl.set_state(self.b1, self.b2, self.theta)
            return
        path = getattr(self, "path", []); pf = 50.0; en = False
        if len(path) >= 2:
            zs = [p[0] for p in path]; rng = max(zs) - min(zs)
            pf = 50.0 if rng <= 0 else (self.zP - min(zs)) / rng * 100.0; en = True
        self.handleCtl.set_state(self.b1, self.b2, self.theta, pf, en)

    def _torque_group(self):
        """『実際の針』の回旋操作＝縦2段の小型クラスタ：タイトル／(↺左・手元ハブの絵・右↻・⌂0°)。
        絵は『術者から先端側を見た手元』＝L/R は術者の左右。↻右で術者の右へカーブが向く。
        説明文はツールチップへ退避し、ICEハンドルの隣に収まる高さにする（2026-07-18 圧縮）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(4, 0, 4, 0); v.setSpacing(1)
        title = QLabel(); title.setStyleSheet("color:#F08F69;font-weight:bold;font-size:11px;")
        title.setAlignment(Qt.AlignCenter)
        self._reg(title, "Cannula — Rotate", "カニューラ回旋")
        tip = L("Stiffening cannula handle, as the operator sees it (looking toward the tip). "
                "L / R are the operator's left / right; ↻ Right turns the curve to the operator's right. "
                "⌂ = home (0°). Please confirm the direction against the actual device markings.",
                "スタイフニングカニューラの手元（術者から先端側を見た向き）です。L／R は術者の左右で、"
                "↻右でカーブが術者の右へ向きます。⌂＝初期位置（0°）。"
                "回す向きの実機刻印との一致は最終的に術者がご確認ください。")
        title.setToolTip(tip); box.setToolTip(tip)
        # タイトルは絵の左横（上に積むと縦が増え『縦はこれ以上広げない』ルールに触れるため）
        row = QHBoxLayout(); row.setSpacing(4)
        row.addWidget(title)
        lbtn = self._btn("↺ Left", lambda: self._nudge_torque(-15), ja="↺ 左")
        hub = CannulaHubWidget(scale=1.25); hub.setToolTip(tip)   # 拡大（先生指示 2026-07-18）
        rbtn = self._btn("Right ↻", lambda: self._nudge_torque(15), ja="右 ↻")
        homeBtn = self._btn("⌂ 0°", self._reset_torque)
        homeBtn.setToolTip(L("Return the stiffening cannula to its home rotation (0°).",
                             "スティフニングカニューラを初期位置（0°）に戻します。"))
        for w in (lbtn, hub, rbtn, homeBtn):
            row.addWidget(w)
        v.addLayout(row)
        return box, lbtn, rbtn, hub

    def _clear_group(self):
        """消去4種を縦に並べたグループ（Open DICOMの左）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._grp("Clear", ja="クリア"); v.addWidget(lbl)
        self.btnClearPath = self._btn("IVC path", self._clear_path, ja="IVCパス")
        self.btnClearNeedle = self._btn("Needle", self._clear_needle, ja="針")
        self.btnClearPlots = self._btn("Plots", self._clear_plots, ja="プロット")
        self.btnClearAll = self._btn("All", self._clear_all, ja="すべて")
        for b in (self.btnClearPath, self.btnClearNeedle, self.btnClearPlots, self.btnClearAll):
            b.setMaximumHeight(20); v.addWidget(b)
        v.addStretch(1)   # 縦4連＝横幅を節約しハンドル帯へ譲る（先生指示 2026-07-18）
        return box

    def _undo_group(self):
        """直前の1手だけ戻すボタン（Saveの左）。クリック/点の設定やClear系・偏向ゼロ・ロール0・
        挿入方向の変更を対象とする（スライダー/Handleのドラッグ操作は対象外）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._grp("Undo", ja="元に戻す"); v.addWidget(lbl)
        tip = L("Undo the last click/point-set or Clear/Zero/Roll-0/insertion change (one step back). "
                "Slider and Handle drags are not covered.",
                "直前のクリック・点の設定や、クリア／偏向ゼロ／ロール0／挿入方向の変更を1つだけ元に戻します"
                "（スライダー／Handleのドラッグ操作は対象外）。")
        self.undoBtn = self._btn("↺ Undo", self._undo, ja="↺ 元に戻す")
        self.undoBtn.setEnabled(False); self.undoBtn.setStyleSheet(SS_OFF); self.undoBtn.setMaximumHeight(20)
        self.undoBtn.setToolTip(tip); box.setToolTip(tip)
        v.addWidget(self.undoBtn); v.addStretch(1)
        return box

    def _save_group(self):
        """作業状態の保存スロット1/2/3を縦に並べたグループ（Clearの左）。患者ごとに独立して保存される
        （同じ「1」でも患者が違えば別物）。右クリックでそのスロットを削除できる。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._grp("Save state", ja="状態保存"); v.addWidget(lbl)
        group_tip = L("Save the current IVC path, Entry/Target, actual needle tip, and view settings "
                      "(rotation/deflection/echo mode) — up to 3 slots for THIS patient only "
                      "(other patients have their own separate 1/2/3). "
                      "Go to the patient list and use the Restore buttons on that patient's row to bring "
                      "a saved slot back. Right-click a slot to delete it.",
                      "今のIVCパス・Entry/Target・実際の針先・表示設定（回転/偏向/エコーモード）を、"
                      "この患者専用に保存します（最大3つ。他の患者にはそれぞれ別の1/2/3があります）。"
                      "呼び戻すときは患者リスト画面の、その患者の行にある「復元」ボタンを使います。"
                      "右クリックでそのスロットを削除できます。")
        lbl.setToolTip(group_tip); box.setToolTip(group_tip)
        self.saveBtns = []
        for i in (1, 2, 3):
            b = self._btn(str(i), lambda _checked=False, n=i: self._save_slot(n))
            b.setMaximumHeight(20); b.setMaximumWidth(30)
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, n=i: self._delete_slot(n))
            v.addWidget(b); self.saveBtns.append(b)
        v.addStretch(1)   # 縦3連＝横幅を節約しハンドル帯へ譲る（先生指示 2026-07-18）
        return box

    def _clear_all(self):
        self._snap_undo()
        self.path = []; self.sProbe.setEnabled(False)
        self.entry = self.target = None; self.ptMode = 0; self.obs = []
        self.hep_veins = []; self.hep_mode = False; self._sync_hep_ui()   # 手動肝静脈も消去
        self.vein_overrides = []; self.vein_edit = False; self._sync_vein_ui()   # 血管の手動再指定も取消
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0
        self.contact = None; self.normal = None
        self.liver = None; self._liver_key = None; self.p3d.liver = None
        self._update_step_ui(); self._refresh()

    def _reset_all_views(self):
        """どれかのペインで画像が『飛んだ』時の一発復旧＝全ペインのズーム/パンを戻し、
        断面(Axial/Coronal/Sagittal)の位置を Entry→Target→体積中心 の順で解剖上に戻す。"""
        for pane in (self.ax, self.cor, self.sag, self.ice):
            pane.reset_view()
        self.label_offsets = {}                              # ドラッグして動かした文字ラベルも既定位置へ戻す
        self.p3d.zoom3d = 1.0; self.p3d.pan3d = QPointF(0, 0)
        if self.vol is not None:
            nz, H, W = self.vol.shape
            ref = self.entry if self.entry is not None else self.target
            if ref is not None:                              # Entry/Target(world mm)を index に戻して十字を合わせる
                self.cx = int(np.clip(round(ref[0] / self.vol.sx), 0, W - 1))
                self.cy = int(np.clip(round(ref[1] / self.vol.sy), 0, H - 1))
                self.cz = int(np.clip(round(ref[2] / self.vol.dz), 0, nz - 1))
            else:                                            # 未設定なら体積の中央
                self.cz, self.cy, self.cx = nz // 2, H // 2, W // 2
        self._refresh()
        self.statusBar().showMessage(L("Views reset (panes re-fit, slices re-centred on the anatomy).",
                                       "表示をリセットしました（各画像を初期倍率に戻し、断面を解剖に合わせました）。"), 4000)

    # ---------- 2画面モード（供覧・教育用の"見せ方"。4分割は一切作り替えない・2026-07-18 先生決裁） ----------
    def _build_two_pane(self):
        """左=CT(Axial/Coronal/Sagittal)またはVR(3D)／右=ICE(固定) の2画面の器を作る（初期は非表示）。
        既存ペインは作り直さず、2画面中だけ該当2枚をこの器へ「借りて」大きく見せ、
        4分割へ戻す時は元のスロットへ返す（データ・操作・保存は完全共通）。
        枠色は先生決裁: CT/VR=テラコッタ・ICE=シアン。チップは将来のiPadを見据えワンタップ切替。"""
        chips = QHBoxLayout(); chips.setContentsMargins(6, 4, 6, 2); chips.setSpacing(6)
        self.twoChips = {}
        for key, en, ja in (("ax", "Axial", "Axial"), ("cor", "Coronal", "Coronal"),
                            ("sag", "Sagittal", "Sagittal"), ("3d", "VR (3D)", "VR（3D）")):
            b = QPushButton(); self._reg(b, en, ja)
            b.setFocusPolicy(Qt.NoFocus); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._set_two_left(k))
            self.twoChips[key] = b; chips.addWidget(b)
        chips.addStretch(1)
        self.twoLeftSlot = QVBoxLayout(); self.twoLeftSlot.setContentsMargins(0, 0, 0, 0)
        lv = QVBoxLayout(); lv.setContentsMargins(3, 3, 3, 3); lv.setSpacing(2)
        lv.addLayout(chips); lv.addLayout(self.twoLeftSlot, 1)
        self.twoLeftFrame = QFrame(); self.twoLeftFrame.setObjectName("twoLeft")
        self.twoLeftFrame.setLayout(lv)
        self.twoLeftFrame.setStyleSheet("QFrame#twoLeft{border:2px solid #F08F69;border-radius:6px;}")
        self.twoRightSlot = QVBoxLayout(); self.twoRightSlot.setContentsMargins(3, 3, 3, 3)
        self.twoRightFrame = QFrame(); self.twoRightFrame.setObjectName("twoRight")
        self.twoRightFrame.setLayout(self.twoRightSlot)
        self.twoRightFrame.setStyleSheet("QFrame#twoRight{border:2px solid #3fc6e0;border-radius:6px;}")
        self.twoSplit = QSplitter(Qt.Horizontal)
        self.twoSplit.addWidget(self.twoLeftFrame); self.twoSplit.addWidget(self.twoRightFrame)
        self.twoSplit.setChildrenCollapsible(False); self.twoSplit.setHandleWidth(6)
        self.twoWrap = QWidget()
        tw = QHBoxLayout(self.twoWrap); tw.setContentsMargins(0, 0, 0, 0); tw.addWidget(self.twoSplit)
        self.twoWrap.hide()
        return self.twoWrap

    def _init_two_btn(self, parent):
        """4分割⇄2画面の切替ボタン。上部バーは廃止済みのため、画像領域の右上に浮かべて置く
        （構造AIボタンと同じ流儀＝縦スペースを一切消費しない。先生決裁: 上部・右・常時表示）。"""
        self.twoBtn = QPushButton(parent)
        self.twoBtn.setFocusPolicy(Qt.NoFocus); self.twoBtn.setCursor(Qt.PointingHandCursor)
        self.twoBtn.setStyleSheet(
            "QPushButton{background:rgba(27,49,74,235);color:#e2eaf3;border:1px solid #F08F69;"
            "border-radius:6px;padding:3px 10px;font-weight:bold;}"
            "QPushButton:hover{background:#2b4762;}")
        self.twoBtn.clicked.connect(self._toggle_two_pane)
        self._update_two_pane_ui()

    # ---------- ICE像の見せ方トグル（エコー風 / ICEに構造）・EUS版と同じ機能 ----------
    def _init_ice_chips(self):
        """『エコー風』『構造』を **ICE画像の右上に浮かべる**（先生指示 2026-07-20「画像が小さくならないように」）。

        下部帯に置くと縦か横を必ず食う（下部UIの高さは番兵テストで固定、横はICEハンドルを痩せさせる）。
        構造AI・2画面ボタンと同じオーバーレイ流儀なら**表示領域をまったく削らない**うえ、
        効き先（ICE像）の真上にあるので何に効くボタンか一目で分かる。
        """
        self.echoLookBtn = QPushButton(self.ice); self.iceOrgBtn = QPushButton(self.ice)
        for b in (self.echoLookBtn, self.iceOrgBtn):
            b.setCheckable(True); b.setFocusPolicy(Qt.NoFocus); b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:rgba(16,32,52,225);color:#cfe0ef;border:1px solid #43658a;"
                "border-radius:5px;padding:1px 7px;font-size:10px;}"
                "QPushButton:checked{background:#F08F69;color:#15263a;border:1px solid #ffd0bb;font-weight:bold;}"
                "QPushButton:hover{border:1px solid #7fb0dd;}")
        self.echoLookBtn.setChecked(self.ct_echo_filter)
        self.iceOrgBtn.setChecked(self.show_ice_organs)
        self.echoLookBtn.clicked.connect(self._toggle_echo_look)
        self.iceOrgBtn.clicked.connect(self._toggle_ice_organs)
        self.ice.installEventFilter(self)                     # リサイズのたびに右上へ置き直す
        self._update_ice_chip_ui()

    def _place_ice_chips(self):
        if not hasattr(self, "echoLookBtn"):
            return
        for b in (self.echoLookBtn, self.iceOrgBtn):
            b.adjustSize()
        x = self.ice.width() - self.iceOrgBtn.width() - 6
        self.iceOrgBtn.move(max(0, x), 4); self.iceOrgBtn.raise_()
        self.echoLookBtn.move(max(0, x - self.echoLookBtn.width() - 4), 4); self.echoLookBtn.raise_()

    def _update_ice_chip_ui(self):
        """チップの文言・ツールチップを今の言語と状態に合わせる。"""
        if not hasattr(self, "echoLookBtn"):
            return
        self.echoLookBtn.setText(L("Echo look", "エコー風"))
        self.echoLookBtn.setToolTip(L(
            "Show the ICE image with an ultrasound-like look (display only — geometry and distances are unchanged).",
            "ICE像をエコー風の見た目にします（表示だけ・幾何や距離の計算は変わりません）。"))
        self.iceOrgBtn.setText(L("Structures", "構造"))
        self.iceOrgBtn.setToolTip(L(
            "Overlay the AI structures that lie near this ICE plane, in their 3D colours "
            "(choose which ones in the “Structures” list on the 3D panel).",
            "この ICE 断面の近くにあるAI構造を、3Dと同じ色で薄く重ねます"
            "（どの構造を出すかは3Dパネルの『描出構造』で選べます）。"))
        self._place_ice_chips()

    def _toggle_echo_look(self):
        self.ct_echo_filter = self.echoLookBtn.isChecked()
        settings_store.store().setValue("ice_echo_look", self.ct_echo_filter)
        self._refresh()

    def _toggle_ice_organs(self):
        self.show_ice_organs = self.iceOrgBtn.isChecked()
        settings_store.store().setValue("ice_organs", self.show_ice_organs)
        if self.show_ice_organs and not self._ice_structure_layers():
            self.statusBar().showMessage(
                L("Run “Auto vessels” first — there are no AI structures to show yet.",
                  "先に『自動構造描出』を実行してください（重ねるAI構造がまだありません）。"), 5000)
        self.ice.update()

    def _place_two_btn(self):
        if not hasattr(self, "twoBtn"):
            return
        self.twoBtn.adjustSize()
        self.twoBtn.move(max(0, self.centerWrap.width() - self.twoBtn.width() - 10), 6)
        self.twoBtn.raise_()

    # ---------- AI臓器の選択（3Dパネル右上の『描出構造』・EUS版と同じ流儀） ----------
    def _build_organ_legend(self):
        """3Dビュー右上に載せる、描出構造の選択リスト（色■＋名前＋チェック）。

        AIは既に広く抽出してある（total タスク1回で全構造）ので、ここでONにしても **AIの再実行は不要**、
        キャッシュから作り直すだけ（設計方針「抽出は広く・表示は絞る」）。肝臓・IVC・門脈は主シーンとして
        常に描くのでリストには出さない。初期は折りたたみ＝3D表示を覆わない。
        """
        import ts_seg
        from PySide6.QtWidgets import QCheckBox, QToolButton
        st = settings_store.store()
        panel = QWidget(self.p3d)
        panel.setStyleSheet("background: rgba(12,22,38,205); border-radius:6px;")
        vv = QVBoxLayout(panel); vv.setContentsMargins(7, 4, 9, 6); vv.setSpacing(1)
        head = QWidget(); hb = QHBoxLayout(head); hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(4)
        self.legendTitle = QLabel(L("Structures", "描出構造"))
        self.legendTitle.setStyleSheet("color:#9fb4c8; font-weight:600; font-size:10px;")
        self.legendFoldBtn = QToolButton(); self.legendFoldBtn.setText("▸")
        self.legendFoldBtn.setStyleSheet("QToolButton{color:#cfe0ef; border:none; font-size:11px;}")
        self.legendFoldBtn.setToolTip(L("Collapse / expand", "折りたたむ / 開く"))
        self.legendFoldBtn.clicked.connect(self._toggle_legend_fold)
        hb.addWidget(self.legendTitle); hb.addStretch(1); hb.addWidget(self.legendFoldBtn)
        vv.addWidget(head)
        self.legendBody = QWidget(); bv = QVBoxLayout(self.legendBody); bv.setContentsMargins(0, 2, 0, 0); bv.setSpacing(1)
        self._organ_checks = {}
        for name in ts_seg.selectable_organs():
            lab = ts_seg.ORGAN_LABELS.get(name, (name, name)); col = ts_seg.ORGAN_COLORS.get(name, (200, 200, 200))
            row = QWidget(); hh = QHBoxLayout(row); hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(4)
            sw = QLabel("■"); sw.setStyleSheet("color: rgb(%d,%d,%d); font-size:12px;" % col)
            cb = QCheckBox(L(lab[0], lab[1]))
            cb.setChecked(bool(st.value("ts_show_%s" % name, ts_seg.default_shown(name), type=bool)))
            cb.setStyleSheet("QCheckBox{color:#dfe6ee; font-size:11px;} QCheckBox::indicator{width:12px;height:12px;}")
            cb.toggled.connect(lambda b, n=name: self._legend_toggle(n, b))
            hh.addWidget(sw); hh.addWidget(cb); hh.addStretch(1)
            bv.addWidget(row); self._organ_checks[name] = (cb, lab)
        vv.addWidget(self.legendBody)
        self.legendBody.setVisible(False)                     # 既定は折りたたみ（3Dを覆わない）
        panel.adjustSize(); self.organLegend = panel
        self.p3d.installEventFilter(self)
        self._reposition_legend()

    def _shown_organs(self):
        """チェックが入っている構造の一覧（build_scene へ渡す）。"""
        import ts_seg
        st = settings_store.store()
        return [n for n in ts_seg.selectable_organs()
                if bool(st.value("ts_show_%s" % n, ts_seg.default_shown(n), type=bool))]

    def _toggle_legend_fold(self):
        folded = self.legendBody.isVisible()
        self.legendBody.setVisible(not folded)
        self.legendFoldBtn.setText("▸" if folded else "▾")
        self._reposition_legend()

    def _legend_toggle(self, name, on):
        """チェック→設定を保存して、キャッシュから作り直す（AI再実行なし）。"""
        settings_store.store().setValue("ts_show_%s" % name, bool(on))
        self._rerender_ts()

    def _reposition_legend(self):
        """凡例を3Dビューの右上へ。上の『2画面』ボタンと、その下の3D操作説明の行を避けて置く
        （説明文は構造AIボタンの下＝y≈50 に描かれるので、その下端より下に落とす）。"""
        lg = getattr(self, "organLegend", None)
        if lg is None:
            return
        lg.adjustSize()
        top = 56
        btn = getattr(self.p3d, "vesselBtn", None)
        if btn is not None:
            top = max(top, btn.geometry().bottom() + 30)      # 構造AIボタン → 説明文 → その下
        lg.move(max(4, self.p3d.width() - lg.width() - 8), top)
        lg.raise_(); lg.show()

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "centerWrap", None) and ev.type() in (QEvent.Resize, QEvent.Show):
            self._place_two_btn()
        elif obj is getattr(self, "p3d", None) and ev.type() == QEvent.Resize:
            self._reposition_legend()
        elif obj is getattr(self, "ice", None) and ev.type() in (QEvent.Resize, QEvent.Show):
            self._place_ice_chips()
        return super().eventFilter(obj, ev)

    def _two_left_widget(self, key):
        return {"ax": self.cAx, "cor": self.cCor, "sag": self.cSag, "3d": self.p3d}[key]

    def _two_return_home(self, w):
        """2画面へ借りていたペインを、4分割の元のスロット（位置・順序そのまま）へ返す。"""
        if w is self.cAx:
            self.quad.top.insertWidget(0, w)
        elif w is self.cCor:
            self.quad.top.insertWidget(1, w)
        elif w is self.cSag:
            self.quad.bot.insertWidget(0, w)
        elif w is self.iceBox:
            self.quad.bot.insertWidget(1, w)
        elif w is self.p3d:
            self.mainSplit.insertWidget(1, w)
        w.show()

    def _toggle_two_pane(self):
        self.two_pane = not self.two_pane
        if self.two_pane:
            self._enter_two_pane()
        else:
            self._exit_two_pane()
        self._update_two_pane_ui()

    def _enter_two_pane(self):
        self._four_sizes = dict(quad=self.quad.sizes(), main=self.mainSplit.sizes())
        self.mainSplit.hide()
        w = self._two_left_widget(self.two_left)
        self.twoLeftSlot.addWidget(w); w.show()
        self.twoRightSlot.addWidget(self.iceBox); self.iceBox.show()
        self.twoWrap.show()
        if not getattr(self, "_two_sized", False):           # 初回だけ左右半々（以後はユーザー調整を維持）
            half = max(1, self.centerWrap.width() // 2)
            self.twoSplit.setSizes([half, half]); self._two_sized = True

    def _exit_two_pane(self):
        self.twoWrap.hide()
        self._two_return_home(self._two_left_widget(self.two_left))
        self._two_return_home(self.iceBox)
        self.mainSplit.show()
        fs = self._four_sizes or {}
        self.quad.set_sizes(fs.get("quad"))
        m = fs.get("main")
        if m and len(m) == 2 and min(m) >= 0 and sum(m) > 0:
            self.mainSplit.setSizes(list(m))
        self.mainSplit.setStretchFactor(0, 3); self.mainSplit.setStretchFactor(1, 1)
        self.quad._place()

    def _set_two_left(self, key):
        """2画面の左ペインの中身（Axial/Coronal/Sagittal/VR）をワンタップで切り替える。"""
        if key not in ("ax", "cor", "sag", "3d"):
            return
        if self.two_pane and key != self.two_left:
            self._two_return_home(self._two_left_widget(self.two_left))
            w = self._two_left_widget(key)
            self.twoLeftSlot.addWidget(w); w.show()
        self.two_left = key
        self._update_two_pane_ui()

    def _update_two_pane_ui(self):
        """切替ボタンの文言とチップの選択表示を今の状態に合わせる（言語切替でも呼ばれる）。"""
        if not hasattr(self, "twoBtn"):
            return
        if self.two_pane:
            self.twoBtn.setText(L("⊞ 4-pane", "⊞ 4分割"))
            self.twoBtn.setToolTip(L("Back to the four-pane view", "いつもの4分割表示に戻します"))
        else:
            self.twoBtn.setText(L("◫ 2-pane", "◫ 2画面"))
            self.twoBtn.setToolTip(L("Show only CT (or VR) and ICE side by side, large",
                                     "CT（またはVR）とICEの2枚だけを左右に大きく並べます"))
        for k, b in self.twoChips.items():
            b.setStyleSheet(SS_ON if k == self.two_left else SS_OFF)
        self._place_two_btn()

    def _footer(self):
        """最下段＝左:Step2の針操作（Step1では畳む）／右:研究・教育用の注記（常に右下・固定）。
        1本の帯にまとめることで、注記専用の行を持たせず画像表示を圧迫しない。"""
        bar = QWidget(); bar.setStyleSheet("background:#14253a;")
        self.footerBar = bar                                 # 高さ番兵テスト(test_bottom_ui_height_budget)が参照
        h = QHBoxLayout(bar); h.setContentsMargins(8, 3, 10, 3); h.setSpacing(10)
        h.addWidget(self._controls(), 1)                    # Step2の針操作（空いた分は右の注記が使う）
        self.footerLbl = QLabel(); self.footerLbl.setStyleSheet("color:#caa46a;")
        self.footerLbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._reg(self.footerLbl,
                  "For research & education / Not a medical device / Not intra-procedural navigation"
                  " / Final judgment is the physician's  ",
                  "研究・教育用 / 医療機器ではありません / 術中ナビゲーションではありません"
                  " / 最終判断は術者  ")
        h.addWidget(self.footerLbl, 0, Qt.AlignRight | Qt.AlignVCenter)
        return bar

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("File"); self._reg(fm.menuAction(), "File", "ファイル")
        self._reg(fm.addAction("", self._go_database), "Patient list", "患者リスト")
        self._reg(fm.addAction("", self._open_dicom), "Open DICOM…", "DICOMを開く…")
        self._reg(fm.addAction("", self._open_sample), "Open sample case (HCC048)", "サンプル症例を開く（HCC048）")
        fm.addSeparator()
        self._reg(fm.addAction("", self._open_npy), "Open .npy… (developer)", ".npyを開く…（開発用）")
        em = mb.addMenu("Edit"); self._reg(em.menuAction(), "Edit", "編集")
        self.undoAction = em.addAction(""); self._reg(self.undoAction, "Undo", "元に戻す")
        self.undoAction.setShortcut(QKeySequence.Undo); self.undoAction.setEnabled(False)
        self.undoAction.triggered.connect(self._undo)
        em.addSeparator()
        self._reg(em.addAction("", self._clear_path), "Clear IVC path", "IVCパスを消去")
        self._reg(em.addAction("", self._clear_needle), "Clear needle", "針を消去")
        self._reg(em.addAction("", self._clear_plots), "Clear plots", "プロットを消去")
        self._reg(em.addAction("", self._clear_hep), "Clear hand-drawn hepatic veins", "手動肝静脈を消去")
        em.addSeparator()
        self.hepMenuAct = em.addAction("", lambda: (self.hepBtn.setChecked(not self.hep_mode), self._toggle_hep_mode()))
        self._reg(self.hepMenuAct, "Draw hepatic vein (manual)", "肝静脈を手動で描く")
        self._reg(em.addAction("", self._hep_new_vein), "New hepatic vein", "新しい肝静脈")
        em.addSeparator()
        self._reg(em.addAction("", self._clear_all), "Clear all", "すべて消去")
        em.addSeparator()
        rv = em.addAction("", self._reset_all_views)         # 画像が『飛んだ』時の一発復旧
        self._reg(rv, "Reset views (if an image flew off)", "表示をリセット（画像が飛んだ時）")
        rv.setShortcut(QKeySequence("Ctrl+0"))
        # Settings：言語（操作スタイルはHandle操作のみに統合済み・Classicモードは廃止）
        sm = mb.addMenu("Settings"); self._reg(sm.menuAction(), "Settings", "設定")
        # 挿入方向（大腿/頸静脈）は施設ごとにほぼ固定なので既定値として設定に集約（患者ごとの上書きも可）
        ism = sm.addMenu(""); self._reg(ism.menuAction(), "Default insertion route", "既定の挿入方向")
        self.actInsFem = ism.addAction(""); self.actInsFem.setCheckable(True)
        self._reg(self.actInsFem, "Femoral (foot)", "大腿（足側から）")
        self.actInsFem.triggered.connect(lambda: self._set_insertion_default(True))
        self.actInsJug = ism.addAction(""); self.actInsJug.setCheckable(True)
        self._reg(self.actInsJug, "Jugular (neck)", "頸静脈（頭側から）")
        self.actInsJug.triggered.connect(lambda: self._set_insertion_default(False))
        self.actInsFem.setChecked(self.tipHighZ); self.actInsJug.setChecked(not self.tipHighZ)
        sm.addSeparator()
        self._reg(sm.addAction("", self._ts_settings), "AI anatomy (TotalSegmentator)…", "AI解剖（TotalSegmentator）…")
        # 解析の細かさ（先生決裁 2026-07-21）。AI は内部で 1.5mm に落として推論するので、
        # 標準にすると薄いスライスの症例で 3割ほど速くなる（臓器体積の差は実測 1.5% 以内）。
        import ts_seg as _ts
        qm = sm.addMenu(""); self._reg(qm.menuAction(), "Analysis detail", "解析の細かさ")
        self._quality_acts = {}
        for _k in ("high", "standard", "fast"):
            _en, _ja = _ts.QUALITY_PRESETS[_k][1]
            _a = qm.addAction(""); _a.setCheckable(True); self._reg(_a, _en, _ja)
            _a.setToolTip(L(*_QUALITY_HELP[_k]))
            _a.triggered.connect(lambda _c=False, k=_k: self._set_ts_quality(k))
            self._quality_acts[_k] = _a
        self._quality_acts[self._ts_quality()].setChecked(True)
        sm.addSeparator()
        # フォントサイズ（EUS版と同じ・先生要望 2026-07-20）＝アプリ全体の文字サイズを変更・保存。
        fsm = sm.addMenu(""); self._reg(fsm.menuAction(), "Font size", "フォントサイズ")
        self._font_acts = {}
        for en, ja, pt in (("Small", "小", 11), ("Normal", "中（既定）", 13), ("Large", "大", 15), ("Extra large", "特大", 18)):
            a = fsm.addAction(""); a.setCheckable(True); self._reg(a, en, ja)
            a.triggered.connect(lambda _c=False, p=pt: self._set_font_size(p))
            self._font_acts[pt] = a
        # 既定は「今までと同じ大きさ」（＝OSの標準）。先生が選んだ時だけ変わる＝初回起動の見た目は不変。
        self._set_font_size(int(settings_store.store().value("font_size", self._base_pt, type=int)), save=False)
        sm.addSeparator()
        # 操作方法の確認とカスタマイズ（先生指示 2026-07-21：操作は設定に記載、ボタン割当も変更可能に）
        self._reg(sm.addAction("", self._controls_settings), "Controls & customization…", "操作方法・カスタマイズ…")
        sm.addSeparator()
        self._reg(sm.addAction("", self._toggle_lang), "Switch language (EN / 日本語)", "表示言語を切替 (EN / 日本語)")
        m = mb.addMenu("Help"); self._reg(m.menuAction(), "Help", "ヘルプ")
        self._reg(m.addAction("", self._open_manual), "User manual (PDF)…", "使い方説明書（PDF）を開く…")
        self._reg(m.addAction("", lambda: self._show_tip_dialog(startup=False)), "Tip of the day", "今日のヒント")
        m.addSeparator()
        self._reg(m.addAction("", self._show_faq), "FAQ / How to use", "FAQ / 使い方")
        self._reg(m.addAction("", self._show_about), "About / Author", "このアプリについて / 作者")
        self._reg(m.addAction("", self._check_updates), "Check for updates…", "アップデートを確認…")

    def _apply_language(self):
        """現在の言語で全UI文字列を貼り替える（登録ウィジェット＋描画系＋DBビュー）。"""
        for w, en, ja in self._i18n:
            w.setText(L(en, ja))
        self._update_liver_btn()
        self.ax.placeholder = self.cor.placeholder = self.sag.placeholder = \
            L("Open a CT to begin", "CTを開いてください")
        self.ice.placeholder = L("Draw the IVC path on Axial (>= 2 clicks)",
                                 "AxialでIVCパスを描いてください（2クリック以上）")
        self._update_mode_ui()                               # ペインのキャプション・Rotate/Tilt等
        self._update_ice_info()
        self._update_two_pane_ui()                           # 2画面切替ボタンの文言（動的なので_i18n登録外）
        self._update_ice_chip_ui()                           # ICE右上『エコー風 / 構造』
        if hasattr(self, "legendTitle"):                     # 3D右上『描出構造』＝見出しと各構造名
            self.legendTitle.setText(L("Structures", "描出構造"))
            for cb, lab in getattr(self, "_organ_checks", {}).values():
                cb.setText(L(lab[0], lab[1]))
            self._reposition_legend()
        self.db.retranslate()
        self.gbar.update()
        for pn in self._panes:
            pn.update()

    def _update_liver_btn(self):
        self.liverModeBtn.setText(L("Liver: ", "肝臓: ")
                                  + (L("Surface", "表面") if self.liver_mode == "surface" else L("Haze", "もや")))

    def _check_updates(self, silent=False):
        """新版を探し、見つかればワンクリックで入れ替え・再起動する。
        起動時(silent)もメニュー(手動)も、ローカル配布フォルダ＋インターネット(UPDATE_URL)の
        両方を見る。公開版のユーザーはローカル配布フォルダを持たないので、起動時にネットを
        見ないと自動更新が一度も走らなかった（2026-07-24 修正）。
        silent=True（起動時）は最新時・確認不可時は無言、silent=False（メニュー）は最新時も通知。"""
        import updater
        updater.cleanup_stale_staging()              # 過去更新の .app.new/.app.old 残骸を掃除（2026-07-18）
        try:
            info = updater.find_update(VERSION, UPDATE_URL)
        except Exception as ex:
            if not silent:
                QMessageBox.information(self, L("Check for updates", "アップデートの確認"),
                    L(f"Could not check for updates.\n{ex}", f"更新を確認できませんでした。\n{ex}"))
            return
        if not info:
            if not silent:
                QMessageBox.information(self, L("Check for updates", "アップデートの確認"),
                    L(f"You have the latest version (v{VERSION}).", f"最新版です（v{VERSION}）。"))
            return
        target = updater.current_app_bundle()
        notes = (info.get("notes") or "").strip()
        if target is None or updater.is_translocated(target):  # ソース実行 or 隔離実行 → 自己置換不可
            if not silent:                                    # silent(起動時)は無言＝更新ループを防ぐ
                extra = (L("\n\nThis app is running from a macOS quarantine location and can't self-update."
                           "\nPlease copy the latest version from the distribution folder to the Desktop"
                           "\n(or move this app to a different folder in Finder to clear quarantine).",
                           "\n\nこのアプリは macOS の隔離領域から実行されているため自動更新できません。"
                           "\n配布フォルダの最新版をデスクトップにコピーし直してください"
                           "（または Finder でアプリを一度別の場所へ移動し、隔離を解除してください）。")
                         if updater.is_translocated(target) else
                         L("\n\nPlease get the new version from the distribution folder.",
                           "\n\n配布フォルダから新しいバージョンを入手してください。"))
                QMessageBox.information(self, L("Update available", "新しいバージョン"),
                    L(f"A new version ({info['version']}) is available (you have {VERSION}).",
                      f"新しいバージョン {info['version']} があります（現在 {VERSION}）。") + f"\n{notes}{extra}")
            return
        msg = (L(f"A new version ({info['version']}) is available (you have {VERSION}).",
                 f"新しいバージョン {info['version']} があります（現在 {VERSION}）。") + "\n"
               + (notes + "\n\n" if notes else "\n")
               + L("Update and restart now?", "今すぐ更新して再起動しますか？"))
        if QMessageBox.question(self, L("Update available", "新しいバージョン"), msg,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        import bg
        bg.run_with_progress(self, L(f"Updating to v{info['version']}…", f"v{info['version']} へ更新中…"),
            lambda prog: updater.stage_new_bundle(info, prog),
            lambda new_app: self._apply_update(new_app, target, info["version"]),
            on_fail=lambda m: QMessageBox.warning(self, L("Update failed", "更新失敗"),
                L("The update failed.\n", "更新に失敗しました。\n") + m.splitlines()[0]
                + L("\n\nPlease replace it manually from the distribution folder.",
                    "\n\n配布フォルダから手動で入れ替えてください。")))

    def _show_faq(self):
        if i18n.lang() == "ja":
            html = """
        <h2>TIPS ICE Planner — FAQ / 使い方</h2>
        <p><b>これは何？</b> TIPSの術前検討・教育・自己研鑽のためのツールです。
        術前造影CTから、IVC内の側射型ICEプローブでどう見えるかを予測し、
        穿刺の候補ライン（現在は Entry→Target の直線）と、ICE&#8596;CT の空間対応を表示します。</p>
        <p><b>これは何ではないか。</b> 認証された医療機器ではありません。術中ナビゲーションではありません。
        診断・治療のためのものではありません。最終的な臨床判断はすべて術者が行います。</p>
        <p><b>プライバシー。</b> すべてこのコンピュータ内で動作します。画像のアップロードは行わず、
        患者データが外部に出ることはありません。</p>
        <h3>クイックスタート</h3>
        <ol>
          <li><b>患者リスト</b> &rarr; 検査/シリーズを選択 &rarr; <i>開く</i>。</li>
          <li><b>手順1 &ndash; ICEセットアップ:</b> AxialでIVCに沿って<b>2点以上クリック</b>しICE軸を設定。
              <i>大腿 / 頸静脈</i> を選択。<i>回転 &theta;</i>・<i>プローブ前後</i>・<i>偏向 A/P・L/R</i> で扇を向ける。
              <b>ロール</b>でICE表示を任意角度に回転、必要なら <i>ICE左右反転</i>。</li>
          <li><b>手順2 &ndash; 穿刺（直線）:</b> <b>&#9312; Entry（刺入点）</b>→<b>&#9313; Target（門脈側）</b>の順に設定。
              Entry→Targetの<b>直線</b>が4画面と3Dに描かれ、距離がICE画像下に表示されます。
              （曲がる針のモデル RUPS / Colapinto と Plot 予習モードは再設計まで一時停止中です。）</li>
          <li><b>実際の針:</b> まず<b>Entry</b>を設定（刺入点は一度決めたら固定）。
              <b>実際の針先（ICEをクリック）</b>を押し、ICE画像上で実際に針先が見える場所を1回クリック。
              Entryからその点まで針が自動で描かれます（針先はドラッグで微調整）。
              約2cmの点線は想定されるColapinto様の弯曲の続きで、Targetとの位置関係（距離＋腹側/背側・頭側/尾側・左右）も表示。
              <b>↺左 / 右↻</b> は手元でカニューラを軸まわりに捻る操作の模擬で、点線が振れて「どちらへ曲がるか」の目安になります
              （右 = 手元から先端方向を見て時計回り。実機と一致するかは要確認）。
              これは記述表示であり、経路の推奨ではありません。</li>
          <li><b>経腹（体表）エコー:</b> 上部の <b>エコー: 血管内ICE → 経腹（体表）</b> に切替後、
              <b>Axial / Coronal / Sagittal のどれかで皮膚をクリック</b>するとプローブが体表に吸着して設置され、
              その断面に扇が乗ります。<b>皮膚上をドラッグ</b>で移動。<i>回転 / 傾き / あおり</i> で向きを調整。
              これは<b>静的なCTプレビュー</b>であり、リアルタイム超音波ではありません。</li>
        </ol>
        <h3>トラックパッド（Mac / 精密タッチパッド）</h3>
        <ul>
          <li><b>ピンチ</b> &mdash; 拡大縮小（カーソル位置中心）</li>
          <li><b>&#8984;/Ctrl + 2本指スクロール</b> &mdash; 拡大縮小（ピンチの代替）</li>
          <li><b>&#8997; Option + ドラッグ</b>（macOS）、または <b>Space</b> 長押し + ドラッグ &mdash; 移動（パン）</li>
          <li>2本指スクロール &mdash; スライス送り（ICE上では &theta; 回転）</li>
          <li>2本指ダブルタップ &mdash; フィット / 2倍 切替</li>
        </ul>
        <h3>マウス</h3>
        <ul>
          <li>ホイール &mdash; スライス送り（ICE上では &theta; 回転）</li>
          <li>左ドラッグ &mdash; 階調（W/L）</li>
          <li>右ドラッグ &mdash; 拡大縮小（カーソル位置中心）・右クリック &mdash; 表示リセット</li>
          <li>中ドラッグ &mdash; 移動（パン）</li>
        </ul>
        <p style="color:#888">作者・寄付については <b>ヘルプ &rarr; このアプリについて / 開発を支援</b> を参照してください。</p>
        """
            self._info_dialog(L("FAQ / How to use", "FAQ / 使い方"), html)
            return
        html = """
        <h2>TIPS ICE Planner — FAQ / How to use</h2>
        <p><b>What is this?</b> A research, education and self-training tool for TIPS planning.
        From a pre-procedure contrast CT it predicts how a side-firing ICE probe in the IVC would look,
        and candidate needle trajectories (RUPS straight / Colapinto curved), and shows the ICE&#8596;CT
        spatial correspondence.</p>
        <p><b>What it is NOT.</b> Not a certified medical device. Not intra-procedural navigation.
        Not for diagnosis or treatment. The operator makes all final clinical decisions.</p>
        <p><b>Privacy.</b> Runs fully on your computer. No images are uploaded; no patient data leaves the machine.</p>
        <h3>Quick start</h3>
        <ol>
          <li><b>Database</b> &rarr; select a study/series &rarr; <i>Open</i>.</li>
          <li><b>Step 1 &ndash; ICE setup:</b> on the Axial pane, click <b>&ge;2 points along the IVC</b> to set the ICE axis.
              Choose <i>Femoral / Jugular</i>. Aim the fan with <i>Rotate &theta;</i>, <i>Probe</i> (push&ndash;pull),
              <i>Deflect A/P &amp; L/R</i>. Use <b>Roll</b> to rotate the ICE view freely to any angle, and <i>Flip ICE L/R</i> if needed.</li>
          <li><b>Step 2 &ndash; Puncture (straight):</b> set <b>&#9312; Entry (puncture)</b> then <b>&#9313; Target (portal)</b>.
              A <b>straight line</b> from Entry to Target is drawn on all four panes and in 3D, with its length shown on the ICE view.
              (Curved-needle models RUPS / Colapinto and the Plot rehearsal mode are temporarily disabled, pending redesign.)</li>
          <li><b>Actual needle:</b> set <b>Entry</b> first (the puncture point is fixed once you set it). Click
              <b>Actual tip (click ICE)</b>, then click once on the ICE view where you actually see the needle tip.
              The needle is drawn automatically from Entry to that point (drag the tip to fine-tune). A short
              (&#8776;2&thinsp;cm) dotted extension shows the assumed Colapinto-like curvature, plus the tip's position
              relative to Target (distance + ventral/dorsal, cranial/caudal, left/right).
              <b>Rotate left / Rotate right</b> simulate twisting the cannula by hand around its own axis &mdash; the
              dotted line swings to show which way the tip would curve, as a guide for which way to turn it.
              (Right = clockwise looking from your hand toward the tip &mdash; verify this matches your actual device.)
              Descriptive only &mdash; not a path recommendation.</li>
          <li><b>Transabdominal (surface) echo:</b> switch <b>Echo: Intravascular ICE → Transabdominal (surface)</b> at the top, then
              <b>click on the skin in any of the Axial / Coronal / Sagittal panes</b> to place the probe (it snaps to
              the body surface); the fan lies in that pane. <b>Drag the probe along the skin</b> to move it. Aim it with
              <i>Rotate (回転) / Tilt (傾き) / Rock (あおり)</i>; the contact stays fixed while the fan sweeps.
              This is a <b>static CT preview</b> of a convex probe, <b>not live ultrasound</b>.</li>
        </ol>
        <h3>Trackpad (Mac / precision touchpad)</h3>
        <ul>
          <li><b>Pinch</b> &mdash; zoom (centered on the cursor)</li>
          <li><b>&#8984;/Ctrl + two-finger scroll</b> &mdash; zoom (alternative to pinch)</li>
          <li><b>&#8997; Option + drag</b> (macOS), or hold <b>Space</b> + drag (any OS) &mdash; pan</li>
          <li>Two-finger scroll &mdash; scroll slices (on ICE: rotate &theta;)</li>
          <li>Two-finger double-tap &mdash; fit / 2&times; toggle</li>
        </ul>
        <h3>Mouse</h3>
        <ul>
          <li>Wheel &mdash; scroll slices (on ICE: rotate &theta;)</li>
          <li>Left-drag &mdash; window level / width</li>
          <li>Right-drag &mdash; zoom (centered on the cursor) &middot; Right-click &mdash; reset view</li>
          <li>Middle-drag &mdash; pan</li>
        </ul>
        <p style="color:#888">See <b>Help &rarr; About / Author</b> for author information.</p>
        """
        self._info_dialog(L("FAQ / How to use", "FAQ / 使い方"), html)

    def _show_about(self):
        if i18n.lang() == "ja":
            html = f"""
        <h2>TIPS ICE Planner</h2>
        <p><b>バージョン:</b> {VERSION}　|　<b>作者:</b> {AUTHOR_LINE}.</p>
        <p>研究・教育のための独立・自己資金による開発です。</p>
        <p><b>免責。</b> これは研究・教育・自己研鑽のための試作品です。
        <b>認証された医療機器ではなく</b>、疾患の診断・治療・予防を目的とせず、
        <b>術中ナビゲーションでもありません</b>。最終的な臨床判断はすべて術者が行います。</p>
        <p><b>プロジェクト:</b> <a href="{GITHUB_REPO}">{GITHUB_REPO}</a></p>
        """
        else:
            html = f"""
        <h2>TIPS ICE Planner</h2>
        <p><b>Version:</b> {VERSION}　|　<b>Author:</b> {AUTHOR_LINE}.</p>
        <p>Independent, self-funded development for research and education.</p>
        <p><b>Disclaimer.</b> This is a prototype for research, education and self-training only.
        It is <b>not a certified medical device</b>, is <b>not</b> intended to diagnose, treat or prevent disease,
        and is <b>not</b> intra-procedural navigation. The operator makes all final clinical decisions.</p>
        <p><b>Project:</b> <a href="{GITHUB_REPO}">{GITHUB_REPO}</a></p>
        """
        self._info_dialog(L("About / Author", "このアプリについて / 作者"), html, open_url=GITHUB_REPO)

    def _maybe_show_donation_prompt(self):
        if not self._donation_prompt_due():
            return
        dlg = QDialog(self); dlg.setWindowTitle(L("Support development", "開発を支援"))
        v = QVBoxLayout(dlg)
        msg = QLabel(L(
            "If TIPS ICE Planner has been useful, a small one-time or monthly contribution "
            "helps keep development going. This is completely optional.",
            "TIPS ICE Plannerがお役に立っていましたら、一回だけでも月額でも、少しの支援が開発の継続の力になります。"
            "任意ですので、気にせず「また今度」を選んでいただいて構いません。"))
        msg.setWordWrap(True); v.addWidget(msg)
        url = self._donation_url()
        via = L("via GitHub Sponsors", "note（noteの募金ページ）から") if i18n.lang() == "ja" \
            else L("via GitHub Sponsors", "GitHub Sponsors から")
        v.addWidget(QLabel(L(f"Support {via}:", f"{via}支援できます:")))
        qr_html = self._qr_html(150)
        if qr_html:
            pic = QLabel(); pic.setTextFormat(Qt.RichText); pic.setAlignment(Qt.AlignCenter)
            pic.setText(qr_html); v.addWidget(pic)
        link = QLabel(f'<a href="{url}">{url}</a>')
        link.setOpenExternalLinks(True); link.setAlignment(Qt.AlignCenter); v.addWidget(link)
        row = QHBoxLayout()
        bMonthly = QPushButton(L("I'm a monthly supporter\n(don't ask again)",
                                 "月額支援中\n（もう聞かない）"))
        bOnce = QPushButton(L("Supported once\n(remind me in a month)",
                              "一度支援した\n（1か月後にまた聞いて）"))
        bLater = QPushButton(L("Not yet / Later", "まだ / また今度"))
        for b in (bMonthly, bOnce, bLater):
            row.addWidget(b)
        v.addLayout(row)

        def _set(status):
            s = settings_store.store()
            s.setValue("donation_status", status)
            if status == "once":
                from datetime import datetime
                s.setValue("donation_last_ack", datetime.now().isoformat())
            dlg.accept()

        bMonthly.clicked.connect(lambda: _set("monthly"))
        bOnce.clicked.connect(lambda: _set("once"))
        bLater.clicked.connect(dlg.reject)     # 状態は変えない＝次回起動時にまた表示
        dlg.exec()

    # ---------- 起動時の「今日のヒント」（VS Code風・チェックボックスでオフ可）----------
    def _show_startup_disclaimer(self):
        """起動時の免責。親=このウィンドウ＋WindowModalで、画面中央ではなくアプリ本体の上に重ねて出す
        （macOSでは親ウィンドウに付くシート、Windowsではウィンドウ中央のモーダル）。"""
        m = QMessageBox(self)
        m.setIcon(QMessageBox.Information)
        m.setWindowTitle("TIPS ICE Planner — research / education tool")
        m.setText("Research, education and self-training only.\n\n"
                  "· Not a certified medical device.\n· Not intra-procedural navigation.\n"
                  "· The operator makes all final clinical decisions.")
        m.setWindowModality(Qt.WindowModal)              # 親ウィンドウに紐づく＝アプリの上でポップ
        m.exec()

    def _update_step_ui(self):
        s1 = (self.step == 0)
        # 『画面を動かさず、はめ込む』（先生指示 2026-07-18）: Step切替で下部UIの高さ・構成を一切変えない。
        # 穿刺点/ターゲット/実際の針先は常設して 穿刺設定OFF(step0) では無効(グレー)にするだけ。
        for w in (self.entryBtn, self.targetBtn, self.aimBtn):
            w.setEnabled(not s1)
        if hasattr(self, "punctureBtn"):                     # 穿刺設定トグルの見た目を step に合わせる
            self.punctureBtn.setChecked(not s1)
            self.punctureBtn.setStyleSheet(SS_ON_L if not s1 else SS_OFF_L)
        # 曲げ系(RUPS/Colapinto・前進量・曲率)とPlot予習は一旦封印（直線穿刺へ巻き戻し・再実装まで非表示）
        for w in (self.needleTypeBtn, self.lblAdv, self.sAdvance, self.advVal,
                  self.lblCurve, self.sCurve, self.curveVal, self.plotBtn, self.predNeedleBtn):
            w.setVisible(False)
        self.predict = False                                  # Plot予習(ピンク点線)もオフに固定
        self._refresh_toggles()

    # ---------- データ ----------
    def _on_series_loaded(self, vol):
        self._set_volume(vol); self.stack.setCurrentWidget(self.viewer_page)
        st = self._pending_restore; self._pending_restore = None
        if st is not None:
            self._restore_state(st)
        self._ensure_sample_ai_cache()                       # 同梱サンプルなら事前計算AIをキャッシュへ（TS未導入でも3D表示）
        self._load_ts_cached_only()                          # AI解剖が既に有れば即時表示。生成は「構造AI」ボタンで

    # 同梱サンプル(Patient_01=公開HCC-TACE-Seg HCC_048)の StudyInstanceUID。
    _SAMPLE_UID = "1.3.6.1.4.1.14519.5.2.1.1706.8374.191238202133507320458118565112"

    def open_external_path(self, path):
        """Mieleプラグイン等から渡されたフォルダ/ファイルを TIPS ICE Planner に
        永久取り込み（カタログ登録＋アプリ専用領域へコピー）してから開く。
        相(シリーズ)ごとに分離して保存し、患者一覧から相を選んで開ける。"""
        import os
        self.showNormal(); self.raise_(); self.activateWindow()
        if not path or not os.path.exists(path):
            self.statusBar().showMessage("Open error: path not found — " + str(path), 8000); return
        if os.path.isfile(path) and path.lower().endswith(".npy"):   # .npy は従来どおり一時表示
            self.current_study_uid = None; self._current_files = []
            try:
                self._set_volume(dicom_io.load_npy(path)); self.stack.setCurrentWidget(self.viewer_page)
            except Exception as ex:
                self.statusBar().showMessage(f"Load error: {ex}", 8000)
            return
        src = path if os.path.isdir(path) else os.path.dirname(path)
        import bg
        bg.run_with_progress(self, L("Importing into TIPS ICE Planner…", "TIPS ICE Plannerへ取り込み中…"),
            lambda prog: self._import_external(src, prog), self._on_external_imported,
            on_fail=self._on_import_failed)

    def _on_external_imported(self, result):
        added, study_uid = result
        self.db.reload(); self.stack.setCurrentWidget(self.db)
        opened = self.db.select_study(study_uid, open_if_single=(added > 0))
        if added > 0:
            self.statusBar().showMessage(
                L(f"Imported {added} series — saved permanently in TIPS ICE Planner.  ",
                  f"{added} シリーズを取り込みました — TIPS ICE Planner 内に永久保存されます。  ")
                + (L("Opening…", "開いています…") if opened
                   else L("Pick a phase (series) to open.", "開く相（シリーズ）を選んでください。")), 12000)
        else:
            self.statusBar().showMessage(L("Already in TIPS ICE Planner. Pick a phase (series) to open.",
                                           "既に取り込み済みです。開く相（シリーズ）を選んでください。"), 10000)

    # ---------- 同梱サンプル症例（TCIA HCC-TACE-Seg / HCC048・CC BY 4.0）----------
    SAMPLE_DIR = "HCC048_portal_venous"

    def _set_volume(self, vol):
        self.vol = vol; nz, H, W = vol.shape
        self.p3d.orient = vol.meta.get("orient")             # 方位キューブ（無ければ標準axial仮定）
        self.cz, self.cy, self.cx = nz // 2, H // 2, W // 2
        self.path = []; self.entry = None; self.target = None; self.obs = []; self.sProbe.setEnabled(False)
        self.hep_veins = []; self.hep_mode = False; self._sync_hep_ui()     # 手動肝静脈も患者ごとにリセット
        self.vein_overrides = []; self.vein_edit = False; self._sync_vein_ui()   # 血管再指定も患者ごとにリセット
        self._vein_lab = None; self._vein_sel = None
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0    # 実際の針も患者ごとにリセット
        self.label_offsets = {}                              # 文字ラベルの手動位置も患者ごとにリセット
        self.contact = None; self.normal = None              # 経腹プローブ接触点も患者ごとにリセット
        self.liver = None; self._liver_key = None; self.p3d.liver = None
        self.p3d.ts_liver = self.p3d.ts_ivc = self.p3d.ts_portal = self.p3d.ts_hepatic = None
        self.p3d.ts_organs = {}                          # 追加表示（胆嚢・結腸・肝腫瘍）も一緒に消す
        self._ts_key = None                                  # AI解剖も患者ごとにリセット
        self.body = None; self._body_key = None; self.p3d.body = None
        self.step = 0; self.ptMode = 0                       # 新規患者は必ず「1. ICEセットアップ」から
        note = vol.meta.get("note", "")
        self.statusBar().showMessage(
            f"Loaded {nz}×{H}×{W}  spacing {vol.sx:.2f}/{vol.sy:.2f}/{vol.dz:.2f} mm" + (f"   ⚠ {note}" if note else ""), 12000)
        self._update_step_ui()
        self._refresh()
        self._compute_body()                                 # 読込時に体表シェルを背景抽出
        self._update_save_buttons()                          # 患者が変わったので保存スロットの表示も更新
        self._mark_clean()                                   # 開いた直後は『未保存の変更なし』（触るまで閉じる時に聞かない）

    # ---------- 作業状態の保存・復元（患者ごとにスロット1/2/3）----------
    def _fingerprint_of(self, st):
        """状態dictの指紋。見た目だけの差（ズーム/パン/断面/窓値=view）と開いていたファイル一覧は
        除く＝『中身の作業』だけを見る。今の作業と保存済み状態の突き合わせにも使う。"""
        import json
        st = dict(st); st.pop("view", None); st.pop("files", None)
        try:
            return json.dumps(st, sort_keys=True, default=str)
        except Exception:
            return repr(st)

    def _state_fingerprint(self):
        """今の作業状態の指紋（保存済みかの判定用）。"""
        return self._fingerprint_of(self._capture_state())

    def _mark_clean(self):
        """今の状態を『保存済み（変更なし）』の基準にする。保存/復元/患者を開いた直後に呼ぶ。"""
        self._clean_fp = self._state_fingerprint()

    def _mark_dirty_if_unsaved(self):
        """保存スロットを削除した後などに呼ぶ：今の作業が *どの残っているスロットにも* 保存されて
        いなければ『未保存』に戻す（🐛先生報告 2026-07-21：保存を削除した直後、作業内容は変わって
        いないので clean のまま→閉じる時に保存を促されず、唯一の保存が消えて作業が宙に浮いていた）。"""
        if self.vol is None or not self.current_study_uid:
            return
        cur = self._state_fingerprint()
        for n in (1, 2, 3):
            st = self.catalog.get_session(self.current_study_uid, n)
            if st and self._fingerprint_of(st) == cur:
                return                                       # まだどこかに保存されている＝clean のまま
        self._clean_fp = None                                # どこにも無い＝未保存に戻す（閉じる時に保存を促す）

    def _has_unsaved_changes(self):
        """保存を促すべきか。作業が空（何も置いていない）なら促さない。基準から変わっていれば促す。"""
        if self.vol is None:
            return False
        has_work = bool(self.path or self.entry is not None or self.target is not None
                        or self.aim_tip is not None or self.hep_veins or self.vein_overrides
                        or self.contact is not None)
        if not has_work:
            return False
        return self._clean_fp is None or self._state_fingerprint() != self._clean_fp

    def _capture_state(self):
        """今の作業状態（IVCパス・Entry/Target・実際の針先・ICE操作パラメータ等）を辞書化。numpyはlistへ。"""
        def _l(v):
            return None if v is None else np.asarray(v, float).tolist()
        return dict(
            files=list(self._current_files), path=list(self.path), zP=self.zP,
            theta=self.theta, b1=self.b1, b2=self.b2, iceRoll=self.iceRoll, flip=self.flip,
            lock3=self.lock3,
            tipHighZ=self.tipHighZ, step=self.step, ptMode=self.ptMode,
            entry=_l(self.entry), target=_l(self.target),
            hep_veins=[[np.asarray(pt, float).tolist() for pt in vein] for vein in self.hep_veins],
            vein_overrides=[dict(pt=[float(c) for c in ov["pt"]]) for ov in self.vein_overrides],
            aim_tip=_l(self.aim_tip), aim_torque=self.aim_torque,
            viewMode=self.viewMode, contact=_l(self.contact), normal=_l(self.normal),
            surfPlane=self.surfPlane, liver_mode=self.liver_mode,
            liver_opacity=self.liver_opacity, show_liver=self.show_liver,
            obs=[np.asarray(o, float).tolist() for o in self.obs],
            view=self._capture_view())

    def _capture_view(self):
        """画面の見え方（＝どう見えていたか）。幾何とは別に持つ。
        これまで保存していたのは幾何（パス・Entry/Target・角度）だけで、CT をどこまで拡大して
        どこを見ていたかは復元時に初期状態へ戻っていた。作業の続きを開いたのに絵が違う。
        保存するのは: 4画面それぞれの拡大率と表示位置 / スライス位置 / 窓値(WL/WW) / 3Dの視点。"""
        def _p(pane):
            return dict(zoom=float(pane.zoom), pan=[float(pane.pan.x()), float(pane.pan.y())])
        return dict(
            panes={k: _p(p) for k, p in (("ax", self.ax), ("cor", self.cor),
                                         ("sag", self.sag), ("ice", self.ice))},
            slices=[int(self.cz), int(self.cy), int(self.cx)],
            wl=float(self.wl), ww=float(self.ww),
            az=float(self.p3d.az), el=float(self.p3d.el), zoom3d=float(self.p3d.zoom3d),
            pan3d=[float(self.p3d.pan3d.x()), float(self.p3d.pan3d.y())],
            two=dict(on=bool(self.two_pane), left=self.two_left,     # 表示モード（4分割/2画面・左の中身）
                     sizes=[int(x) for x in self.twoSplit.sizes()]),
            quad=self.quad.sizes(), main=self.mainSplit.sizes(),   # 4画面の枠そのものの大きさ
            labels=[[pk, li, float(o[0]), float(o[1])]            # ドラッグして動かした文字ラベルの位置
                    for (pk, li), o in self.label_offsets.items()])

    def _restore_view(self, v):
        """_capture_view の内容を戻す。古い保存データには 'view' が無いので、その場合は何もしない
        （＝従来どおり初期表示）。壊れた値で画面が飛ばないよう、範囲は必ずクランプする。"""
        if not v or self.vol is None:
            return
        self.label_offsets = {(str(pk), str(li)): (float(dx), float(dy))    # 文字ラベルの位置を復元
                              for pk, li, dx, dy in (v.get("labels") or [])}
        nz, H, W = self.vol.shape
        for k, pane in (("ax", self.ax), ("cor", self.cor), ("sag", self.sag), ("ice", self.ice)):
            d = (v.get("panes") or {}).get(k)
            if not d:
                continue
            pane.zoom = float(np.clip(d.get("zoom", 1.0), 0.2, 12.0))       # zoom_at と同じ上下限
            px, py = d.get("pan", [0.0, 0.0])
            pane.pan = QPointF(float(px), float(py))
        sl = v.get("slices")
        if sl and len(sl) == 3:
            self.cz = int(np.clip(sl[0], 0, nz - 1))
            self.cy = int(np.clip(sl[1], 0, H - 1))
            self.cx = int(np.clip(sl[2], 0, W - 1))
        self.wl = float(v.get("wl", self.wl)); self.ww = max(1.0, float(v.get("ww", self.ww)))
        self.p3d.az = float(v.get("az", self.p3d.az))
        self.p3d.el = float(np.clip(v.get("el", self.p3d.el), -89, 89))
        self.p3d.zoom3d = float(np.clip(v.get("zoom3d", self.p3d.zoom3d), 0.2, 8.0))
        p3 = v.get("pan3d", [0.0, 0.0])
        self.p3d.pan3d = QPointF(float(p3[0]), float(p3[1]))
        tw = v.get("two")                                     # 表示モード（4分割/2画面）も保存時の見え方へ戻す
        if tw:                                                # 古い保存には無い＝現状維持
            self._set_two_left(tw.get("left") if tw.get("left") in ("ax", "cor", "sag", "3d") else "ax")
            if bool(tw.get("on")) != self.two_pane:
                self._toggle_two_pane()
            ts = tw.get("sizes")
            if ts and len(ts) == 2 and min(ts) >= 0 and sum(ts) > 0:
                self.twoSplit.setSizes([int(ts[0]), int(ts[1])]); self._two_sized = True
        self.quad.set_sizes(v.get("quad"))                    # 4画面の枠の大きさ（仕切りの位置）
        m = v.get("main")
        if m and len(m) == 2 and min(m) >= 0 and sum(m) > 0:
            self.mainSplit.setSizes(list(m))
        if self.two_pane:                                     # 2画面中は「4分割へ戻った時」に使う枠サイズも保存値で更新
            fs = self._four_sizes or {}
            if v.get("quad"):
                fs["quad"] = v.get("quad")
            if m and len(m) == 2:
                fs["main"] = list(m)
            self._four_sizes = fs

    def _restore_state(self, st):
        """_capture_state の辞書から作業状態を復元し、画面を更新する。"""
        def _a(v):
            return None if v is None else np.array(v, float)
        self.path = [list(p) for p in st.get("path", [])]; self.zP = st.get("zP", 0.0)
        self.hep_veins = [[np.array(pt, float) for pt in vein] for vein in st.get("hep_veins", [])]
        self.hep_mode = False; self._sync_hep_ui()           # 復元時は描画OFF（誤クリック防止）
        self.vein_overrides = [dict(pt=[float(c) for c in ov.get("pt", [])])
                               for ov in st.get("vein_overrides", []) if len(ov.get("pt", [])) == 3]
        self.vein_edit = False; self._vein_sel = None; self._sync_vein_ui()   # 復元時は編集OFF（誤クリック防止）
        self.theta = st.get("theta", 180.0); self.b1 = st.get("b1", 0.0); self.b2 = st.get("b2", 0.0)
        self.iceRoll = st.get("iceRoll", 0.0); self.flip = st.get("flip", False)
        self.lock3 = bool(st.get("lock3", False))            # 3点固定モード
        self.lock3Btn.setChecked(self.lock3)
        self.lock3Btn.setText(L("◎ 3-point lock: ON", "◎ 3点固定: ON") if self.lock3
                              else L("◎ 3-point lock: OFF", "◎ 3点固定: OFF"))
        self.tipHighZ = st.get("tipHighZ", True)
        self.step = st.get("step", 0); self.ptMode = st.get("ptMode", 0)
        self.entry = _a(st.get("entry")); self.target = _a(st.get("target"))
        self.aim_tip = _a(st.get("aim_tip")); self.aim_torque = st.get("aim_torque", 0.0)
        self.viewMode = st.get("viewMode", "ice")
        self.contact = _a(st.get("contact")); self.normal = _a(st.get("normal"))
        self.surfPlane = st.get("surfPlane", 0)
        self.liver_mode = st.get("liver_mode", "haze"); self.liver_opacity = st.get("liver_opacity", 0.5)
        self.show_liver = st.get("show_liver", True)
        self.obs = [np.array(o, float) for o in st.get("obs", [])]
        self.sProbe.setEnabled(len(self.path) >= 2)
        for s, v in ((self.sTheta, self.theta), (self.sB1, self.b1), (self.sB2, self.b2), (self.sRoll, self.iceRoll)):
            s.blockSignals(True); s.setValue(int(v)); s.blockSignals(False)
        self.liverBtn.setChecked(self.show_liver)
        self.sLiverOp.blockSignals(True); self.sLiverOp.setValue(int(self.liver_opacity * 100)); self.sLiverOp.blockSignals(False)
        self._update_liver_btn()
        self.iceBtn.setChecked(self.viewMode == "ice"); self.surfBtn.setChecked(self.viewMode == "surface")
        # ボタンの見た目と実際の入力モードは必ず一致させる。以前は checked だけを針の有無から
        # 決めていて aimMode は False のままだったので、**押された見た目なのに ICE をクリックしても
        # 針が動かない**という食い違いが起きていた。
        self.aimMode = self.aim_tip is not None
        self.aimBtn.setChecked(self.aimMode); self.hubWidget.set_torque(self.aim_torque)
        self.liver = None; self._liver_key = None; self.p3d.liver = None      # 復元後に幾何から再計算させる
        self.body = None; self._body_key = None; self.p3d.body = None
        self._restore_view(st.get("view"))                    # CT の拡大率・表示位置・スライス・窓値・3D視点
        self._update_step_ui(); self._update_mode_ui(); self._refresh_toggles()
        self._refresh(); self._compute_body()
        self._mark_clean()                                   # 復元直後は保存時と同じ＝『未保存の変更なし』
        self.statusBar().showMessage(L("Restored saved state.", "保存された作業状態を復元しました。"), 6000)

    # ---------- Undo（直前の1手だけ戻す・多段履歴ではない） ----------
    def _on_session_deleted(self, study_uid, slot):
        """患者リスト側でスロットが削除された：今その患者を開いていればビューアの保存ボタンも更新。
        （別オブジェクトのカタログではなく同一カタログを共有しているので削除自体は反映済み。
        ここは *見た目* を合わせるだけ。同じセッションで削除→再保存が確実にできるようにする。）"""
        if study_uid == self.current_study_uid:
            self._update_save_buttons()
            self._mark_dirty_if_unsaved()                     # 削除で作業が宙に浮くなら閉じる時に保存を促す

    def _update_save_buttons(self):
        patient = self._current_patient_label()
        for n, b in zip((1, 2, 3), self.saveBtns):
            saved = bool(self.current_study_uid) and self.catalog.has_session(self.current_study_uid, n)
            b.setStyleSheet(SS_ON if saved else SS_OFF)
            if not self.current_study_uid:
                b.setToolTip(L("Open a patient from the list first.", "先に患者リストから患者を開いてください。"))
            else:
                who = patient or L("this patient", "この患者")
                b.setToolTip(L(
                    f"Slot {n} — {who}: save the current IVC path, Entry/Target, actual needle tip, "
                    f"and view settings. Right-click to delete." + (" [saved]" if saved else " [empty]"),
                    f"スロット{n} — {who}: 今のIVCパス・Entry/Target・実際の針先・表示設定を保存します。"
                    f"右クリックで削除。" + ("【保存済み】" if saved else "【未保存】")))

    def _ortho_world(self, plane, col, row):
        """断面クリック(col,row) → world mm。Axial=(x,y)@z=cz / Coronal=(x,N-1-z)@y=cy / Sagittal=(y,N-1-z)@x=cx。"""
        v = self.vol; nz, H, W = v.shape
        if plane == 0:
            return np.array([col * v.sx, row * v.sy, self.cz * v.dz])
        if plane == 1:
            return np.array([col * v.sx, self.cy * v.sy, ((nz - 1) - row) * v.dz])
        return np.array([self.cx * v.sx, col * v.sy, ((nz - 1) - row) * v.dz])

    def _hep_add(self, world):
        """手動肝静脈: 今描いている血管に点を追加（無ければ新規開始）。"""
        self._snap_undo()
        if not self.hep_veins:
            self.hep_veins.append([])
        self.hep_veins[-1].append(np.asarray(world, float))
        self._refresh()

    def _toggle_hep_mode(self):
        """手動肝静脈の描画モード ON/OFF。ONにしたら新しい血管を1本描き始める。
        OFF（終了）にすると空の血管を掃除し、折れ線＋節点はなだらかな曲線に整う。"""
        self.hep_mode = self.hepBtn.isChecked()
        if self.hep_mode:
            if not self.hep_veins or self.hep_veins[-1]:
                self.hep_veins.append([])                    # 空の血管が末尾に無ければ新規開始
            self.statusBar().showMessage(
                L("Draw a hepatic vein: click along it on any CT pane. “+ vein” starts another; "
                  "“Finish” ends and smooths the curve.",
                  "肝静脈を描く: 3断面のどれかで血管に沿ってクリック。「＋新しい血管」で次の1本、"
                  "「完了」で終了（点が消えてなだらかな曲線になります）。"), 0)
        else:
            self.hep_veins = [v for v in self.hep_veins if len(v) >= 1]   # 空の血管を掃除
            self.statusBar().showMessage(L("Hepatic vein drawing finished (smoothed).",
                                           "肝静脈の描画を終了しました（なだらかに整えました）。"), 3000)
        self._sync_hep_ui()
        self._refresh()

    def _hep_finish(self):
        """肝静脈の描画を終了（＝描画モードOFF）。折れ線＋節点が滑らかな曲線に描き直される。"""
        if self.hep_mode:
            self.hepBtn.setChecked(False); self._toggle_hep_mode()

    def _hep_new_vein(self):
        """次の肝静脈を描き始める（今の血管を確定して空の血管を追加）。"""
        if not self.hep_mode:
            self.hepBtn.setChecked(True); self._toggle_hep_mode(); return
        if self.hep_veins and self.hep_veins[-1]:
            self.hep_veins.append([]); self._refresh()

    def _clear_hep(self):
        """手動で描いた肝静脈をすべて消去。"""
        if self.hep_veins:
            self._snap_undo(); self.hep_veins = []
            if self.hep_mode:
                self.hep_veins.append([])
            self._refresh()

    def _sync_hep_ui(self):
        """手動肝静脈ボタンの見た目を状態に同期。"""
        if hasattr(self, "hepBtn"):
            self.hepBtn.setChecked(self.hep_mode)
            self.hepBtn.setStyleSheet(SS_ON if self.hep_mode else SS_OFF)

    # ---------- AI分離の手動再指定（枝ごと 門脈⇔肝静脈）----------
    def _toggle_vein_edit(self):
        """血管訂正モード ON/OFF（先生要望 2026-07-21 の作り直し）。
        ON にすると、AI が分けた門脈(青)/肝静脈(ローズ)を **CT 断面に自動で重ねて描く**。
        枝をクリックすると、その枝が白く浮き出て「門脈／肝静脈」を選ぶピッカーが出る。"""
        self.vein_edit = self.veinEditBtn.isChecked()
        if self.vein_edit and self.hep_mode:                 # 手動肝静脈モードとは排他（クリックの取り合いを防ぐ）
            self.hepBtn.setChecked(False); self._toggle_hep_mode()
        self._vein_sel = None
        self._sync_vein_ui()
        if self.vein_edit:
            import ts_seg
            has_ai = bool(getattr(self, "_ts_key", None)) and ts_seg.available()
            if self._vein_lab is None and (self.p3d.ts_portal is not None or self.p3d.ts_hepatic is not None or has_ai):
                self._rerender_ts()                          # 訂正モード＝暫定分離を必ず出す（force_vein_split）
            if self._vein_lab is None:
                self.statusBar().showMessage(
                    L("Run Structure AI first — there is no vessel tree to correct yet.",
                      "先に構造AIを実行してください（訂正する血管がまだありません）。"), 6000)
            else:
                self.statusBar().showMessage(
                    L("Correct the vessel type: click a branch on any CT pane — it highlights, then "
                      "choose Portal or Hepatic vein.",
                      "血管の種類を訂正: 3断面のどれかで枝をクリックすると白く浮き出ます。"
                      "続いて「門脈／肝静脈」を選んでください。"), 0)
            self._rerender_ts()                              # CT へ血管を描く（overlay は _refresh 内）
        else:
            self.statusBar().clearMessage()
            self._rerender_ts()                              # 単色/信頼度に基づく通常表示へ戻す
        self._refresh()

    def _vein_reassign(self, world):
        """血管訂正モードでCTをクリック：枝を浮き出させ、門脈/肝静脈を選ぶピッカーを出す。"""
        if self.vol is None or self._vein_lab is None:
            self.statusBar().showMessage(
                L("Run Structure AI first.", "先に構造AIを実行してください。"), 5000)
            return
        import ts_seg
        v = self.vol
        br = ts_seg.vein_branch_at(self._vein_lab, np.asarray(world, float), v.sx, v.sy, v.dz)
        if br is None:
            self.statusBar().showMessage(
                L("No vessel there — click on a blue or rose vessel.",
                  "そこには血管がありません。青（門脈）かローズ（肝静脈）の血管をクリックしてください。"), 4000)
            return
        # 枝を白く浮き出させてから種類を尋ねる
        self._vein_sel = br["center"]                        # ハイライト位置（重心）
        self._refresh()
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(L("This vessel is…", "この血管は…"))
        cur = L("portal (blue)", "門脈（青）") if br["is_portal"] else L("hepatic vein (rose)", "肝静脈（ローズ）")
        box.setText(L(f"Currently marked as {cur}. Set this branch to:",
                      f"今は {cur} です。この枝を次に設定:"))
        pb = box.addButton(L("Portal vein", "門脈"), QMessageBox.AcceptRole)
        hb = box.addButton(L("Hepatic vein", "肝静脈"), QMessageBox.AcceptRole)
        box.addButton(L("Cancel", "キャンセル"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton()
        self._vein_sel = None
        if chosen not in (pb, hb):
            self._refresh(); return
        to = "portal" if chosen is pb else "hepatic"
        self._snap_undo()
        self.vein_overrides = list(self.vein_overrides) + [
            dict(pt=[float(world[0]), float(world[1]), float(world[2])], to=to)]
        self._rerender_ts()                                  # キャッシュから再構築＝AI再実行なし・速い

    def _clear_vein_overrides(self):
        """手動再指定をすべて取り消し（AIの推定分離に戻す）。"""
        if self.vein_overrides:
            self._snap_undo(); self.vein_overrides = []; self._vein_sel = None
            self._rerender_ts()

    def _draw_vein_overlay(self, p, to_widget, plane):
        """血管訂正モード中、門脈(青)/肝静脈(ローズ)を CT 断面へ重ねて描く（現在スライス近傍だけ）。
        選択中の枝は白く強調（浮き出し）。3D 点群(ts_portal/ts_hepatic)を proj_mm でこの断面へ投影する。
        面外距離は断面ごとに直接計算（Axial=z / Coronal=y / Sagittal=x が“面の軸”）。"""
        if not self.vein_edit or self.vol is None:
            return
        v = self.vol; nz = v.shape[0]
        # この断面の位置(mm)と、点のどの座標が面外かを決める
        if plane == 0:
            slab = self.cz * v.dz; ax = 2                     # Axial: 面の軸 = z
        elif plane == 1:
            slab = self.cy * v.sy; ax = 1                     # Coronal: y
        else:
            slab = self.cx * v.sx; ax = 0                     # Sagittal: x
        import ts_seg
        layers = [(getattr(self.p3d, "ts_portal", None), ts_seg.ORGAN_COLORS["portal_vein_and_splenic_vein"]),
                  (getattr(self.p3d, "ts_hepatic", None), (222, 104, 120))]
        p.setPen(Qt.NoPen)
        for pts, col in layers:
            if pts is None or not len(pts):
                continue
            arr = np.asarray(pts, float)[::2]                 # 間引いて軽く
            near = np.abs(arr[:, ax] - slab) <= 3.0           # 走査断面の近くだけ（±3mm）
            if not near.any():
                continue
            qc = QColor(col[0], col[1], col[2], 160)
            p.setBrush(qc)
            for P in arr[near]:
                cc, rr = core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)
                p.drawEllipse(to_widget(cc, rr), 2.2, 2.2)
        if self._vein_sel is not None:                        # 選択中の枝を白リングで浮き出させる
            cc, rr = core.proj_mm(self._vein_sel, v.sx, v.sy, v.dz, plane, nz)
            w = to_widget(cc, rr)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(255, 255, 255), 2.4))
            p.drawEllipse(w, 12, 12); p.drawEllipse(w, 7, 7)

    def _sync_vein_ui(self):
        if hasattr(self, "veinEditBtn"):
            self.veinEditBtn.setChecked(self.vein_edit)
            self.veinEditBtn.setStyleSheet(SS_ON if self.vein_edit else SS_OFF)

    def _hep_polys(self):
        """描画用: 手動肝静脈を [np.array(N,3) world mm, ...] で返す（空の血管は除く）。"""
        return [np.asarray(v, float) for v in self.hep_veins if len(v) >= 1]

    def _axial_click(self, col, row):
        if self.vol is None:
            return
        if self.vein_edit:                                   # 血管の手動再指定＝クリックした枝を門脈⇔肝静脈
            self._vein_reassign(self._ortho_world(0, col, row)); return
        if self.hep_mode:                                    # 手動肝静脈モード＝クリックで点を打つ
            self._hep_add(self._ortho_world(0, col, row)); return
        self._snap_undo()
        if self.viewMode == "surface":                       # 経腹: Axialの皮膚へプローブ設置
            self._place_probe(0, col, row); return
        if self.step == 0:                                   # IVC パス
            self.path.append([float(self.cz), float(row), float(col)])
            if len(self.path) >= 2:
                self.sProbe.setEnabled(True); self.zP = max(p[0] for p in self.path) if self.tipHighZ else min(p[0] for p in self.path)
        else:                                                # Axis→Entry→Target を順に
            world = np.array([col * self.vol.sx, row * self.vol.sy, self.cz * self.vol.dz])
            self._set_point(world)
        self._refresh()

    def _set_point(self, world):
        """3点方式: ①Axis(軸の手前) → ②Entry(刺入点) → ③Target(狙い) を順に確定。"""
        if self.ptMode == 0:
            self.entry = world; self.ptMode = 1              # ①Entry(刺入点)→②Target(門脈)
        else:
            self.target = world
        self._update_step_ui()

    def _ortho_click(self, plane, col, row):
        """Coronal/Sagittal クリック=参照位置(十字)移動。経腹モードではプローブ設置。"""
        if self.vol is None:
            return
        if self.vein_edit:                                   # 血管の手動再指定（どの断面でも枝をクリック）
            self._vein_reassign(self._ortho_world(plane, col, row)); return
        if self.hep_mode:                                    # 手動肝静脈モード＝Coronal/Sagittalでも点を打てる
            self._hep_add(self._ortho_world(plane, col, row)); return
        if self.viewMode == "surface":                       # 経腹: この断面の皮膚へプローブ設置
            self._place_probe(plane, col, row); return
        nz, H, W = self.vol.shape
        if plane == 1:                                   # Coronal: (x, N-1-z)
            self.cx = int(np.clip(round(col), 0, W - 1)); self.cz = int(np.clip(round((nz - 1) - row), 0, nz - 1))
        else:                                            # Sagittal: (y, N-1-z)
            self.cy = int(np.clip(round(col), 0, H - 1)); self.cz = int(np.clip(round((nz - 1) - row), 0, nz - 1))
        self._refresh()

    def _ice_to_world(self, col, row):
        """ICE表示画素(col,row) → world mm（rot/flip逆変換込み）。無効なら None。"""
        g = self._ice_geom
        if g is None or self._ice_wi <= 0:
            return None
        c = (self._ice_wi - 1 - col) if self.flip else col   # col,row は to_image がロール逆変換済み
        lat = (c - self._ice_wi / 2.0) * core.PXMM; depth = row * core.PXMM
        Tp, Vp, Sp = np.asarray(g["Tp"]), np.asarray(g["Vp"]), np.asarray(g["Sp"])
        return Tp + depth * Vp + lat * Sp

    def _ice_click(self, col, row):
        """ICE画像クリック→ Plotモードなら針先プロット／通常はEntry/Targetに逆投影。Step2のみ。"""
        if self.step != 1:
            return
        world = self._ice_to_world(col, row)
        if world is None:
            return
        self._snap_undo()
        if self.aimMode:                       # 『実際の針』モード＝Entry(固定)→クリックした点(針先)で自動的に組む
            if self.entry is None:
                self.statusBar().showMessage(L("Set Entry (the puncture point) first.",
                                               "先に Entry（刺入点）を設定してください。"), 6000); return
            self.aim_tip = world
            self._refresh(); return
        if self.predict:                       # 予習モード＝実際の針先をプロット
            self.obs.append(world); self._refresh(); return
        self._set_point(world); self._refresh()

    def _move_point(self, pid, col, row, plane):
        if self.vol is None:
            return
        if pid == "contact":                                 # 経腹プローブを皮膚上でドラッグ移動
            self._place_probe(plane, col, row); return
        v = self.vol; nz = v.shape[0]
        pt = self.entry if pid == "entry" else self.target
        if pt is None:
            return
        pt = pt.copy()
        if plane == 0:
            pt[0] = col * v.sx; pt[1] = row * v.sy
        elif plane == 1:
            pt[0] = col * v.sx; pt[2] = ((nz - 1) - row) * v.dz
        else:
            pt[1] = col * v.sy; pt[2] = ((nz - 1) - row) * v.dz
        if pid == "entry":
            self.entry = pt
        else:
            self.target = pt
        self._refresh()

    def _on_move_label(self, pkey, lid, dx, dy):
        """画像上の文字ラベル(Entry/Target/実際の針)をドラッグして動かす（先生要望 2026-07-21）。
        (画面, ラベルid) ごとに移動量(dx,dy)を積算し、その画面だけ描き直す。"""
        key = (pkey, lid)
        ox, oy = self.label_offsets.get(key, (0.0, 0.0))
        self.label_offsets[key] = (ox + float(dx), oy + float(dy))
        pane = {"ax": self.ax, "cor": self.cor, "sag": self.sag, "ice": self.ice}.get(pkey)
        if pane is not None:
            pane.update()

    def _probe_to(self, v):
        if len(self.path) >= 2:
            zs = [p[0] for p in self.path]; self.zP = min(zs) + (max(zs) - min(zs)) * v / 100.0
            self.cz = int(np.clip(round(self.zP), 0, self.vol.shape[0] - 1)); self._refresh()

    def _set_probe(self, v):
        if self.lock3:
            self._lock3_hold = True                  # ロック中の手動押し引き＝以後この軸は術者のもの（θのみ追従）
        self._probe_to(v)
    # θ を手で回す入口。3点固定モード中は θ が「解かれる量」なので、どの入口からも受け付けない
    # （受け付けて直後に上書きすると、ホイールが効かない・壊れている、という見え方になる）。
    def _toggle_lock3(self):
        """3点固定モード。ON にした瞬間に θ＋押し引き（経腹は θ＋あおり）を同時に解いて
        Entry / Target を画像面へ乗せ切り（2026-07-18 完全版）、以後は θ のみで追従する。
        偏向（と、手動介入後の押し引き/あおり）は先生の手に残る（2026-07-15 決裁の趣旨は維持）。"""
        self.lock3 = self.lock3Btn.isChecked()
        self._lock3_key = None; self._lock3_hold = False     # ON/OFF いずれも「乗せ直し」状態をリセット
        self.lock3Btn.setText(L("◎ 3-point lock: ON", "◎ 3点固定: ON") if self.lock3
                              else L("◎ 3-point lock: OFF", "◎ 3点固定: OFF"))
        if not self.lock3:
            self._lock3 = None
        self._refresh()
        if self.lock3 and self._lock3 is None:               # ONにしたのに解けなかった＝条件が足りない
            msg = (L("3-point lock needs a probe on the skin, an Entry and a Target.",
                     "3点固定には、体表にプローブを置き、Entry と Target を設定してください。")
                   if self.viewMode == "surface" else
                   L("3-point lock needs an IVC path, an Entry and a Target.",
                     "3点固定には、IVCパス・Entry・Target が必要です。"))
            self.statusBar().showMessage(msg, 6000)

    def _apply_lock3(self):
        """3点固定（2026-07-18 完全版）。_refresh の先頭で毎回呼ぶ。

        ・「乗せ直し」＝ON直後 / Entry・Target・パス・モードが変わった時：
          θ と「もう1軸」（血管内ICE=押し引き zP、経腹=あおり b1）を同時に解いて乗せ切る
          （未知数2=拘束2なので残差ほぼ0。全域探索 ~0.4s はこの時だけ）。
        ・以後の連続追従＝ θ のみ（軽い・偏向ドラッグにも追従。従来どおり）。
        ・ロック中に押し引き（経腹はあおり）を手で動かしたら、その軸は術者へ返し θ のみ続行。
        残差は隠さず mm で出す（先生決裁）。"""
        self._lock3 = None
        if not (self.lock3 and self.vol is not None
                and self.entry is not None and self.target is not None):
            self._lock3_key = None
            return
        v = self.vol
        key = (self.viewMode, tuple(np.round(self.entry, 3)), tuple(np.round(self.target, 3)),
               len(self.path),
               tuple(np.round(self.path[0], 3)) if self.path else None,
               tuple(np.round(self.path[-1], 3)) if self.path else None,
               None if self.contact is None else tuple(np.round(self.contact, 3)))
        if key != self._lock3_key:
            self._lock3_hold = False                          # 点やパスを触った＝もう一度乗せ直してよい
        land = (key != self._lock3_key) and not self._lock3_hold
        if self.viewMode == "surface":                        # 経腹: θ(+あおり) で3点を扇面へ
            if self.contact is None or self.normal is None:
                return
            if land:
                s = core.solve_surface_3points2(self.contact, self.normal, self.b1, self.b2,
                                                self.entry, self.target, self._surf_plane_axis(),
                                                v.sx, v.sy, v.dz)
                if s is not None:
                    self.b1 = float(s["tilt"])
                    self.sB1.blockSignals(True); self.sB1.setValue(int(round(self.b1))); self.sB1.blockSignals(False)
            else:
                s = core.solve_surface_3points(self.contact, self.normal, self.b1, self.b2,
                                               self.entry, self.target, self._surf_plane_axis(),
                                               v.sx, v.sy, v.dz)
        else:                                                 # 血管内ICE: θ(+押し引き) で画像面へ
            if len(self.path) < 2:
                return
            if land:
                s = core.solve_theta_pos_3points(self.path, self.zP, self.b1, self.b2, v.sx, v.sy, v.dz,
                                                 self.entry, self.target, tip_high_z=self.tipHighZ)
                if s is not None:
                    self.zP = float(s["pos"])
                    zs = [pnt[0] for pnt in self.path]; rngz = max(zs) - min(zs)
                    if rngz > 0:                              # 押し引きスライダへ反映（信号は出さない）
                        pf = (self.zP - min(zs)) / rngz * 100.0
                        self.sProbe.blockSignals(True); self.sProbe.setValue(int(round(pf))); self.sProbe.blockSignals(False)
            else:
                s = core.solve_theta_3points(self.path, self.zP, self.b1, self.b2, v.sx, v.sy, v.dz,
                                             self.entry, self.target, tip_high_z=self.tipHighZ)
        if s is None:
            return
        if land:
            self._lock3_key = key
        self.theta = float(s["theta"]) % 360.0
        self._lock3 = s
        self.sTheta.blockSignals(True)                        # 解いた値をスライダへ（信号は出さない＝再入しない）
        self.sTheta.setValue(int(round(self.theta)) % 360)
        self.sTheta.blockSignals(False)
    def _set_b1(self, v):
        if self.lock3 and self.viewMode == "surface":
            self._lock3_hold = True                  # 経腹ロック中の手動あおり＝以後この軸は術者のもの
        self.b1 = float(v); self._refresh()
    def _update_mode_ui(self):
        """経腹モードでは操作ウィジェットをHandleControl→SurfaceProbeControlへ丸ごと切り替える
        （先生指定2026-07-14：ラベルの読み替えではなく専用ウィジェットへスイッチ）。"""
        surf = (self.viewMode == "surface")
        self.ctlStack.setCurrentIndex(1 if surf else 0)      # 3点固定スイッチごと切り替え（高さは不変）
        for w in (self.lblProbe, self.probeFoot, self.sProbe, self.probeHead):
            w.setVisible(not surf)                            # 経腹は push-pull なし
        self._sync_handle()
        self.ax.caption = (L("Axial  (click/drag = place probe on skin)", "Axial（クリック/ドラッグ = 体表にプローブ）")
                           if surf else L("Axial  (click = IVC path / Entry-Target)", "Axial（クリック = IVCパス / Entry-Target）"))
        self.cor.caption = (L("Coronal  (click/drag = place probe on skin)", "Coronal（クリック/ドラッグ = 体表にプローブ）")
                            if surf else "Coronal")
        self.sag.caption = (L("Sagittal  (click/drag = place probe on skin)", "Sagittal（クリック/ドラッグ = 体表にプローブ）")
                            if surf else "Sagittal")
        self.ice.caption = (L("Transabdominal echo  (convex probe, CT-look)", "経腹エコー（体表コンベックス・CT見え）")
                            if surf else L("ICE view  (wheel = Rotate θ)", "ICE像（ホイール = θ回転）"))
        for pn in (self.ax, self.cor, self.sag, self.ice):
            pn.update()
    def _clear_path(self):
        self._snap_undo()
        self.path = []; self.sProbe.setEnabled(False)
        self.liver = None; self._liver_key = None; self.p3d.liver = None
        self._refresh()
    def _toggle_aim(self):
        """『実際の針先』ボタン。ONの間、ICEクリックは Entry/Target でなく aim_tip を更新する。
        **OFF にしたら、置いた針そのものを消す。**

        以前は OFF にしても「入力モードを抜ける」だけで、描いた針は残り続けた。しかも針だけを
        消す手段がどこにも無く（Clear ▸ Needle は Entry/Target ごと消える）、一度置いたら
        二度と消せなかった（先生報告 2026-07-15）。再描画すら呼んでいなかった。
        消す前に Undo を積むので、間違えて消しても ⌘Z で戻せる。"""
        on = self.aimBtn.isChecked()
        if not on and self.aim_tip is not None:
            self._snap_undo()                                # 消す前に1手だけ戻せるようにする
            self.aim_tip = None; self.aim_torque = 0.0
            self.hubWidget.set_torque(0.0)
        self.aimMode = on
        self._refresh_toggles(); self._refresh()

    def _tick_dash(self):
        """予測点線を先端→進行方向へ流す点線アニメーション。
        CT/ICEは実際の針(aim_tip)、3Dは金属針(Entry→Target)にも流れる線を出すので条件を広げる。"""
        if self.entry is None or (self.aim_tip is None and self.target is None):
            return
        self._dash_phase = (self._dash_phase + 1.4) % 20.0
        self.p3d.dash_phase = self._dash_phase                # 3Dの進行方向ダッシュへ供給
        for pane in (self.ice, self.ax, self.cor, self.sag, self.p3d):
            pane.update()

    _DIR_EN = {"左": "left", "右": "right", "背側": "dorsal", "腹側": "ventral", "頭側": "cranial", "尾側": "caudal"}

    def _update_ice_info(self):
        """ICE画像『下』のテキスト窓（画像に文字を重ねない）。直進穿刺長・同一断面チェック・実際の針→Targetの位置関係。"""
        nh = self._needle()
        if nh is None:
            self.iceInfo.setText(""); return
        lines = ["<span style='color:#ffd246;font-weight:bold;'>"
                 + L(f"Straight path Entry → Target: {nh.get('length', 0):.0f} mm",
                     f"Entry → Target 直線距離: {nh.get('length', 0):.0f} mm") + "</span>"]
        g = self._ice_geom
        if self.lock3 and self._lock3 is not None:           # 3点固定モード: 解いた結果と残差をそのまま出す
            s = self._lock3
            oe, ot, res = s["off_entry"], s["off_target"], s["resid"]
            vE = float(s.get("vis_entry", 0.0)); vT = float(s.get("vis_target", 0.0))
            worst = max(res, vE, vT)                         # 扇の外にはみ出していれば色も正直に落とす
            col = "#5fd282" if worst < 2.0 else ("#ffd246" if worst < 8.0 else "#F08F69")
            lines.append(f"<span style='color:{col};'>"
                         + L(f"◎ 3-point lock ON — θ solved to {self.theta:.0f}°. "
                             f"Off-plane: Entry {oe:+.1f} mm / Target {ot:+.1f} mm",
                             f"◎ 3点固定 ON — θを {self.theta:.0f}° に自動調整。"
                             f"面外のズレ: Entry {oe:+.1f} mm / Target {ot:+.1f} mm") + "</span>")
            if max(vE, vT) >= 2.0:                           # 面上でも扇の絵に入り切らない＝正直に言う
                lines.append("<span style='color:#F08F69;'>"
                             + L(f"⚠ This Entry/Target pair does not fit in one ICE fan "
                                 f"(outside the sector: Entry {vE:.0f} mm / Target {vT:.0f} mm). "
                                 "The lock keeps the best compromise — check them on the CT panes.",
                                 f"⚠ この Entry と Target は1枚のICE扇に同時に入り切りません"
                                 f"（扇の外: Entry {vE:.0f} mm / Target {vT:.0f} mm）。"
                                 "最善の妥協位置を保持しています。CT断面での確認も併用してください。") + "</span>")
            elif res >= 2.0:
                lines.append("<span style='color:#9fb4c8;'>"
                             + L("This is the geometric floor for θ + push-pull with this anatomy "
                                 "(both points kept inside the fan). Nudge deflection (A-P / L-R) to "
                                 "close the rest; the lock keeps following.",
                                 "この配置で θ＋押し引きで詰められる下限です（3点とも扇の中を維持）。"
                                 "残りは偏向（A-P / L-R）を少し操作すると詰まります（ロックは追従します）。") + "</span>")
        elif self.viewMode == "ice" and g is not None and self.entry is not None and self.target is not None:
            cp = core.ice_coplanarity(g, self.entry, self.target)
            oe, ot = cp["off_entry"], cp["off_target"]
            worst = max(abs(oe), abs(ot))
            if worst < 8.0:
                lines.append("<span style='color:#5fd282;'>"
                             + L(f"✓ Entry and Target both lie in this ICE image plane "
                                 f"(Entry {oe:+.0f} mm / Target {ot:+.0f} mm off-plane)",
                                 f"✓ EntryとTargetはこのICE断面にほぼ乗っています"
                                 f"（面外 Entry {oe:+.0f} mm / Target {ot:+.0f} mm）") + "</span>")
            else:
                dth = ((cp["best_theta"] - self.theta + 90.0) % 180.0) - 90.0
                lines.append("<span style='color:#F08F69;'>"
                             + L(f"Entry and Target are NOT both in this ICE image plane "
                                 f"(Entry {oe:+.0f} mm / Target {ot:+.0f} mm off-plane) "
                                 f"— rotate θ by {dth:+.0f}° to bring the whole path into view",
                                 f"EntryとTargetがこのICE断面に同時に乗っていません"
                                 f"（面外 Entry {oe:+.0f} mm / Target {ot:+.0f} mm）"
                                 f"— θを {dth:+.0f}° 回すと針路全体が同一断面に近づきます") + "</span>")
        if self.entry is not None and self.aim_tip is not None and self.target is not None:
            orient = self.vol.meta.get("orient") if self.vol is not None else None
            ar = core.aim_readout(self.aim_tip, self.target, orient)
            if ar["has_orient"]:
                d = dict(ar["comps"])                        # ラベルは日本語（tips_core正本）→英語表示時のみ変換
                parts = ", ".join(f"{k if i18n.lang() == 'ja' else self._DIR_EN.get(k, k)} {v:.0f} mm"
                                  for k, v in d.items() if v >= 1.0) or L("almost exactly aligned", "ほぼ一致")
                lines.append("<span style='color:#fffaeb;'>"
                             + L(f"Actual needle tip → Target: ≈{ar['dist']:.0f} mm ({parts})",
                                 f"実際の針先 → Target: 約{ar['dist']:.0f} mm（{parts}）") + "</span>")
            else:
                lines.append("<span style='color:#fffaeb;'>"
                             + L(f"Actual needle tip → Target: ≈{ar['dist']:.0f} mm "
                                 f"(direction unknown — no patient-orientation data)",
                                 f"実際の針先 → Target: 約{ar['dist']:.0f} mm"
                                 f"（向きは不明 — 患者体位情報がありません）") + "</span>")
        self.iceInfo.setText("<br>".join(lines))

    def _set_insertion(self, fem):
        self._snap_undo()
        self.tipHighZ = fem
        if len(self.path) >= 2:
            zs = [p[0] for p in self.path]; self.zP = max(zs) if fem else min(zs)
            self.cz = int(np.clip(round(self.zP), 0, self.vol.shape[0] - 1))
        self._update_step_ui(); self._refresh()

    def _refresh(self):
        if self.vol is None:
            return
        self.p3d.hep_manual = self._hep_polys()              # 手動肝静脈を3Dへ同期
        self.p3d.hep_drawing = self.hep_mode                 # 描画中＝点＋直線／終了＝滑らか曲線
        self._apply_lock3()                                  # 3点固定モード: 幾何を作る前に θ を解く
        v = self.vol; nz, H, W = v.shape
        a = core.ortho_image(v.array, v.sx, v.sy, v.dz, 0, self.cz, self.wl, self.ww); self.ax.set_image(*a)
        c = core.ortho_image(v.array, v.sx, v.sy, v.dz, 1, self.cy, self.wl, self.ww); self.cor.set_image(*c)
        s = core.ortho_image(v.array, v.sx, v.sy, v.dz, 2, self.cx, self.wl, self.ww); self.sag.set_image(*s)
        self.cAx.set_range(nz, self.cz); self.cCor.set_range(H, self.cy); self.cSag.set_range(W, self.cx)
        if len(self.path) >= 2:                               # ICE縦バー=プローブ前後（下部スライダーと同期）
            zs = [p[0] for p in self.path]; rng = max(zs) - min(zs)
            pf = 0 if rng <= 0 else (self.zP - min(zs)) / rng * 100
            self.cIce.set_range(101, pf)
            self.sProbe.blockSignals(True); self.sProbe.setValue(int(pf)); self.sProbe.blockSignals(False)
        else:
            self.cIce.set_range(0, 0)
        g = self._geom(); self._ice_geom = g
        if g is not None:
            out = core.ice_image(v.array, v.sx, v.sy, v.dz, g, self.wl, self.ww, self.flip)
            if out is not None:
                im, pw, ph = out; self._ice_wi = im.shape[1]; self._ice_hi = im.shape[0]
                if self.ct_echo_filter:                   # エコー風フィルタ（表示だけ・幾何や計測は不変）
                    im = core.echo_filter(im)
                self.ice.roll_deg = self.iceRoll          # 無段階ロール表示（表示のみ・幾何は不変）
                self.ice.set_image(im, pw, ph)
        else:
            self.ice.img = None; self.ice.update()
        self.p3d.body = self.body                            # 経腹モードは3Dに体表シェルを表示
        self.p3d.show_body = (self.viewMode == "surface")
        self._build_3d(g)
        self._maybe_compute_liver()                          # IVCパス確定で肝臓を背景抽出
        self._update_ice_info()                              # ICE画像下のテキスト窓（画像に文字を重ねない）
        self.b1Val.setText(f"{self.b1:+.0f}°"); self.b2Val.setText(f"{self.b2:+.0f}°")
        self._sync_handle()                                  # Activeハンドルの絵を現在値に同期

    # ---------- 画像上の点ラベル（フォントサイズ追従＋下地つき） ----------
    def _lbl_pt(self, base_size):
        """画像上のラベル（Entry/Target/実際の針）の点サイズを、設定のフォントサイズに追従させる
        （先生要望 2026-07-21：フォントサイズ変更が画像ラベルに効いていなかった）。base_size は
        OS標準サイズ基準の見た目（13/16 など）。"""
        base = max(1, int(getattr(self, "_base_pt", 12)))
        return max(6, int(round(base_size * getattr(self, "_font_pt", base) / base)))

    def _draw_label(self, p, pos, text, color, pt, bold=False, pkey=None, lid=None, anchor=None, sink=None):
        """CT/ICE の背景に埋もれないよう、半透明の角丸の下地を敷いてから点ラベルを描く
        （先生要望 2026-07-21：背景CTと混じって読みにくい）。pos = drawText のベースライン基準点。
        pkey/lid を渡すと、ユーザーがドラッグして動かした量(self.label_offsets)を反映し、動かした時は
        引き出し線で元の点(anchor)と結ぶ。sink(list) に (lid, 矩形) を積んでドラッグ判定に使う。"""
        f = QFont(); f.setPointSize(int(pt)); f.setBold(bold)
        fm = QFontMetricsF(f)
        off = self.label_offsets.get((pkey, lid)) if (pkey and lid) else None
        if off is not None:                                  # ドラッグで動かした分を反映
            pos = QPointF(pos.x() + off[0], pos.y() + off[1])
            if anchor is not None:                           # 動かした＝どの点のラベルか分かるよう引き出し線
                p.setPen(QPen(color, 1.0, Qt.DotLine)); p.setBrush(Qt.NoBrush)
                p.drawLine(anchor, QPointF(pos.x() - 2.0, pos.y() - fm.ascent() / 2.0))
        tw = fm.horizontalAdvance(text); asc = fm.ascent(); desc = fm.descent(); pad = 3.0
        rect = QRectF(pos.x() - pad, pos.y() - asc - pad, tw + 2 * pad, asc + desc + 2 * pad)
        p.setPen(Qt.NoPen); p.setBrush(QColor(10, 16, 26, 175)); p.drawRoundedRect(rect, 4, 4)
        p.setFont(f); p.setPen(color); p.drawText(pos, text); p.setFont(QFont())
        if sink is not None and lid:
            sink.append((lid, rect))

    # ---------- オーバーレイ（CT 3断面） ----------
    def _overlay(self, p, to_widget, plane):
        if self.vol is None:
            return
        v = self.vol; nz = v.shape[0]; g = self._geom()
        if self.vein_edit:                                   # 血管訂正モード＝門脈/肝静脈をCTへ重ねる（背景層）
            self._draw_vein_overlay(p, to_widget, plane)
        if g is not None:                                    # 扇ゴースト
            poly = core.fan_fill_for_plane(g, v.sx, v.sy, v.dz, plane, nz)
            if poly:
                p.setBrush(QColor(255, 210, 63, 30)); p.setPen(QPen(QColor(255, 210, 63, 130), 1))
                p.drawPolygon(QPolygonF([to_widget(cc, rr) for cc, rr in poly]))
                bm = core.fan_beam_for_plane(g, v.sx, v.sy, v.dz, plane, nz)
                p.setPen(QPen(QColor(255, 240, 160, 200), 1, Qt.DashLine)); p.drawLine(to_widget(*bm[0]), to_widget(*bm[1]))
        if plane in (1, 2) and self.viewMode != "surface" and len(self.path) >= 2:   # Coronal/Sagittal
            # カテーテル本体は背景の参考表示＝Entry/Target/実際の針など主要な描画より先(下)に描き、
            # 上に重なって隠してしまわないようにする（先生報告「実際の針が描画できていない」の原因）。
            self._draw_catheter_body(p, to_widget, v, plane, nz)
        if self.viewMode == "surface" and self.contact is not None:   # 経腹プローブ（コンベックス形状）
            # プローブは Axial / Coronal / Sagittal の **3断面すべて** に描く（先生指示 2026-07-14）。
            # 以前は置いた断面にしか実体を描かず、他の2断面では水色の点しか出なかったので、
            # 「どちらを向いているか」が置いた断面でしか読めなかった。probe_glyph は world mm の
            # 3D 点列なので、proj_mm でどの断面にも同じように投影できる。
            # 現在スライスから外れている断面では淡く描く（Entry/Target の強調規則と同じ考え方）。
            if g is not None:
                gl = core.probe_glyph(g)
                _pm = lambda P: to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz))
                ci = (self.contact[2] / v.dz, self.contact[1] / v.sy, self.contact[0] / v.sx)[plane]
                on = abs(ci - (self.cz, self.cy, self.cx)[plane]) <= 1.5      # プローブがこの断面上にある
                ka = 1.0 if on else 0.42                                      # 断面外＝薄いゴースト
                p.setBrush(QColor(201, 210, 221, int(150 * ka)))
                p.setPen(QPen(QColor(120, 132, 150, int(255 * ka)), 2 if on else 1.2))
                p.drawPolygon(QPolygonF([_pm(P) for P in gl["outline"]]))     # 白い筐体
                p.setBrush(QColor(80, 146, 196, int(220 * ka)))
                p.setPen(QPen(QColor(42, 90, 138, int(255 * ka)), 1.5 if on else 1))
                p.drawPolygon(QPolygonF([_pm(P) for P in gl["array"]]))       # 青いアレイ凸面
                p.setBrush(QColor(120, 172, 212, int(255 * ka)))
                p.setPen(QPen(QColor(120, 132, 150, int(255 * ka)), 1))
                p.drawEllipse(_pm(gl["button"]), 3, 3)
            cc, rr = core.proj_mm(self.contact, v.sx, v.sy, v.dz, plane, nz)  # 皮膚接触点そのもの
            p.setBrush(CYAN); p.setPen(QPen(Qt.white, 1.5)); p.drawEllipse(to_widget(cc, rr), 4, 4)
        nd = self._needle()                                  # 針(機構dict: カニューラ＋針)
        if nd is not None:
            self._draw_device(p, nd, lambda P: to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)))
        # IVC パス中心線（クリック点をz順に結ぶ線）＋点（赤リング）。
        # 経腹モードではプローブは体表にあり、IVC の中を通るカテーテルの経路は関係が無い。3Dペインは
        # 既に shaft=None で隠しているのに 2D だけ描き続けていて、画面が食い違っていた（先生指摘）。
        if self.viewMode != "surface" and len(self.path) >= 2:
            sp = sorted(self.path, key=lambda q: q[0])
            line = [to_widget(*core.proj_mm([x * v.sx, y * v.sy, z * v.dz], v.sx, v.sy, v.dz, plane, nz))
                    for (z, y, x) in sp]
            ivc_alpha = 255 if self.step == 0 else 120            # Step1(IVCパス編集中)は濃く、Step2以降は薄く＝重なるICE扇を隠さない
            p.setPen(QPen(QColor(95, 205, 235, ivc_alpha), 2)); p.setBrush(Qt.NoBrush); p.drawPolyline(QPolygonF(line))
        # パスのクリック点（赤丸）は **ICE ルート抽出中（Step1 = ICEセットアップ／自動抽出）だけ** 表示する
        # （先生指示 2026-07-21）。Step2 以降は Target（赤）と紛れて画面が読みにくいだけなので出さない。
        show_dots = (self.viewMode != "surface" and self.step == 0)
        p.setBrush(Qt.NoBrush); p.setPen(QPen(REDC, 2))
        for (z, y, x) in (self.path if show_dots else []):
            cc, rr = core.proj_mm([x * v.sx, y * v.sy, z * v.dz], v.sx, v.sy, v.dz, plane, nz)
            p.drawEllipse(to_widget(cc, rr), 4, 4)
        # 手動で描いた肝静脈（ローズ）＝各断面に proj_mm で投影。ただし現在スライスから面外に
        # 離れた所は薄くし、十分離れたら消す（先生指示：Axで描いた線が遠いスライスまで残ると違和感）。
        # 描画中は折れ線＋節点、終了後はなだらかな曲線。
        _axis = (2, 1, 0)[plane]                             # 面外方向の座標: Axial=z(点[2]) / Cor=y(点[1]) / Sag=x(点[0])
        _cur = (self.cz * v.dz, self.cy * v.sy, self.cx * v.sx)[plane]   # 現在スライスの mm 位置
        _NEAR, _FAR = 2.0, 10.0                              # NEAR以内=濃い / FARで消える(mm)

        def _hep_a(P):
            d = abs(float(P[_axis]) - _cur)
            return 1.0 if d <= _NEAR else (0.0 if d >= _FAR else (_FAR - d) / (_FAR - _NEAR))
        for vein in self._hep_polys():
            pts = list(vein) if self.hep_mode else list(core.catmull_rom(vein))
            proj = [(to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)), _hep_a(P)) for P in pts]
            for i in range(len(proj) - 1):                   # セグメントごとに面外距離でフェード
                (w1, a1), (w2, a2) = proj[i], proj[i + 1]
                a = (a1 + a2) * 0.5
                if a <= 0.03:                                # 両端とも十分離れている＝描かない
                    continue
                p.setPen(QPen(QColor(226, 110, 128, int(235 * a)), 3)); p.setBrush(Qt.NoBrush)
                p.drawLine(w1, w2)
            if self.hep_mode:                                # 描画中の節点は現在スライス近くだけ表示
                p.setBrush(QColor(226, 110, 128)); p.setPen(QPen(Qt.white, 1))
                for (w2, a) in proj:
                    if a > 0.5:
                        p.drawEllipse(w2, 2.6, 2.6)
        # Entry / Target（緑/赤・ラベル・ドラッグ可）。現在スライスが真の点と一致したら強調
        hits = []; lbl_boxes = []                            # lbl_boxes = 文字ラベルの矩形（ドラッグ判定用）
        pkey = ("ax", "cor", "sag")[plane]
        cur = (self.cz, self.cy, self.cx)[plane]
        for pid, pt, col in (("entry", self.entry, GREENC), ("target", self.target, REDC)):
            if pt is None:
                continue
            idx = round((pt[2] / v.dz, pt[1] / v.sy, pt[0] / v.sx)[plane])   # この断面での点のスライス番号
            emph = abs(idx - cur) <= 1                                       # ±1スライスで一致とみなす
            cc, rr = core.proj_mm(pt, v.sx, v.sy, v.dz, plane, nz); w = to_widget(cc, rr)
            r = 8 if emph else 4
            p.setBrush(col); p.setPen(QPen(Qt.white, 2 if emph else 1)); p.drawEllipse(w, r, r)
            if emph:                                                         # 外周リング＋太字ラベル
                p.setBrush(Qt.NoBrush); p.setPen(QPen(col, 2)); p.drawEllipse(w, r + 6, r + 6)
            self._draw_label(p, w + QPointF(r + 4, -r),          # フォントサイズ追従＋半透明の下地・ドラッグ移動可
                             pid.capitalize() + (" ◀ on slice" if emph else ""),
                             col, self._lbl_pt(16 if emph else 13), bold=emph,
                             pkey=pkey, lid=pid, anchor=w, sink=lbl_boxes)
            hits.append((pid, cc, rr))
        p.setFont(QFont())
        try:                                                    # 『実際の針』描画：ここが失敗しても他の描画を巻き添えにしない
            def _emph_at(pt):                                    # 現在スライスが真の点と一致したら強調(Entry/Targetと同基準)
                idx = round((pt[2] / v.dz, pt[1] / v.sy, pt[0] / v.sx)[plane])
                return abs(idx - cur) <= 1
            if self.entry is not None and self.aim_tip is not None:   # 実際の針＝Entry(固定)→針先。半透明の物体として重ねる
                et = _emph_at(self.aim_tip)                       # 現在スライスが針先と一致＝針そのものを強調
                ng = core.needle_glyph(self.entry, self.aim_tip)
                poly = QPolygonF([to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)) for P in ng["outline"]])
                p.setBrush(QColor(NEEDLE_COL.red(), NEEDLE_COL.green(), NEEDLE_COL.blue(), 215 if et else 110))
                p.setPen(QPen(NEEDLE_COL, 2.6 if et else 1.3)); p.drawPolygon(poly)
                pred = core.predict_curve(self.entry, self.aim_tip, radius=core.COLA_R,   # Colapinto想定2cm予測(目立たせる＝太く・流れるダッシュ)
                                          span_deg=np.degrees(20.0 / core.COLA_R), torque_deg=self.aim_torque)
                pp = QPolygonF([to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)) for P in pred])
                pen = QPen(QColor(255, 255, 255, 235), 3.2, Qt.CustomDashLine)
                pen.setDashPattern([3, 2]); pen.setDashOffset(-self._dash_phase); pen.setCapStyle(Qt.RoundCap)  # 負= 先端→進行方向へ流す
                p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPolyline(pp)
                tw = to_widget(*core.proj_mm(self.aim_tip, v.sx, v.sy, v.dz, plane, nz))
                if et:
                    p.setPen(QPen(NEEDLE_COL, 2)); p.setBrush(Qt.NoBrush); p.drawEllipse(tw, 9, 9)
                self._draw_label(p, tw + QPointF(11, -6),
                                 L("Actual needle", "実際の針") + (" ◀ on slice" if et else ""),
                                 NEEDLE_COL, self._lbl_pt(15 if et else 12), bold=et,
                                 pkey=pkey, lid="aim", anchor=tw, sink=lbl_boxes)
                if self.target is not None:                       # Targetまでの残り＝細く控えめ
                    gcc, grr = core.proj_mm(self.target, v.sx, v.sy, v.dz, plane, nz)
                    p.setPen(QPen(QColor(NEEDLE_COL.red(), NEEDLE_COL.green(), NEEDLE_COL.blue(), 110), 1, Qt.DashLine))
                    p.drawLine(tw, to_widget(gcc, grr))
        except Exception:
            pass
        p.setFont(QFont()); p.setBrush(Qt.NoBrush)
        if self.viewMode == "surface" and self.contact is not None:   # プローブ全体を掴んで動かせるように
            if g is not None and plane == self.surfPlane:
                for P in core.probe_glyph(g)["outline"]:
                    hits.append(("contact",) + tuple(core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz)))
            cc, rr = core.proj_mm(self.contact, v.sx, v.sy, v.dz, plane, nz)
            hits.append(("contact", cc, rr))
        pane = (self.ax, self.cor, self.sag)[plane]; pane.hit_points = hits
        pane.label_boxes = lbl_boxes                         # 文字ラベルのドラッグ判定に使う矩形
        # 参照十字（シアン・中央ギャップ）: 他断面の現在位置を示す
        if plane == 0:
            xc, yc = self.cx, self.cy
        elif plane == 1:
            xc, yc = self.cx, (nz - 1) - self.cz
        else:
            xc, yc = self.cy, (nz - 1) - self.cz
        w = to_widget(xc, yc); p.setPen(QPen(CYAN, 1)); gp = 6
        p.drawLine(w + QPointF(-22, 0), w + QPointF(-gp, 0)); p.drawLine(w + QPointF(gp, 0), w + QPointF(22, 0))
        p.drawLine(w + QPointF(0, -22), w + QPointF(0, -gp)); p.drawLine(w + QPointF(0, gp), w + QPointF(0, 22))
        pw = self._pred_world()                              # 予習モード：CTミラー
        if pw is not None:
            self._paint_pred(p, pw, lambda Q: to_widget(*core.proj_mm(Q, v.sx, v.sy, v.dz, plane, nz)), label=False)

    def _draw_catheter_body(self, p, to_widget, v, plane, nz):
        """3D連動パネルで動いているカテーテル本体(灰シャフト＋偏向で曲がる先端=オレンジ)を、
        同じワールド座標(mm)のままCoronal/Sagittal断面へ投影して実際の位置に重ねる。
        3Dパネルの絵とは別に静的な参考アイコンを置くのではなく、ICEの軌跡(IVCパス)上に
        今どう曲がっているカテーテルが乗っているかをCT断面自体の上で見せる
        （先生指摘：右下の小さいアイコンは不要／軌跡上に視覚化してほしい・2026-07-12）。"""
        try:
            b = core.bend_tip(self.path, self.zP, self.theta, self.b1, self.b2,
                              v.sx, v.sy, v.dz, tip_high_z=self.tipHighZ)
        except Exception:
            b = None
        if b is None:
            return
        def to_pt(P):
            return to_widget(*core.proj_mm(P, v.sx, v.sy, v.dz, plane, nz))
        if b.get("shaft") is not None and len(b["shaft"]) >= 2:
            pts = QPolygonF([to_pt(P) for P in b["shaft"]])
            p.setPen(QPen(QColor(20, 26, 36, 170), 10)); p.setBrush(Qt.NoBrush)   # 濃色の縁取りで浮かせる
            p.drawPolyline(pts)
            p.setPen(QPen(QColor(220, 232, 242, 235), 6)); p.setBrush(Qt.NoBrush)
            p.drawPolyline(pts)
        if b.get("orange") is not None and len(b["orange"]) >= 2:
            pts = QPolygonF([to_pt(P) for P in b["orange"]])
            p.setPen(QPen(QColor(20, 26, 36, 170), 11)); p.setBrush(Qt.NoBrush)
            p.drawPolyline(pts)
            p.setPen(QPen(QColor(255, 150, 55, 235), 7)); p.setBrush(Qt.NoBrush)
            p.drawPolyline(pts)

    # ---------- オーバーレイ（ICE） ----------
    def _ice_structure_layers(self):
        """ICE像に重ねるAI構造 [(点群 Nx3 mm, (r,g,b)), …]。3Dに出ている物と同じ集合＝見え方が一致する。
        主シーンの IVC/門脈/肝血管ツリー＋チェックリストで選んだ臓器（ts_organs）。"""
        import ts_seg
        out = []
        if not getattr(self.p3d, "show_ts", False):
            return out
        for pts, col in ((getattr(self.p3d, "ts_ivc", None), ts_seg.ORGAN_COLORS["inferior_vena_cava"]),
                         (getattr(self.p3d, "ts_portal", None), ts_seg.ORGAN_COLORS["portal_vein_and_splenic_vein"]),
                         (getattr(self.p3d, "ts_hepatic", None), (222, 104, 120))):
            if pts is not None and len(pts):
                out.append((np.asarray(pts, float), col))
        for name, pts in (getattr(self.p3d, "ts_organs", None) or {}).items():
            if pts is not None and len(pts):
                out.append((np.asarray(pts, float), ts_seg.ORGAN_COLORS.get(name, (200, 200, 200))))
        return out

    def _draw_ice_organs(self, p, to_widget, Tp, Vp, Sp, n0, Wi, geom=None, near_mm=3.5, cap=1200):
        """走査面の近く(±near_mm)にある構造の点だけを、その構造の色で淡く点描する。

        ICEは毎フレーム（進行方向ダッシュのアニメ 60ms）再描画されるので、点ごとの Python ループでは
        重すぎる。面外距離の判定と面内座標の計算は numpy でまとめて行い、**実際に描くのは面の近くに
        残った点だけ**にする（数十万点でも数ms）。それでも多い時は間引いて cap 点までに抑える。
        """
        Tp = np.asarray(Tp, float); Vp = np.asarray(Vp, float); Sp = np.asarray(Sp, float)
        n0 = np.asarray(n0, float)
        nn = float(np.linalg.norm(n0))
        if nn < 1e-9:
            return
        n0 = n0 / nn
        geom = geom or self._ice_geom or {}
        g_R = float(geom.get("R", core.R_DEPTH)); g_r0 = float(geom.get("r0", 0.0))
        g_fan = float(geom.get("fan_half", core.FAN_HALF))
        p.setPen(Qt.NoPen)
        for pts, col in self._ice_structure_layers():
            w = pts - Tp
            near = np.abs(w @ n0) <= near_mm
            if not near.any():
                continue
            ws = w[near]
            # **扇の絵の中だけに描く**。無限平面に投影しただけだと、実際には映らない位置（深達の先・
            # 扇角の外・背側）にも点が出て「エコーに写っていない物が写っているように」見える
            # （3点固定で直したのと同じ原則）。
            depth = ws @ Vp; lat = ws @ Sp
            rr = np.hypot(depth, lat)
            inside = (rr <= float(g_R)) & (rr >= float(g_r0)) & \
                     (np.abs(np.arctan2(lat, depth)) <= float(g_fan))
            if not inside.any():
                continue
            ws = ws[inside]; depth = depth[inside]; lat = lat[inside]
            if len(ws) > cap:                                 # 描画点の上限（間引いても見た目の密度は保てる）
                k = int(np.ceil(len(ws) / cap)); depth = depth[::k]; lat = lat[::k]
            colpx = Wi / 2.0 + lat / core.PXMM
            if self.flip:
                colpx = Wi - 1 - colpx
            rowpx = depth / core.PXMM
            p.setBrush(QColor(col[0], col[1], col[2], 90))
            for cpx, rpx in zip(colpx, rowpx):
                p.drawEllipse(to_widget(float(cpx), float(rpx)), 2.0, 2.0)

    def _ice_overlay(self, p, to_widget):
        g = self._ice_geom
        if g is None or self._ice_wi <= 0:
            return
        Tp, Vp, Sp = g["Tp"], g["Vp"], g["Sp"]
        n0 = np.cross(Vp, Sp); Wi = self._ice_wi
        def to_px(Q):
            w = np.asarray(Q) - Tp
            depth = float(w @ Vp); lat = float(w @ Sp); nd = abs(float(w @ n0))
            col = Wi / 2.0 + lat / core.PXMM
            if self.flip:
                col = Wi - 1 - col
            return col, depth / core.PXMM, nd               # ロールは ImagePane.to_widget が吸収
        if self.show_ice_organs:                              # AI構造をICE像に薄く重ねる（背景層＝針や点より先に描く）
            self._draw_ice_organs(p, to_widget, Tp, Vp, Sp, n0, Wi, geom=g)
        labels = []                                          # 点ラベル(Entry/Target/実際の針)を集め、最後に重なり回避して描く
        nh = self._needle()
        if nh is not None:
            self._draw_device(p, nh, lambda P: to_widget(*to_px(P)[:2]))
            best = None; bestd = 1e9                          # 針が断面を貫く位置 ×
            for P in nh["needle"]:
                col, rowp, dist = to_px(P)
                if dist < bestd:
                    bestd = dist; best = (col, rowp)
            if best and bestd < 8:
                w = to_widget(*best); p.setPen(QPen(REDC, 2))
                p.drawLine(w + QPointF(-6, -6), w + QPointF(6, 6)); p.drawLine(w + QPointF(-6, 6), w + QPointF(6, -6))
        try:                                                    # 『実際の針』描画：失敗しても他の描画を巻き添えにしない
            if self.entry is not None and self.aim_tip is not None:   # 実際の針＝Entry(固定)→針先。半透明の物体として重ねる
                _, _, tip_off = to_px(self.aim_tip)
                et = tip_off < 2.5                                # 針先がこのICE断面にほぼ乗っている＝針そのものを強調
                ng = core.needle_glyph(self.entry, self.aim_tip)
                poly = QPolygonF([to_widget(*to_px(P)[:2]) for P in ng["outline"]])
                p.setBrush(QColor(NEEDLE_COL.red(), NEEDLE_COL.green(), NEEDLE_COL.blue(), 215 if et else 110))
                p.setPen(QPen(NEEDLE_COL, 2.6 if et else 1.3)); p.drawPolygon(poly)
                pred = core.predict_curve(self.entry, self.aim_tip, radius=core.COLA_R,   # Colapinto想定2cm予測(目立たせる＝太く・流れるダッシュ)
                                          span_deg=np.degrees(20.0 / core.COLA_R), torque_deg=self.aim_torque)
                pp = QPolygonF([to_widget(*to_px(P)[:2]) for P in pred])
                pen = QPen(QColor(255, 255, 255, 235), 3.2, Qt.CustomDashLine)
                pen.setDashPattern([3, 2]); pen.setDashOffset(-self._dash_phase); pen.setCapStyle(Qt.RoundCap)  # 負= 先端→進行方向へ流す
                p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPolyline(pp)
                w = to_widget(*to_px(self.aim_tip)[:2])
                if et:                                            # 断面に乗った＝リング＋太字ラベル(Entry/Targetと同基準)
                    p.setPen(QPen(NEEDLE_COL, 2)); p.setBrush(Qt.NoBrush); p.drawEllipse(w, 9, 9)
                    labels.append(dict(id="aim", text=L("Actual needle", "実際の針") + " ◀ on plane",
                                       anchor=w, color=NEEDLE_COL, size=15, bold=True, dx=11, dy=-6))
                if self.target is not None:                       # Targetまでの残り＝細く控えめ
                    gx, gy, _ = to_px(self.target)
                    p.setPen(QPen(QColor(NEEDLE_COL.red(), NEEDLE_COL.green(), NEEDLE_COL.blue(), 110), 1, Qt.DashLine))
                    p.drawLine(w, to_widget(gx, gy))
        except Exception:
            pass
        p.setFont(QFont()); p.setBrush(Qt.NoBrush)
        hits = []
        if self.aim_tip is not None:
            c, r, _ = to_px(self.aim_tip); hits.append(("aim_tip", c, r))
        for pid, pt, col in (("entry", self.entry, GREENC), ("target", self.target, REDC)):
            if pt is None:
                continue
            c, r, dist = to_px(pt)
            if dist < 14:
                emph = dist < 2.5                                # ICE平面上に乗ったら強調
                rr2 = 8 if emph else 5
                w = to_widget(c, r); p.setBrush(col); p.setPen(QPen(Qt.white, 2 if emph else 1)); p.drawEllipse(w, rr2, rr2)
                if emph:
                    p.setBrush(Qt.NoBrush); p.setPen(QPen(col, 2)); p.drawEllipse(w, rr2 + 6, rr2 + 6)
                labels.append(dict(id=pid, text=pid.capitalize() + (" ◀ on plane" if emph else ""),
                                   anchor=w, color=col, size=16 if emph else 13, bold=emph, dx=rr2 + 4, dy=-rr2))
                hits.append((pid, c, r))
        p.setFont(QFont())
        self._place_labels(p, labels)                        # 重なったら縦に離し、離れたら引き出し線で点と結ぶ
        self.ice.hit_points = hits
        pw = self._pred_world()                              # 予習モード：プロット追跡＋前方予測
        if pw is not None:
            self._paint_pred(p, pw, lambda Q: to_widget(*to_px(Q)[:2]), label=True)

    @staticmethod
    def _stack_labels(its, H, gap=2.0):
        """重なり回避の縦積み（純粋計算）。its は anchor が上→下の順で並んだ [{'y'(baseline),'asc','h'}...]。
        baseline y を下へ押して重なりを解消し、最下端が H を超えたら全体を上へ寄せる。y を書き換えて返す。"""
        cursor = None
        for it in its:
            top = it["y"] - it["asc"]
            if cursor is not None and top < cursor:
                it["y"] += (cursor - top); top = cursor
            cursor = top + it["h"] + gap
        if its:
            over = (its[-1]["y"] - its[-1]["asc"] + its[-1]["h"]) - (H - 2)
            if over > 0:
                for it in its:
                    it["y"] -= over
        return its

    def _place_labels(self, p, labels):
        """ICE上の点ラベル(Entry/Target/実際の針)を描く。ユーザーがドラッグして動かしたラベルは
        その位置に固定(ピン留め)、動かしていないラベルだけ重なり回避で縦に離す。既定位置から離れた
        ラベルは点線の引き出し線で元の点と結ぶ（先生要望）。ドラッグ判定用に矩形を self.ice.label_boxes へ。
        labels: [{"id","text","anchor":QPointF,"color":QColor,"size":int,"bold":bool,"dx","dy"}]。"""
        if not labels:
            self.ice.label_boxes = []
            return
        W = float(self.ice.width()); H = float(self.ice.height())
        its = []
        for lab in labels:
            f = QFont(); f.setPointSize(self._lbl_pt(lab["size"])); f.setBold(lab["bold"])   # フォントサイズ設定に追従
            fm = QFontMetricsF(f)
            bx = lab["anchor"].x() + lab["dx"]; by = lab["anchor"].y() + lab["dy"]   # 既定=点の右上(既存offset)
            off = self.label_offsets.get(("ice", lab.get("id")))                     # ドラッグで動かした量
            pinned = off is not None
            if pinned:
                bx += off[0]; by += off[1]
            its.append(dict(lab=lab, f=f, tw=fm.horizontalAdvance(lab["text"]), asc=fm.ascent(),
                            h=fm.height(), x=bx, y=by, pinned=pinned))
        auto = [it for it in its if not it["pinned"]]        # 動かしていないラベルだけ自動で重なり回避
        auto.sort(key=lambda it: it["lab"]["anchor"].y())
        self._stack_labels(auto, H)                          # 重なりを下へ押しやり、下端が画面外なら全体を上へ
        boxes = []
        for it in its:
            lab = it["lab"]; a = lab["anchor"]
            x = min(max(it["x"], 2.0), max(2.0, W - it["tw"] - 2.0))      # 左右も画面内に収める
            y = it["y"]
            dfx = a.x() + lab["dx"]; dfy = a.y() + lab["dy"]              # 既定位置
            if abs(x - dfx) > 3.0 or abs(y - dfy) > 3.0:     # 既定から離れた（ドラッグ or 重なり回避）＝引き出し線
                p.setPen(QPen(lab["color"], 1.0, Qt.DotLine)); p.setBrush(Qt.NoBrush)
                p.drawLine(a, QPointF(x - 2.0, y - it["asc"] / 2.0))
            pad = 3.0                                        # 背景CTに埋もれないよう半透明の角丸下地を敷く
            rect = QRectF(x - pad, y - it["asc"] - pad, it["tw"] + 2 * pad, it["h"] + 2 * pad)
            p.setPen(Qt.NoPen); p.setBrush(QColor(10, 16, 26, 175)); p.drawRoundedRect(rect, 4, 4)
            p.setFont(it["f"]); p.setPen(lab["color"])
            p.drawText(QPointF(x, y), lab["text"])
            if lab.get("id"):
                boxes.append((lab["id"], rect))
        p.setFont(QFont())
        self.ice.label_boxes = boxes

    def _update_vessel_btn(self, busy=False):
        """3Dパネル「構造AI」ボタンの見た目を状態に合わせる（未生成/解析中/表示中/非表示）。
        手順グループ先頭の「自動構造描出」（内容は構造AIと同じ）も同じ状態機械に追従させる。"""
        btn = getattr(self.p3d, "vesselBtn", None)
        if btn is None:
            return
        has = self.p3d.ts_liver is not None                  # 肝臓が有れば「生成済み」（ボタンON表示）
        btn.blockSignals(True)
        if busy:
            btn.setText(L("Analyzing…", "解析中…")); btn.setEnabled(False); btn.setChecked(True)
        else:
            btn.setEnabled(self.vol is not None)
            btn.setText(L("Structure AI", "構造AI"))
            btn.setChecked(bool(has and self.p3d.show_ts))
        btn.blockSignals(False)
        fb = getattr(self, "vesselFlowBtn", None)            # 手順グループの「1. 自動構造抽出」
        if fb is not None:
            if busy:
                fb.setText(L("1. Analyzing…", "1. 解析中…")); fb.setEnabled(False)
                fb.setStyleSheet(SS_ON_L)
            else:
                fb.setEnabled(self.vol is not None)
                fb.setText(L("1. 🧠 Auto structures", "1. 🧠 自動構造抽出"))
                fb.setStyleSheet(SS_ON_L if bool(has and self.p3d.show_ts) else SS_OFF_L)
        rb = getattr(self.p3d, "rerenderBtn", None)          # ⟳ は解析中は無効、それ以外はCT有りで有効
        if rb is not None:
            rb.setEnabled(self.vol is not None and not busy)
        rf = getattr(self, "rerenderFlowBtn", None)          # 手順グループへ移設した ⟳ も同じ規則
        if rf is not None:
            rf.setEnabled(self.vol is not None and not busy)

    def _pin_bottom_font(self):
        """下部の操作帯とフッターの文字サイズを基準サイズへ固定する（＝画像の面積を守る）。

        setFont では効かない：アプリ全体にスタイルシートが効いている状態だと、Qt はリサイズや
        再ポリッシュのたびに子ウィジェットのフォントを解決し直し、明示指定を剥がしてしまう
        （実測：resize しただけで 9pt→18pt に戻った）。スタイルシートの font-size は安定して
        勝つので、**帯の子孫に効く QSS ルール**として与える。
        各グループ見出しは自前で font-size:10px を持っており、そちらが優先される（従来どおり）。
        """
        pt = int(self._base_pt)
        for name, holder in (("bottomStrip", getattr(self, "bottomStrip", None)),
                             ("footerBar", getattr(self, "footerBar", None))):
            if holder is None:
                continue
            holder.setObjectName(name)
            holder.setStyleSheet(
                "QWidget#%s{background:#14253a;} "
                "QWidget#%s QPushButton, QWidget#%s QLabel, QWidget#%s QCheckBox{font-size:%dpt;}"
                % (name, name, name, name, pt))

    def _set_font_size(self, pt, save=True):
        """アプリ全体の文字サイズを変更・保存（EUS版と同じ・先生要望 2026-07-20）。

        ただし **下部の操作帯とフッターだけは元の文字サイズに固定**する。ここを一緒に大きくすると
        帯が縦に育ち、その分だけ CT/ICE の表示面積が削られるため（先生の恒久ルール「下部UIの縦は
        これ以上広げない」／番兵テスト test_bottom_ui_height_budget）。**大きくなるのは画像上の
        ラベル・ICE下の情報窓・患者リスト・メニュー・各ダイアログ**＝読みたい所だけが読みやすくなる。
        """
        from PySide6.QtGui import QFont
        self._font_pt = int(pt)                               # 画像上の点ラベル（Entry/Target/実際の針）もこの値に追従（_lbl_pt）
        a = QApplication.instance()
        if a is not None:
            f = QFont(a.font()); f.setPointSize(int(pt)); a.setFont(f)
            # ★ app.setFont / 親への setFont は **既に作られた子ウィジェットには伝播しない**
            #   （Qt の既知の挙動＝新規ウィジェットにしか効かない。先生報告「押しても何も変わらない」の真因）。
            #   起動時に全ウィジェットが揃っているので、生きている子へ 1つずつ配って回る＝確実に反映する。
            #   自前描画のラベル（ペインのキャプション等）は paintEvent で QFont を作り直すので影響なし。
            self.setFont(f)
            for wd in self.findChildren(QWidget):
                wd.setFont(f)
            self._pin_bottom_font()                          # 下部帯は QSS の font-size で据え置き（widget font に勝つ）
        # 保存するのは *先生がメニューで選んだ時だけ*。起動時の適用でも書き戻すと、実行環境ごとに
        # 違う既定サイズ（オフスクリーン検証は 9pt など）が設定に焼き付き、次の起動で文字が極小に
        # なる事故になる。
        if save:
            settings_store.store().setValue("font_size", int(pt))
        for p, act in getattr(self, "_font_acts", {}).items():
            act.setChecked(p == int(pt))
        self._place_ice_chips(); self._reposition_legend()    # 文字サイズで大きさが変わる浮動UIを置き直す
        # 画像上のラベル（Entry/Target/実際の針）は自前描画で _font_pt に追従するが、setFont だけでは
        # CT 3断面が再描画されず古いサイズのまま残ることがある（先生報告：ICEは変わるがAxial/Sagittalが
        # 変わらない＝ICEは _place_ice_chips で再描画されるが CT は取りこぼす）。全ペインを明示的に再描画。
        for pn in (self.ax, self.cor, self.sag, self.ice, getattr(self, "p3d", None)):
            if pn is not None:
                pn.update()

    def _ts_quality(self):
        """解析の細かさ（高精度/標準/速い）。設定に保存・既定は標準。"""
        import ts_seg
        v = settings_store.store().value("ts_quality", ts_seg.QUALITY_DEFAULT)
        return v if v in ts_seg.QUALITY_PRESETS else ts_seg.QUALITY_DEFAULT

    def _set_ts_quality(self, name):
        """精度を変更。キャッシュは精度ごとに別なので、既に解析済みの症例は作り直しになる。"""
        import ts_seg
        settings_store.store().setValue("ts_quality", name)
        for k, act in getattr(self, "_quality_acts", {}).items():
            act.setChecked(k == name)
        self._ts_key = None                              # 次回の表示・生成は新しいキーで
        self.statusBar().showMessage(
            L("Analysis detail: %s. Cases already analysed will be rebuilt when you press Structure AI."
              % ts_seg.QUALITY_PRESETS[name][1][0],
              "解析の細かさ: %s。解析済みの症例は『構造AI』を押すと作り直します。"
              % ts_seg.QUALITY_PRESETS[name][1][1]), 8000)

    def _ts_split(self):
        """門脈/肝静脈の推定分離を行うか（設定・既定ON）。"""
        return bool(settings_store.store().value("ts_split_veins", True, type=bool))

    def _rerender_ts(self):
        """設定変更をAI表示に反映＝キャッシュから build_scene をやり直す（速い）。
        必要な相(肝血管ツリー)がキャッシュに無ければ TS 再解析にフォールバック。"""
        import ts_seg
        if self.vol is None or not ts_seg.available():
            self._update_vessel_btn(); return
        if self._ts_worker is not None and self._ts_worker.isRunning():
            return
        key, cache = self._ts_cache_dir()
        want_vessels = bool(settings_store.store().value("ts_vessels", True, type=bool))
        masks = ts_seg.cached_masks(cache)
        have = masks is not None and "liver" in masks and (not want_vessels or ts_seg.HEPATIC in masks)
        if not have:
            self._compute_ts(); return                       # 材料不足→AI再解析
        self._ts_key = key; self.p3d.show_ts = True
        vol = self.vol; split = self._ts_split(); shown = self._shown_organs()
        self.statusBar().showMessage(L("Re-rendering AI view…", "AI表示を作り直し中…"), 0)
        self._update_vessel_btn(busy=True)
        import bg
        w = bg.Worker(lambda prog: ts_seg.build_scene(masks, vol.sx, vol.sy, vol.dz, split_veins=split,
                                                      show_organs=shown, vein_overrides=self.vein_overrides,
                                                      force_vein_split=self.vein_edit))
        self._ts_worker = w
        w.done.connect(lambda scene, k=key: self._apply_ts_scene(scene, k, announce=True))
        w.failed.connect(lambda _m: (self.statusBar().clearMessage(), setattr(self, "_auto_path_pending", False),
                                     self._update_vessel_btn()))
        w.start()

    def _maybe_compute_liver(self):
        """IVCパス(≥2点)が確定/変化したら肝臓を背景スレッドで自動抽出（モーダル無し）。"""
        if self.vol is None or len(self.path) < 2:
            return
        key = tuple(tuple(round(float(c), 1) for c in p) for p in self.path)
        if key == self._liver_key:
            return
        if self._liver_worker is not None and self._liver_worker.isRunning():
            return                                       # 計算中（完了後に最新パスで再評価）
        self._liver_key = key
        import bg
        vol = self.vol; path = [list(p) for p in self.path]; thz = self.tipHighZ
        w = bg.Worker(lambda prog: liver_core.estimate(vol.array, path, vol.sx, vol.sy, vol.dz, tip_high_z=thz))
        self._liver_worker = w
        w.done.connect(self._on_liver_done)
        w.failed.connect(lambda _m: None)                # ゴーストは任意機能→失敗は黙殺
        w.start()

    def _ts_cache_dir(self):
        """この症例のAIキャッシュ場所（study_uidごとに分離）。key と dir を返す。
        抽出セット(ROIS_VERSION)をキーに混ぜる＝構造セットを変えたら旧キャッシュと別物になり自動で作り直す
        （混ぜないと、抽出構造を増やしても解析済み症例では古いマスクが返り新しい臓器が出ない）。"""
        import catalog, hashlib, ts_seg
        key = self.current_study_uid or ("vol%d" % id(self.vol))
        # 精度もキーに混ぜる：同じ症例でも「高精度/標準/速い」で中身が変わるため、
        # 混ぜないと精度を上げたのに前の粗いマスクが返ってしまう。
        h = hashlib.md5((key + "|" + ts_seg.ROIS_VERSION + "|" + self._ts_quality()).encode()).hexdigest()[:16]
        return key, os.path.join(catalog.app_data_dir(), "ts_cache", h)

    def _load_ts_cached_only(self):
        """CTを開いた直後: AI解剖が既にキャッシュ済みなら即時表示（計算はしない）。
        未計算なら何もしない＝『構造AI』ボタンで生成する（毎回30秒待たせないため）。"""
        self.p3d.ts_liver = self.p3d.ts_ivc = self.p3d.ts_portal = self.p3d.ts_hepatic = None
        self.p3d.ts_organs = {}                          # 追加表示（胆嚢・結腸・肝腫瘍）も一緒に消す
        self._ts_key = None
        if os.environ.get("TIPS_NO_TS") or self.vol is None:
            self._update_vessel_btn(); return
        import ts_seg
        if not ts_seg.available():
            self._update_vessel_btn(); return
        key, cache = self._ts_cache_dir()
        masks = ts_seg.cached_masks(cache)
        if masks is None or "liver" not in masks:            # 肝臓を含む完全キャッシュのみ表示（不完全は構造AIで再生成）
            self._update_vessel_btn(); return
        self._ts_key = key; self.p3d.show_ts = True          # build_scene(数秒)は背景で＝開いた瞬間に固まらない
        vol = self.vol; split = self._ts_split(); shown = self._shown_organs()
        import bg
        w = bg.Worker(lambda prog: ts_seg.build_scene(masks, vol.sx, vol.sy, vol.dz, split_veins=split,
                                                      show_organs=shown, vein_overrides=self.vein_overrides,
                                                      force_vein_split=self.vein_edit))
        self._ts_worker = w
        w.done.connect(lambda scene, k=key: self._apply_ts_scene(scene, k, announce=False))
        w.failed.connect(lambda _m: self._update_vessel_btn())
        self._update_vessel_btn(); w.start()

    def _on_vessel_ai(self):
        """『構造AI』ボタン: 未生成なら生成（肝/IVC/門脈＋設定により肝血管ツリー）、生成済みなら表示ON/OFF。"""
        if self.vol is None:
            self._update_vessel_btn(); return
        busy = self._ts_worker is not None and self._ts_worker.isRunning()
        has = self.p3d.ts_liver is not None                  # 肝臓(アンカー)が有れば完成。IVCだけの不完全は未生成扱い→再生成
        if has and not busy:                                 # 既に生成済み → 表示ON/OFFトグル（導入状況に依らない）
            self.p3d.show_ts = not self.p3d.show_ts; self.p3d.update(); self._update_vessel_btn(); return
        if busy:                                             # 解析中は無視
            self._update_vessel_btn(busy=True); return
        import ts_seg
        if not ts_seg.available():                           # 未導入 → 設定への導線
            self._prompt_install_ts(); self._update_vessel_btn(); return
        self._compute_ts()

    def _compute_ts(self):
        """AI解剖を背景で生成（構造AIボタンから）。肝血管ツリーは設定ON＋ライセンス有りのときだけ含める。"""
        import ts_seg
        if self.vol is None or not ts_seg.available():
            self._update_vessel_btn(); return
        key, cache = self._ts_cache_dir(); self._ts_key = key
        # 肝血管ツリー(肝静脈)は設定ONなら常に要求。モデルが無い/ライセンスが要る環境では
        # ヘルパーが静かにスキップし total(肝/IVC/門脈)だけ返る（壊れない）。
        want_vessels = bool(settings_store.store().value("ts_vessels", True, type=bool))
        vol = self.vol; orient = vol.meta.get("orient")
        device = settings_store.store().value("ts_device", "mps")
        self.p3d.show_ts = True
        self.statusBar().showMessage(
            L("Extracting structures with AI (liver / IVC / portal%s, ~30s)…"
              % (" / hepatic tree" if want_vessels else ""),
              "AIで構造抽出中（肝臓・IVC・門脈%s、約30秒）…"
              % ("・肝血管ツリー" if want_vessels else "")), 0)
        self._update_vessel_btn(busy=True)
        split = self._ts_split(); shown = self._shown_organs(); quality = self._ts_quality()
        from PySide6.QtCore import QThread
        import bg

        def work(prog):
            stop = lambda: QThread.currentThread().isInterruptionRequested()   # 終了時にTSを止める
            masks = ts_seg.segment(vol.array, vol.sx, vol.sy, vol.dz, orient, cache,
                                   device=device, should_stop=stop, vessels=want_vessels,
                                   quality=quality)
            return ts_seg.build_scene(masks, vol.sx, vol.sy, vol.dz, split_veins=split,
                                      show_organs=shown, vein_overrides=self.vein_overrides,
                                      force_vein_split=self.vein_edit)
        w = bg.Worker(lambda prog: work(prog))
        self._ts_worker = w
        w.done.connect(lambda scene, k=key: self._apply_ts_scene(scene, k, announce=True))
        w.failed.connect(lambda _m: (self.statusBar().clearMessage(), setattr(self, "_auto_path_pending", False),
                                     self._update_vessel_btn()))
        w.start()

    def _apply_ts_scene(self, scene, key, announce=True):
        """背景で作った3Dシーンを反映。別症例に切り替わっていたら(key不一致)破棄。"""
        if key != self._ts_key:                              # 開いている症例が変わっていた→捨てる
            self._auto_path_pending = False                  # 別症例へ移った→自動パス待ちも解除
            return
        self.statusBar().clearMessage()
        if not scene:
            if announce:
                self.statusBar().showMessage(L("AI structures not available (using light view).",
                                               "AI構造は使えませんでした（軽量表示のまま）。"), 5000)
            if self._auto_path_pending:                      # AI失敗＝自動パスも出せない
                self._auto_path_pending = False
                self.statusBar().showMessage(
                    L("The AI could not detect the IVC — please draw the path on Axial.",
                      "AIがIVCを検出できませんでした。Axialで手動で描いてください。"), 7000)
            self._update_vessel_btn(); return
        self.p3d.ts_liver = scene.get("liver"); self.p3d.ts_ivc = scene.get("ivc")
        self.p3d.ts_portal = scene.get("portal"); self.p3d.ts_hepatic = scene.get("hepatic")
        self.p3d.ts_organs = scene.get("organs") or {}       # 回避目標（胆嚢・結腸）と肝腫瘍を色分け表示
        self._vein_lab = scene.get("vein_lab")               # 門脈/肝静脈のラベル（CTクリックで枝を拾う）
        self.p3d.show_ts = True; self.p3d.update()
        if announce and not self._auto_path_pending:
            hep = self.p3d.ts_hepatic is not None
            self.statusBar().showMessage(
                L("AI structures ready (liver / IVC / portal%s)." % (" / hepatic tree" if hep else ""),
                  "AI構造を表示しました（肝臓・IVC・門脈%s）。" % ("・肝血管ツリー" if hep else "")), 6000)
        self._update_vessel_btn()
        if self._auto_path_pending:                          # 「AIでIVCパス自動作成」からの実行 → いま軸を引く
            self._auto_path_pending = False
            import ts_seg
            _, cache = self._ts_cache_dir()
            masks = ts_seg.cached_masks(cache)
            ivc = masks.get("inferior_vena_cava") if masks else None
            if ivc is not None:
                self._build_auto_ivc_path(ivc)
            else:
                self.statusBar().showMessage(
                    L("The AI could not detect the IVC — please draw the path on Axial.",
                      "AIがIVCを検出できませんでした。Axialで手動で描いてください。"), 7000)

    def _auto_ivc_path(self):
        """『AIでIVCパス自動作成』ボタン: 構造AIのIVCマスクから ICE の軸(IVCパス)を自動生成する。
        マスクが未生成なら構造AIを走らせ、完了後(_apply_ts_scene)に自動で軸を引く。"""
        if self.vol is None:
            self.statusBar().showMessage(L("Open a CT first.", "先にCTを開いてください。"), 5000); return
        import ts_seg
        _, cache = self._ts_cache_dir()
        masks = ts_seg.cached_masks(cache)
        ivc = masks.get("inferior_vena_cava") if masks else None
        if ivc is not None:                                  # AI済み＝すぐ軸を引く
            self._build_auto_ivc_path(ivc); return
        if not ts_seg.available():                           # 未導入 → 設定への導線
            self._prompt_install_ts(); return
        self._auto_path_pending = True                       # AI完了後に軸を引く
        self.statusBar().showMessage(L("Detecting the IVC with AI, then drawing the axis…",
                                       "AIでIVCを検出してから軸を引きます…"), 0)
        self._compute_ts()

    def _build_auto_ivc_path(self, ivc):
        """IVCマスク→中心線→self.path に反映（下書き）。手動と同じ [z,y,x] index・zP規約に合わせる。"""
        v = self.vol
        if v is None:
            return
        pts = liver_core.ivc_centerline(ivc, dz=v.dz)
        if len(pts) < 2:
            self.statusBar().showMessage(
                L("Could not auto-detect the IVC axis — please draw it on Axial.",
                  "IVCの軸を自動検出できませんでした。Axialで手動で描いてください。"), 7000)
            return
        self._snap_undo()                                    # ⌘Zで元に戻せるように
        self.path = pts
        zs = [p[0] for p in pts]
        self.zP = max(zs) if self.tipHighZ else min(zs)      # 手動時と同じ（先端側にプローブを置く）
        self.sProbe.setEnabled(True)
        self.step = 0; self.ptMode = 0                       # ICEセットアップに戻す
        self._update_step_ui(); self._refresh()
        self.statusBar().showMessage(
            L("AI drew the IVC path (a draft) — drag points to adjust, or Clear if it looks off.",
              "AIがIVCパス（下書き）を引きました。点をドラッグで調整、イマイチなら『消去』で消せます。"), 9000)

    def _prompt_install_ts(self):
        """構造AI(TS)未導入時に、設定への導線を出す。"""
        from PySide6.QtWidgets import QMessageBox
        m = QMessageBox(self); m.setWindowTitle(L("Structure AI", "構造AI"))
        m.setText(L("The structure AI (TotalSegmentator) is not installed yet.\n"
                    "Install it from Settings ▸ AI anatomy (optional, ~2GB, runs fully offline on this Mac).",
                    "構造AI（TotalSegmentator）はまだ導入されていません。\n"
                    "設定 ▸ AI解剖 から導入してください（任意・約2GB・このMac内で完全ローカル動作）。"))
        open_ = m.addButton(L("Open Settings", "設定を開く"), QMessageBox.AcceptRole)
        m.addButton(L("Later", "あとで"), QMessageBox.RejectRole)
        m.exec()
        if m.clickedButton() is open_:
            self._ts_settings()

    def _controls_settings(self):
        """操作方法の一覧と、マウスの割り当てのカスタマイズ（先生指示 2026-07-21）。
        拡大縮小／階調(W/L)／移動 の各ジェスチャを、左/右/中どのボタンで行うか選べる。
        左クリックでの点の配置・点のドラッグは割り当てに関係なく不変（誤設定で操作不能にしない）。"""
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                                       QPushButton, QFrame, QGridLayout)
        import panes
        st = settings_store.store()
        dlg = QDialog(self); dlg.setWindowTitle(L("Controls & customization", "操作方法・カスタマイズ"))
        dlg.setMinimumWidth(560); v = QVBoxLayout(dlg)

        head = QLabel(L("How to operate the images. Zoom / Window-Level / Move can be reassigned to a "
                        "different mouse button below. Left-click always places points (Entry/Target, "
                        "IVC path) — that never changes.",
                        "画像の操作方法です。拡大縮小／階調(W/L)／移動 は、下でマウスのボタンを割り当て直せます。"
                        "左クリックでの点の配置（Entry/Target・IVCパス）は割り当てに関係なく常に有効です。"))
        head.setWordWrap(True); v.addWidget(head)

        # --- マウス割り当て（3ジェスチャ × 左/右/中） ---
        box = QGridLayout(); box.setHorizontalSpacing(10); box.setVerticalSpacing(6)
        btn_opts = [(L("Left drag", "左ドラッグ"), "left"),
                    (L("Right drag", "右ドラッグ"), "right"),
                    (L("Middle drag", "中ドラッグ"), "middle")]
        rows = [("wl", L("Window / Level (contrast)", "階調 W/L（コントラスト）")),
                ("zoom", L("Zoom", "拡大縮小")),
                ("pan", L("Move (pan)", "移動（パン）"))]
        self._gesture_combos = {}
        for r, (key, label) in enumerate(rows):
            box.addWidget(QLabel(label), r, 0)
            cb = QComboBox()
            for txt, val in btn_opts:
                cb.addItem(txt, val)
            cur = panes.gesture_button(key)
            cb.setCurrentIndex(max(0, [o[1] for o in btn_opts].index(cur) if cur in [o[1] for o in btn_opts] else 0))
            box.addWidget(cb, r, 1)
            self._gesture_combos[key] = cb
        v.addLayout(box)

        warn = QLabel(""); warn.setStyleSheet("color:#F08F69;"); v.addWidget(warn)

        def apply_map():
            vals = {k: cb.currentData() for k, cb in self._gesture_combos.items()}
            if len(set(vals.values())) < 3:                  # 同じボタンを2つに割り当てない
                warn.setText(L("Each gesture needs a different button.",
                               "3つの操作にはそれぞれ別のボタンを割り当ててください。"))
                return False
            warn.setText("")
            for k, val in vals.items():
                st.setValue("gesture_%s" % k, val)
            return True

        def reset_map():
            for k, cb in self._gesture_combos.items():
                d = panes._GESTURE_DEFAULTS[k]
                cb.setCurrentIndex([o[1] for o in btn_opts].index(d))
            apply_map()

        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet("color:#39414f;"); v.addWidget(sep)

        # --- そのほかの操作（参照のみ） ---
        ref = QLabel(L(
            "Also available:\n"
            "  • Zoom: mouse wheel / trackpad pinch / ⌘+scroll (centred on the cursor)\n"
            "  • Move: ⌥Option-drag or Space-drag (any pane)\n"
            "  • Right-click: reset zoom/pan of that pane\n"
            "  • 3D panel: drag = rotate, wheel = zoom\n"
            "  • Undo: ⌘Z    •  Reset all views (if an image “jumps”): ⌘0\n"
            "  • Font size: Settings ▸ Font size",
            "そのほかの操作:\n"
            "  • 拡大縮小: マウスホイール / トラックパッドのピンチ / ⌘+スクロール（カーソル中心）\n"
            "  • 移動: ⌥Option＋ドラッグ、または Space＋ドラッグ（どの画面でも）\n"
            "  • 右クリック: その画面の拡大/位置をリセット\n"
            "  • 3Dパネル: ドラッグ=回転、ホイール=拡大縮小\n"
            "  • 元に戻す: ⌘Z    •  表示をリセット（画像が“飛んだ”時）: ⌘0\n"
            "  • 文字サイズ: 設定 ▸ フォントサイズ"))
        ref.setWordWrap(True); ref.setStyleSheet("color:#9fb4c8;"); v.addWidget(ref)

        row = QHBoxLayout()
        rb = QPushButton(L("Reset to defaults", "既定に戻す")); rb.clicked.connect(reset_map); row.addWidget(rb)
        row.addStretch(1)
        ok = QPushButton(L("Save", "保存"))
        ok.clicked.connect(lambda: dlg.accept() if apply_map() else None)
        row.addWidget(ok)
        cancel = QPushButton(L("Close", "閉じる")); cancel.clicked.connect(dlg.reject); row.addWidget(cancel)
        v.addLayout(row)
        dlg.exec()

    def _ts_settings(self):
        """Settings ▸ AI解剖（TotalSegmentator）。導入状況・ワンクリック導入・GPU・肝血管ツリー(要ライセンス)。"""
        import ts_seg, tempfile
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
                                       QPushButton, QLineEdit, QFrame)
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        st = settings_store.store()
        dlg = QDialog(self); dlg.setWindowTitle(L("AI anatomy (TotalSegmentator)", "AI解剖（TotalSegmentator）"))
        dlg.setMinimumWidth(540); v = QVBoxLayout(dlg)
        intro = QLabel(L("Extract liver / IVC / portal vein from the contrast CT with a free AI (TotalSegmentator), "
                         "fully on this Mac (no upload). Press “Structure AI” on the 3D panel to build them. "
                         "Optional and heavy (~2GB) — installed only if you want it.",
                         "無料AI（TotalSegmentator）で造影CTから肝臓・IVC・門脈を抽出し3Dに重ねます（このMac内で完結・"
                         "アップロード無し）。3Dパネルの「構造AI」ボタンで生成します。任意・重い(~2GB)ので希望者だけ導入。"))
        intro.setWordWrap(True); v.addWidget(intro)
        status = QLabel(); v.addWidget(status)

        def refresh():
            status.setText(L("● Installed — ready", "● 導入済み — 使えます") if ts_seg.available()
                           else L("○ Not installed", "○ 未導入"))
        refresh()
        gpuChk = QCheckBox(L("Use GPU (MPS) — faster on Apple Silicon (off = CPU)",
                             "GPU(MPS)を使う — Apple Siliconで高速（OFF=CPU）"))
        gpuChk.setChecked(st.value("ts_device", "mps") != "cpu")
        gpuChk.toggled.connect(lambda b: st.setValue("ts_device", "mps" if b else "cpu"))
        v.addWidget(gpuChk)

        # ── 肝血管ツリー（肝静脈を含む・要 無料ライセンス）
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet("color:#39414f;"); v.addWidget(sep)
        hepChk = QCheckBox(L("Also build the liver vessel tree (incl. hepatic veins)",
                             "肝血管ツリー（肝静脈を含む）も生成する"))
        hepChk.setChecked(bool(st.value("ts_vessels", True, type=bool)))
        hepChk.toggled.connect(lambda b: st.setValue("ts_vessels", bool(b)))
        v.addWidget(hepChk)
        splitChk = QCheckBox(L("Estimate portal vs hepatic veins by connectivity (blue = portal, rose = hepatic)",
                               "門脈と肝静脈を連結性から推定分離（青＝門脈系・ローズ＝肝静脈系）"))
        splitChk.setChecked(bool(st.value("ts_split_veins", True, type=bool)))
        splitChk.toggled.connect(lambda b: st.setValue("ts_split_veins", bool(b)))
        v.addWidget(splitChk)
        splitNote = QLabel(L("Heuristic estimate on a single-phase CT — not exact. Change it, then press ⟳ on the 3D "
                             "panel to re-render (no AI re-run needed).",
                             "単相CTでの連結性ヒューリスティックの推定＝完全ではありません。切替後は3Dパネルの ⟳ で"
                             "作り直せます（AI再解析は不要）。"))
        splitNote.setWordWrap(True); splitNote.setStyleSheet("color:#9aa6b2;"); v.addWidget(splitNote)
        licNote = QLabel(L("The hepatic-vein tree uses TotalSegmentator's liver_vessels model. It often works with no "
                           "extra setup; if downloading the model asks for a free (non-commercial) licence, get a number "
                           "below and paste it. Portal and hepatic branches are shown together in one colour "
                           "(a single-phase CT can't separate them).",
                           "肝静脈ツリーは liver_vessels モデルを使います。多くの環境では追加設定なしで動きますが、"
                           "モデルの取得に無料の非商用ライセンスを求められた場合は下で取得・貼り付けてください。"
                           "門脈枝と肝静脈枝は1色で一緒に表示されます（単相CTでは分離できません）。"))
        licNote.setWordWrap(True); licNote.setStyleSheet("color:#9aa6b2;"); v.addWidget(licNote)
        licRow = QHBoxLayout()
        licEdit = QLineEdit(); licEdit.setText(ts_seg.get_license())
        licEdit.setPlaceholderText(L("License number (e.g. aca_XXXXXXXX)", "ライセンス番号（例 aca_XXXXXXXX）"))
        saveBtn = QPushButton(L("Save", "保存"))
        licRow.addWidget(licEdit, 1); licRow.addWidget(saveBtn); v.addLayout(licRow)
        licRow2 = QHBoxLayout()
        getBtn = QPushButton(L("Get free license…", "無料ライセンスを取得…"))
        getBtn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ts_seg.LICENSE_URL)))
        licStat = QLabel()
        licRow2.addWidget(getBtn); licRow2.addWidget(licStat); licRow2.addStretch(1); v.addLayout(licRow2)

        def licRefresh():
            licStat.setText(L("● License set", "● ライセンス設定済み") if ts_seg.license_set()
                            else L("○ No license — hepatic tree will be skipped", "○ 未設定 — 肝血管ツリーは出ません"))
            licStat.setStyleSheet("color:%s;" % ("#7fd18a" if ts_seg.license_set() else "#c7a86b"))
        licRefresh()

        def saveLic():
            ts_seg.set_license(licEdit.text()); licRefresh()
        saveBtn.clicked.connect(saveLic)

        logLbl = QLabel(""); logLbl.setWordWrap(True); logLbl.setStyleSheet("color:#9aa6b2;"); v.addWidget(logLbl)
        row = QHBoxLayout()
        instBtn = QPushButton(L("Install / Update AI (~2GB)…", "AIを導入/更新（~2GB）…"))
        closeBtn = QPushButton(L("Close", "閉じる"))
        row.addWidget(instBtn); row.addStretch(1); row.addWidget(closeBtn); v.addLayout(row)
        closeBtn.clicked.connect(dlg.accept)
        log_path = os.path.join(tempfile.gettempdir(), "tips_ts_install.log")
        timer = QTimer(dlg)

        def poll():
            try:
                lines = open(log_path, encoding="utf-8").read().strip().splitlines()
                if lines:
                    logLbl.setText(lines[-1][:160])
            except OSError:
                pass
        timer.timeout.connect(poll)

        def do_install():
            open(log_path, "w").close()
            instBtn.setEnabled(False); logLbl.setText(L("Starting… (needs network, several minutes)",
                                                        "開始中…（ネットワーク必要・数分）")); timer.start(700)
            import bg
            w = bg.Worker(lambda prog: ts_seg.install(log_path=log_path))
            self._ts_install_worker = w

            def done(ok):
                timer.stop(); poll(); instBtn.setEnabled(True); refresh()
            w.done.connect(done)
            w.failed.connect(lambda _m: (timer.stop(), instBtn.setEnabled(True)))
            w.start()
        instBtn.clicked.connect(do_install)
        dlg.exec()

    def closeEvent(self, e):
        """終了時、①患者の作業状態が開いていれば保存するか確認（先生要望）、②走行中の背景スレッド
        (肝抽出/DICOM読込)を待つ。QThread が走行中に破棄されると Qt が abort() するため②は必須。"""
        if (self.stack.currentWidget() is self.viewer_page and self.vol is not None
                and self.current_study_uid and self._has_unsaved_changes()):
            resp = QMessageBox.question(self, L("Save before closing?", "閉じる前に保存しますか？"),
                L("Save your current work state (IVC path, Entry/Target, actual needle tip, "
                  "view settings) for this patient before closing?",
                  "閉じる前に、この患者の今の作業状態（IVCパス・Entry/Target・実際の針先・表示設定）を"
                  "保存しますか？"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if resp == QMessageBox.Cancel:
                e.ignore(); return
            if resp == QMessageBox.Yes:
                slot = self._ask_save_slot()                 # どのスロットに保存するか聞く（先生要望 2026-07-21）
                if slot is None:                             # スロット選択でキャンセル＝閉じるのも取りやめ
                    e.ignore(); return
                self._save_slot(slot, notify=False, confirm_overwrite=False)   # 選択で意図確認済み
        self._shutdown()
        super().closeEvent(e)

    def _ask_save_slot(self):
        """終了時「保存」を選んだら、どのスロット(1/2/3)に保存するか尋ねる（先生要望 2026-07-21）。
        各スロットの保存状況（空 / 保存済み）も見せる。キャンセルなら None を返す。"""
        from PySide6.QtWidgets import QMessageBox
        patient = self._current_patient_label() or L("this patient", "この患者")
        box = QMessageBox(self)
        box.setWindowTitle(L("Save to which slot?", "どのスロットに保存しますか？"))
        box.setText(L(f"Choose a save slot for {patient}:", f"{patient}の保存先スロットを選んでください:"))
        btns = {}
        for n in (1, 2, 3):
            used = self.catalog.has_session(self.current_study_uid, n)
            tag = L(" (in use)", "（使用中）") if used else L(" (empty)", "（空き）")
            b = box.addButton(L(f"Slot {n}{tag}", f"スロット{n}{tag}"), QMessageBox.AcceptRole)
            btns[b] = n
        box.addButton(L("Cancel", "キャンセル"), QMessageBox.RejectRole)
        box.exec()
        return btns.get(box.clickedButton())

    # ---------- 3D linkage 構築（plugin update3D 相当） ----------
    def _build_3d(self, g):
        if g is None:
            self.p3d.set_geom(None); return
        v = self.vol
        aim_outline, aim_pred = self._aim_3d()
        if g.get("mode") == "surface":                       # 経腹: カテーテル無し・接触点から扇＋プローブ本体
            Vp = g["Vp"]; Sp = g["Sp"]; R = g["R"]; fh = g["fan_half"]; r0 = g.get("r0", 0.0)
            apex = np.asarray(g["Tp"], float); A = apex - r0 * Vp
            phis = np.linspace(-fh, fh, 25)
            fan = np.vstack([apex] + [A + R * (np.cos(ph) * Vp + np.sin(ph) * Sp) for ph in phis])
            pg = core.probe_glyph(g)                          # コンベックス・プローブ本体（3Dにも乗せる）
            nh = self._needle()
            self.p3d.show_liver = self.show_liver
            self.p3d.liver_mode = self.liver_mode; self.p3d.liver_opacity = self.liver_opacity
            self.p3d.set_geom(dict(shaft=None, orange=None, arr=None, fan=fan, apex=apex,
                                   needle=(nh["full"] if nh else None), cannula=None,
                                   probe_outline=pg["outline"], probe_face=pg["face"],
                                   probe_array=pg["array"], probe_button=pg["button"],
                                   entry=self.entry, target=self.target, liver=self.liver,
                                   aim_outline=aim_outline, aim_pred=aim_pred, aim_tip=self.aim_tip))
            return
        b = core.bend_tip(self.path, self.zP, self.theta, self.b1, self.b2, v.sx, v.sy, v.dz, tip_high_z=self.tipHighZ)
        Tp = b["Tp"]; t1 = b["t1"]; Vp = g["Vp"]; Sp = g["Sp"]; R = g["R"]; fh = g["fan_half"]
        a1 = Tp + 2.5 * Vp; a0 = Tp - 12 * t1 + 2.5 * Vp; amid = (a0 + a1) / 2
        dvt = float(t1 @ Vp); Sp3 = t1 - dvt * Vp
        Sp3 = core.nrm(Sp3) if np.linalg.norm(Sp3) > 1e-6 else Sp
        phis = np.linspace(-fh, fh, 25)
        fan = np.vstack([amid] + [amid + R * (np.cos(ph) * Vp + np.sin(ph) * Sp3) for ph in phis])
        nh = self._needle()
        metal = self._metal_needle_3d()                          # TIPS金属針(外筒＋接続弧＋Chiba針＋進行方向)
        self.p3d.show_liver = self.show_liver                     # ゴースト表示パラメータを同期
        self.p3d.liver_mode = self.liver_mode; self.p3d.liver_opacity = self.liver_opacity
        self.p3d.set_geom(dict(shaft=b["shaft"], orange=b["orange"], arr=np.vstack([a0, a1]),
                               fan=fan, apex=amid, needle=None,          # 旧・単純点線→金属針アセンブリに置換
                               cannula=(nh.get("cannula") if nh else None),
                               entry=self.entry, target=self.target, liver=self.liver,
                               aim_outline=aim_outline, aim_pred=aim_pred, aim_tip=self.aim_tip, **metal))


def main():
    app_boot.run(MainWindow)   # 起動部の実体は app_boot.py（正本 engine/core、Phase 3a）


if __name__ == "__main__":
    main()
