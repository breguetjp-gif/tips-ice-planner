"""TIPS ICE Planner — スタンドアロン・アプリケーション (macOS / Windows)。

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
                           QDesktopServices, QNativeGestureEvent, QPainterPath, QFontMetrics, QKeySequence)
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, Signal, QUrl, QEvent, QUrlQuery, QTimer, QSettings

import tips_core as core
from tips_core import liver as liver_core
import dicom_io
import i18n
from i18n import L
import settings_store
from handle_control import HandleControl, SurfaceProbeControl

GITHUB_REPO = "https://github.com/breguetjp-gif/tips-ice-planner"
AUTHOR_LINE = "Masayoshi Yamamoto — Department of Radiology, Teikyo University School of Medicine, Tokyo, Japan"
VERSION = "0.5.6"                                            # 配布のたびに上げる
URL_SCHEME = "tipsiceplanner"                                # 外部アプリから検査を渡すためのURLスキーム
# 更新確認用 version.json。リポジトリ直下のものを raw で読む（個人のクラウド共有リンクは埋め込まない）。
UPDATE_URL = "https://raw.githubusercontent.com/breguetjp-gif/tips-ice-planner/main/version.json"

# 起動時の「今日のヒント」（VS Code風）。(en, ja) のタプル。0番目はWelcome的な内容にしている。
TIPS_EN_JA = [
    ("Welcome to TIPS ICE Planner! Start with Step 1 (ICE setup): click along the IVC on the "
     "Axial pane to set the probe's path. See Help → User manual for the full walkthrough.",
     "TIPS ICE Plannerへようこそ！まずはStep 1（ICEセットアップ）"
     "から：Axial画面でIVCに沿ってクリックし、"
     "プローブの通り道を設定します。"
     "詳しい手順はヘルプ→使い方説明書をご覧ください。"),
    ("Pinch or ⌘+scroll to zoom any image pane — it stays centered on your cursor.",
     "ピンチまたは⌘+スクロールで拡大縮小できます。"
     "カーソル位置が中心になります。"),
    ("Right-click any pane (including the 3D linkage panel) to reset its zoom and pan.",
     "各画面（3D連動パネルも含む）を右クリックすると、"
     "拡大・位置をリセットできます。"),
    ("Save (1/2/3) keeps up to three working states per patient — Restore them anytime from the patient list.",
     "保存（1・2・3）で患者ごとに最大3つの作業状態を残せます。"
     "呼び戻しは患者リスト画面からいつでもできます。"),
    ("The actual-needle shape is only drawn bold with a slice/plane label when it truly lies in "
     "the cross-section you're viewing right now.",
     "実際の針の形は、今見ている断面に本当に乗っているときだけ、"
     "太く強調表示されラベルが付きます。"),
    ("Switch the whole interface between English and Japanese anytime with the button, top-right.",
     "右上のボタンでいつでも英語⇄日本語を切り替えられます。"),
    ("The Liver ghost overlays a translucent liver outline in 3D to help judge the fan's position.",
     "肝臓ゴーストは、扇の位置関係を把握するための"
     "半透明な肝臓輪郭です。"),
    ("Roll freely rotates the ICE image for a clearer view without changing the underlying geometry.",
     "ロールはICE画像を任意角度で回して見やすくします"
     "（幾何学的な位置関係は変わりません）。"),
    ("You can drag an IVC-path point, or Entry/Target, after placing it to fine-tune its position.",
     "IVCパスの点やEntry/Targetは、置いた後もドラッグで"
     "微調整できます。"),
    ("Clear lets you remove just the IVC path, the needle, or everything — one patient at a time.",
     "クリアでは、IVCパスだけ・針だけ・全部、"
     "と個別に消去できます。"),
    ("Transabdominal mode simulates a surface probe — click the skin on any CT pane to place it.",
     "経腹モードは体表プローブを模擬します。"
     "CT断面の皮膚をクリックして設置します。"),
]

TERRA = QColor(240, 143, 105); CYAN = QColor(80, 200, 220)
GREENC = QColor(95, 210, 130); REDC = QColor(255, 90, 80); AMBER = QColor(255, 200, 70)
NEEDLE_COL = QColor(255, 250, 235)      # 実際の針（物体として半透明で重ねる・エコー輝尾風の白）
STYLE = ("QWidget{background:#0e2236;color:#e2eaf3;} "
         "QPushButton{background:#2b4762;color:#e2eaf3;border:1px solid #43658a;border-radius:5px;padding:3px 9px;} "
         # 明示的なQSS(背景色指定)があるとQtは無効(disabled)状態を自動で暗くしない＝押せないボタンが押せそうに見える
         # 事故(先生指摘：復元ボタンが反応しないように見える)の元だったため、無効状態を明確に暗くする。
         "QPushButton:disabled{background:#16293d;color:#4c5c70;border:1px solid #1c3550;} "
         "QLabel{color:#e2eaf3;} QTreeWidget,QListWidget,QLineEdit{background:#0c1a2a;color:#e2eaf3;} "
         "QHeaderView::section{background:#22405d;color:#e2eaf3;}")
# トグルの選択/非選択は :checked に頼らずボタン毎に明示（macOS/FusionでQSS背景が当たらない問題を回避）
SS_ON = "background:#F08F69;color:#15263a;font-weight:bold;border:1px solid #ffd0bb;border-radius:5px;padding:3px 9px;"
SS_OFF = "background:#2b4762;color:#e2eaf3;border:1px solid #43658a;border-radius:5px;padding:3px 9px;"


def _roll_xy(wx, wy, cx, cy, deg):
    """ウィジェット点(wx,wy)を中心(cx,cy)まわりに deg 度回す（ICE表示の無段階ロール）。"""
    a = np.radians(deg); ca, sa = np.cos(a), np.sin(a)
    dx, dy = wx - cx, wy - cy
    return cx + ca * dx - sa * dy, cy + sa * dx + ca * dy


def _log_click(pane, e, ic):
    """クリック診断ログ（「押した所より左にマークされる」報告の原因調査用）。
    local と mapped の食い違い＝イベント座標の異常、dpr/screen＝ディスプレイ切替起因、を切り分けられる。
    app_data/logs/clicks.log へ追記。失敗しても本体には影響させない。"""
    try:
        import catalog, datetime
        d = os.path.join(catalog.app_data_dir(), "logs"); os.makedirs(d, exist_ok=True)
        f = os.path.join(d, "clicks.log")
        if os.path.exists(f) and os.path.getsize(f) > 1_000_000:
            os.replace(f, f + ".1")                         # 簡易ローテーション（直近+1世代のみ）
        gp = e.globalPosition(); lp = e.position(); mp = pane.mapFromGlobal(gp)
        scr = pane.screen(); sg = scr.geometry() if scr else None
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='milliseconds')} "
                     f"pane={(pane.caption.split() or ['?'])[0]} "
                     f"local=({lp.x():.1f},{lp.y():.1f}) mapped=({mp.x():.1f},{mp.y():.1f}) "
                     f"global=({gp.x():.1f},{gp.y():.1f}) dpr={pane.devicePixelRatioF():.2f} "
                     f"img=({ic[0]:.1f},{ic[1]:.1f}) zoom={pane.zoom:.2f} "
                     f"screen={scr.name() if scr else '?'}"
                     f"{f' {sg.width()}x{sg.height()}' if sg is not None else ''}\n")
    except Exception:
        pass


ACTIVE = QColor(240, 143, 105)            # アクティブ画面の枠色（テラコッタ）
INACTIVE_FRAME = QColor(34, 52, 74)


def _frame(p, w, active):
    """画面の枠。アクティブ＝太いテラコッタ枠、非アクティブ＝細い枠。"""
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(ACTIVE, 3) if active else QPen(INACTIVE_FRAME, 1))
    p.drawRect(w.rect().adjusted(1, 1, -2, -2))


# ============ 操作アイコン（拡大縮小 / 階調 / 移動）============
def _draw_zoom(p, x, y, s, col):
    p.setPen(QPen(col, 2)); p.setBrush(Qt.NoBrush)
    r = s * 0.40; cx, cy = x + r + 1, y + r + 1
    p.drawEllipse(QPointF(cx, cy), r, r)                                  # レンズ
    p.drawLine(QPointF(cx + r * 0.7, cy + r * 0.7), QPointF(x + s, y + s))  # 取っ手
    p.drawLine(QPointF(cx - r * 0.5, cy), QPointF(cx + r * 0.5, cy))      # ＋（拡大縮小）
    p.drawLine(QPointF(cx, cy - r * 0.5), QPointF(cx, cy + r * 0.5))


def _draw_wl(p, x, y, s, col):
    cx, cy = x + s * 0.45, y + s * 0.45; r = s * 0.40
    p.setPen(QPen(col, 2)); p.setBrush(Qt.NoBrush); p.drawEllipse(QPointF(cx, cy), r, r)
    path = QPainterPath(); path.moveTo(cx, cy - r)
    path.arcTo(cx - r, cy - r, 2 * r, 2 * r, 90, -180); path.closeSubpath()
    p.setPen(Qt.NoPen); p.setBrush(col); p.drawPath(path)                # 右半分塗り=コントラスト/階調


def _draw_move(p, x, y, s, col):
    cx, cy = x + s * 0.45, y + s * 0.45; a = s * 0.38; h = s * 0.13
    p.setPen(QPen(col, 2)); p.drawLine(QPointF(cx - a, cy), QPointF(cx + a, cy))
    p.drawLine(QPointF(cx, cy - a), QPointF(cx, cy + a))
    p.setPen(Qt.NoPen); p.setBrush(col)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):                    # 4方向の矢印先端
        ex, ey = cx + dx * a, cy + dy * a
        if dx:
            pts = [QPointF(ex, ey), QPointF(ex - dx * h, ey - h), QPointF(ex - dx * h, ey + h)]
        else:
            pts = [QPointF(ex, ey), QPointF(ex - h, ey - dy * h), QPointF(ex + h, ey - dy * h)]
        p.drawPolygon(QPolygonF(pts))


class GestureBar(QWidget):
    """操作の凡例（拡大縮小 / 階調 / 移動）。horizontal=横1行 / vertical=複合帯の左ブロック（3行積み）。"""

    def __init__(self, vertical=False):
        super().__init__(); self.vertical = vertical
        if vertical:
            self.setFixedWidth(198)                     # 複合帯の左端に縦積み＝縦方向の行数を増やさない
        else:
            self.setFixedHeight(40)
        self.setStyleSheet("background:#14253a;")

    @staticmethod
    def _entries():
        return [(_draw_zoom, L("Zoom", "拡大縮小"), L("Pinch  /  ⌘+scroll", "ピンチ / ⌘+スクロール")),
                (_draw_wl,   L("Window/Level", "階調 (W/L)"), L("Drag", "ドラッグ")),
                (_draw_move, L("Move", "移動"), L("⌥ / Space + drag", "⌥ / Space + ドラッグ"))]

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(0x14, 0x25, 0x3a))
        if self.vertical:                                # 縦積み（アイコン＋タイトル、下に操作方法）
            f1 = QFont(); f1.setPointSize(10); f1.setBold(True)
            f2 = QFont(); f2.setPointSize(8)
            ents = self._entries()
            bh = 34; y0 = max(4, (self.height() - bh * len(ents)) // 2)
            for i, (draw, title, how) in enumerate(ents):
                y = y0 + i * bh
                draw(p, 8, y + 2, 16, ACTIVE)
                p.setFont(f1); p.setPen(ACTIVE); p.drawText(32, y + 13, title)
                p.setFont(f2); p.setPen(QColor(0xc8, 0xd6, 0xe6)); p.drawText(32, y + 27, how)
            return
        f1 = QFont(); f1.setPointSize(11); f1.setBold(True)
        f2 = QFont(); f2.setPointSize(9)
        x = 14; s = 22
        for draw, title, how in self._entries():
            draw(p, x, (self.height() - s) // 2, s, ACTIVE)
            tx = x + s + 9
            p.setFont(f1); p.setPen(ACTIVE); p.drawText(tx, 17, title)
            p.setFont(f2); p.setPen(QColor(0xc8, 0xd6, 0xe6)); p.drawText(tx, 33, how)
            w = max(QFontMetrics(f1).horizontalAdvance(title), QFontMetrics(f2).horizontalAdvance(how))
            x = tx + w + 30


class CannulaHubWidget(QWidget):
    """RUPS-100手元のハブ＋ウイング(羽根状ハンドル)の模式図。文献の『ベースプレートの矢印＝カーブの向き』
    を再現：ウイングに矢印を焼き込み、aim_torque(°)に合わせて回す。術者が手元をこの絵と同じ向きに
    合わせれば、体内での曲がる方向の目安になる（実機の刻印向きとの一致は先生に要確認）。"""
    def __init__(self):
        super().__init__(); self.setFixedSize(64, 76); self.torque_deg = 0.0   # 下部の縦方向を節約（旧72x90）

    def set_torque(self, deg):
        self.torque_deg = deg; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(0x14, 0x25, 0x3a))
        cx, cy = self.width() / 2, self.height() / 2 + 6
        # シャフト（体内へ続く方向・常に上向き＝0°の基準）
        p.setPen(QPen(QColor(120, 150, 180), 4)); p.drawLine(QPointF(cx, cy), QPointF(cx, 6))
        # 下側の尾は角度表示の手前で止める（±0°の文字に線が重なって読みにくかった）
        p.setPen(QPen(QColor(160, 170, 180), 2)); p.drawLine(QPointF(cx, cy), QPointF(cx, self.height() - 18))
        # ハブ（回転体）
        p.save(); p.translate(cx, cy); p.rotate(self.torque_deg)
        p.setBrush(QColor(0xc9, 0xd3, 0xdd)); p.setPen(QPen(QColor(0x8a, 0x96, 0xa4), 1.5))
        p.drawEllipse(QPointF(0, 0), 9, 9)
        # ウイング（羽根状ハンドル）＋矢印＝カーブの向き
        wing = QPolygonF([QPointF(-30, -5), QPointF(30, -5), QPointF(30, 5), QPointF(-30, 5)])
        p.setBrush(QColor(0xe9, 0xed, 0xf2)); p.setPen(QPen(QColor(0x8a, 0x96, 0xa4), 1.2)); p.drawPolygon(wing)
        p.setPen(QPen(TERRA, 2.4)); p.drawLine(QPointF(0, 0), QPointF(26, 0))
        p.drawLine(QPointF(26, 0), QPointF(19, -6)); p.drawLine(QPointF(26, 0), QPointF(19, 6))
        p.restore()
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f); p.setPen(TERRA)
        p.drawText(QRectF(0, self.height() - 16, self.width(), 14), Qt.AlignCenter, f"{self.torque_deg:+.0f}°")


# ============ 3D linkage（QPainter・自由回転） ============
class Pane3D(QWidget):
    activated = Signal(object)            # マウスが入った＝アクティブ画面
    surfacePicked = Signal(int)          # 経腹: 3D体表上でプローブ設置/移動（body["surf"]のindex）

    def __init__(self):
        super().__init__()
        self.az = 0.0; self.el = -75.0; self._last = None    # 初期＝正面(AP/冠状・頭側上)からスタート
        self.shaft = None; self.orange = None; self.arr = None
        self.fan = None; self.needle = None
        self.probe_outline = None; self.probe_face = None    # 経腹コンベックス・プローブ本体（3D）
        self.probe_array = None; self.probe_button = None    # 青いアレイ帯・操作ボタン（実機風の詳細）
        self.entry = None; self.target = None; self.apex = None
        self.valid = False; self.active = False
        self.liver = None; self.show_liver = True            # 肝臓ゴースト
        self.liver_mode = "haze"; self.liver_opacity = 0.5
        self._liver_qimg = None; self._liver_buf = None
        self.body = None; self.show_body = False             # 体表シェル（経腹モードで表示・掴む面）
        self._body_qimg = None; self._body_buf = None
        self.orient = None                                   # index軸→LPS(3x3)。Noneなら標準axial仮定
        self.aim_outline = None; self.aim_pred = None         # 実際の針(半透明)＋Colapinto想定2cm予測(3D)
        self.aim_tip = None                                   # 実際の針先（円柱風の二重線描画に使う実座標）
        self.cannula_rod = None; self.needle_body = None      # TIPS金属針: 外筒＋（接続部＋針シャフト）
        self.needle_tip = None; self.needle_pred = None       # 金属ベベル先端＋進行方向
        self.dash_phase = 0.0                                 # 進行方向の流れるダッシュ位相（_tick_dashが供給）
        self.zoom3d = 1.0; self.pan3d = QPointF(0, 0)         # カーソル中心ズーム（右クリックでリセット）
        self._moving_probe = False                            # 経腹: プローブ/体表を掴んで移動中（掴んだら回転せず追従）
        self.setMinimumSize(260, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def enterEvent(self, e):
        self.activated.emit(self); super().enterEvent(e)

    def set_geom(self, d):
        if d is None:
            self.valid = False
        else:
            self.valid = True
            self.shaft = d.get("shaft"); self.orange = d.get("orange"); self.arr = d.get("arr")
            self.fan = d.get("fan"); self.needle = d.get("needle")
            self.probe_outline = d.get("probe_outline"); self.probe_face = d.get("probe_face")
            self.probe_array = d.get("probe_array"); self.probe_button = d.get("probe_button")
            self.entry = d.get("entry"); self.target = d.get("target"); self.apex = d.get("apex")
            self.aim_outline = d.get("aim_outline"); self.aim_pred = d.get("aim_pred")
            self.aim_tip = d.get("aim_tip")
            self.cannula_rod = d.get("cannula_rod"); self.needle_body = d.get("needle_body")
            self.needle_tip = d.get("needle_tip"); self.needle_pred = d.get("needle_pred")
            if "liver" in d:
                self.liver = d.get("liver")
        self.update()

    def _proj(self, p, c, s, b):
        a = np.radians(self.az); e = np.radians(self.el)
        x, y, z = p[0] - c[0], p[1] - c[1], p[2] - c[2]
        X = np.cos(a) * x - np.sin(a) * y
        Y = np.sin(a) * x + np.cos(a) * y
        Y2 = np.cos(e) * Y - np.sin(e) * z
        seff = s * self.zoom3d
        return QPointF(b.width() / 2 + X * seff + self.pan3d.x(), b.height() / 2 - Y2 * seff + self.pan3d.y())

    def _zoom_at(self, factor, pos, b):
        """カーソル位置(pos)を中心にズーム（画面座標での book-keeping・ImagePane.zoom_atと同じ考え方）。"""
        factor = max(0.5, min(2.0, factor))
        z2 = max(0.2, min(8.0, self.zoom3d * factor))
        f = z2 / self.zoom3d if self.zoom3d > 1e-9 else 1.0   # クランプ後の実効倍率（端でパンだけ滑るのを防ぐ）
        self.zoom3d = z2
        center = QPointF(b.width() / 2.0, b.height() / 2.0)
        self.pan3d = self.pan3d + (pos - center - self.pan3d) * (1.0 - f)
        self.update()

    def _poly(self, pts, c, s, b):
        return [self._proj(p, c, s, b) for p in pts]

    def _stroke(self, p, pts, c, s, b, col, w, dash=False):
        if pts is None or len(pts) < 2:
            return
        path = QPolygonF(self._poly(pts, c, s, b))
        pen = QPen(col, w); pen.setStyle(Qt.DashLine if dash else Qt.SolidLine)
        p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPolyline(path)

    def _flow(self, p, pts, c, s, b, col, w):
        """進行方向の『流れるダッシュ』（CT/ICEの予測線と同じ表現・先端→進行方向へ流す）。"""
        if pts is None or len(pts) < 2:
            return
        path = QPolygonF(self._poly(pts, c, s, b))
        pen = QPen(col, w, Qt.CustomDashLine)
        pen.setDashPattern([3, 2]); pen.setDashOffset(-self.dash_phase); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPolyline(path)

    def _draw_liver(self, p, c, s, b):
        """肝臓ゴーストを最背面に。device幾何と同じ apex(c)/scale(s)/az/el で投影整合。
        ズーム/パンも _proj と同じ値を渡す（渡さないと拡大時に肝臓だけ置き去りになり分離して見える）。"""
        w, h = b.width(), b.height()
        rgba = liver_core.render_ghost(self.liver, self.az, self.el, c, s * self.zoom3d, w, h,
                                       mode=self.liver_mode, opacity=self.liver_opacity,
                                       offset=(self.pan3d.x(), self.pan3d.y()),
                                       ss=1 if self._last is not None else 2)   # ドラッグ中は速度優先
        if rgba is None:
            return
        buf = np.ascontiguousarray(rgba)
        self._liver_buf = buf                              # drawImage 中の GC を防ぐ
        qimg = QImage(buf.data, w, h, 4 * w, QImage.Format_RGBA8888)
        self._liver_qimg = qimg
        p.drawImage(0, 0, qimg)

    def _orient_letters(self):
        """index軸 ±(x,y,z) → 解剖文字。orient(3x3, 列=+x/+y/+z のLPS)無ければ標準axial(HFS)仮定。"""
        M = np.eye(3) if self.orient is None else np.asarray(self.orient, float)

        def lett(v):                                       # LPS: 0=L(+)/R(-), 1=P(+)/A(-), 2=S(+)/I(-)
            i = int(np.argmax(np.abs(v)))
            return (("L", "R"), ("P", "A"), ("S", "I"))[i][0 if v[i] >= 0 else 1]
        return {"+x": lett(M[:, 0]), "-x": lett(-M[:, 0]),
                "+y": lett(M[:, 1]), "-y": lett(-M[:, 1]),
                "+z": lett(M[:, 2]), "-z": lett(-M[:, 2])}

    def _draw_orient_cube(self, p, b):
        """右下の解剖方位キューブ（サイコロ）。本体3Dと同じ az/el で回り「どこから見ているか」を示す羅針盤。"""
        lab = self._orient_letters()
        a = np.radians(self.az); e = np.radians(self.el)

        def rot(v):
            x, y, z = v
            X = np.cos(a) * x - np.sin(a) * y
            Y = np.sin(a) * x + np.cos(a) * y
            return X, np.cos(e) * Y - np.sin(e) * z, np.sin(e) * Y + np.cos(e) * z   # X, Y2(up), depth(手前+)
        R = 20.0; cx = b.width() - R - 20; cy = b.height() - R - 30   # 右下（下部凡例を避ける）
        scr = lambda v: QPointF(cx + rot(v)[0] * R, cy - rot(v)[1] * R)
        faces = [
            ("+x", (1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
            ("-x", (-1, 0, 0), [(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)]),
            ("+y", (0, 1, 0), [(-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)]),
            ("-y", (0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
            ("+z", (0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
            ("-z", (0, 0, -1), [(-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)]),
        ]
        f = QFont(); f.setPixelSize(13); f.setBold(True); p.setFont(f)
        for key, nrm, corners in sorted(faces, key=lambda fc: rot(fc[1])[2]):   # 奥→手前
            front = rot(nrm)[2] > 0
            poly = QPolygonF([scr(c) for c in corners])
            p.setPen(QPen(QColor(120, 150, 180, 200), 1))
            p.setBrush(QBrush(QColor(30, 46, 66, 235 if front else 90)))
            p.drawPolygon(poly)
            if front:                                       # 手前の面だけ文字
                mx = sum(pt.x() for pt in poly) / 4.0; my = sum(pt.y() for pt in poly) / 4.0
                p.setPen(QColor(240, 143, 105))
                p.drawText(QRectF(mx - 11, my - 10, 22, 20), Qt.AlignCenter, lab[key])

    def _surf_frame(self, b):
        """経腹モードの3D投影中心/スケール（体全体が収まるように）。"""
        c = self.body["center"]
        s = min(b.width(), b.height()) / (self.body["extent"] * 1.2 + 1e-3)
        return c, s

    def _draw_body(self, p, c, s, b):
        """体表シェル（陰影付きの皮膚＝解剖図譜相当）。
        視点・ズーム・パン・ペイン寸法が変わらなければ描き直さない：paintEvent は針を動かしても
        走るので、毎回フルレンダリングすると無駄に重い。回転ドラッグ中だけ超解像を切って速度を優先し、
        指を離した瞬間に高画質で描き直す。"""
        w, h = b.width(), b.height()
        ss = 1 if self._last is not None else 2              # ドラッグ中=速度 / 静止=画質
        key = (id(self.body), round(self.az, 2), round(self.el, 2), round(self.zoom3d, 4),
               round(self.pan3d.x(), 1), round(self.pan3d.y(), 1), w, h, ss)
        if key != getattr(self, "_body_ckey", None) or getattr(self, "_body_qimg", None) is None:
            rgba = liver_core.render_ghost(self.body, self.az, self.el, c, s * self.zoom3d, w, h,
                                           mode="surface", opacity=0.97, color=(234, 197, 173),
                                           offset=(self.pan3d.x(), self.pan3d.y()), ss=ss)
            if rgba is None:
                return
            self._body_buf = np.ascontiguousarray(rgba)      # drawImage 中の GC を防ぐ
            self._body_qimg = QImage(self._body_buf.data, w, h, 4 * w, QImage.Format_RGBA8888)
            self._body_ckey = key
        p.drawImage(0, 0, self._body_qimg)

    def _pick_body(self, pos, c, s, b, radius=18.0, nearest=False):
        """カーソル直下の最前面の体表点index。radius内に無ければ None。
        nearest=True（移動中スティッキー）は半径外でも画面上で近い点群から最前面を掴み続ける。"""
        if self.body is None:
            return None
        surf = self.body["surf"]
        u, v, depth = liver_core._project(surf, self.az, self.el, c, s * self.zoom3d, b.width(), b.height(),
                                          offset=(self.pan3d.x(), self.pan3d.y()))
        d2 = (u - pos.x()) ** 2 + (v - pos.y()) ** 2
        within = d2 < (radius * radius)
        if within.any():
            cand = np.where(within)[0]
            return int(cand[np.argmax(depth[cand])])          # 半径内→手前(depth大)を選ぶ
        if nearest:                                           # 半径外でも最近傍40点から最前面（掴んだら離さない）
            k = np.argsort(d2)[:40]
            return int(k[np.argmax(depth[k])])
        return None

    def paintEvent(self, _):
        p = QPainter(self); p.fillRect(self.rect(), QColor(7, 10, 14)); p.setPen(TERRA)
        surf_mode = self.show_body and self.body is not None
        p.drawText(8, 16, L("3D body surface — drag probe/body = move · drag space = rotate · wheel/pinch = zoom · right-click = reset",
                            "3D体表 — プローブ/体表ドラッグ=移動 / 余白ドラッグ=回転 / ホイール・ピンチ=拡大縮小 / 右クリック=リセット")
                   if surf_mode else L("3D linkage (drag to rotate, wheel to zoom, right-click to reset)",
                                       "3D連動（ドラッグ = 回転 / ホイール = 拡大縮小 / 右クリック = リセット）"))
        if not surf_mode and (not self.valid or self.apex is None):
            p.setPen(QColor(140, 140, 140))
            p.drawText(10, self.height() // 2, L("Draw the ICE path to show 3D", "IVCパスを描くと3Dが表示されます"))
            p.setRenderHint(QPainter.Antialiasing, True); self._draw_orient_cube(p, self.rect())
            _frame(p, self, self.active); return
        p.setRenderHint(QPainter.Antialiasing, True)
        b = self.rect()
        if surf_mode:
            c, s = self._surf_frame(b); self._draw_body(p, c, s, b)   # 体表シェルを先に
        else:
            c = self.apex; s = min(b.width(), b.height()) / 210.0
        if self.show_liver and self.liver is not None:        # 肝臓ゴースト（最背面・device幾何の裏）
            self._draw_liver(p, c, s, b)
        if self.fan is not None and len(self.fan) > 2:        # 扇
            poly = QPolygonF(self._poly(self.fan, c, s, b))
            p.setBrush(QBrush(QColor(56, 200, 215, 46))); p.setPen(QPen(QColor(255, 215, 100, 200), 1)); p.drawPolygon(poly)
        if self.probe_outline is not None and len(self.probe_outline) > 2:   # コンベックス・プローブ本体（下部モックと同じ実機風：白い筐体＋青いアレイ＋ボタン）
            poly = QPolygonF(self._poly(self.probe_outline, c, s, b))
            p.setBrush(QColor(201, 210, 221, 240)); p.setPen(QPen(QColor(120, 132, 150), 2)); p.drawPolygon(poly)   # 白い筐体
            if self.probe_array is not None and len(self.probe_array) > 2:    # 青いアレイ凸面（皮膚に当たる走査面）
                ap = QPolygonF(self._poly(self.probe_array, c, s, b))
                p.setBrush(QColor(80, 146, 196, 245)); p.setPen(QPen(QColor(42, 90, 138), 2)); p.drawPolygon(ap)
            if self.probe_button is not None:                                 # 操作ボタン
                bc = self._proj(self.probe_button, c, s, b)
                p.setBrush(QColor(120, 172, 212)); p.setPen(QPen(QColor(120, 132, 150), 1)); p.drawEllipse(bc, 3.0, 3.0)
        self._stroke(p, self.shaft, c, s, b, QColor(204, 217, 230), 5)        # 灰シャフト
        self._stroke(p, self.orange, c, s, b, QColor(245, 140, 50), 8)        # 偏向で曲がる先端
        self._stroke(p, self.arr, c, s, b, QColor(64, 184, 250), 5)           # アレイ
        self._stroke(p, self.needle, c, s, b, QColor(245, 140, 50), 2, dash=True)  # 針(計画=Entry→Target)
        # TIPS金属針システム: 外筒(頭側から)＋接続部＋針シャフト＋金属ベベル先端。動かしても常に連続。
        if self.cannula_rod is not None and len(self.cannula_rod) >= 2:       # 外筒＝太い金属棒
            self._stroke(p, self.cannula_rod, c, s, b, QColor(198, 206, 220), 10)  # 外周(金属)
            self._stroke(p, self.cannula_rod, c, s, b, QColor(120, 132, 150), 3)   # 芯の陰影で筒らしく
        if self.needle_body is not None and len(self.needle_body) >= 2:      # 接続部＋針シャフト＝太い灰色メタリック
            self._stroke(p, self.needle_body, c, s, b, QColor(176, 184, 198), 7)   # 金属シャフト（灰）
            self._stroke(p, self.needle_body, c, s, b, QColor(96, 104, 120), 2)    # 芯の陰影で丸みを出す
        if self.needle_tip is not None and len(self.needle_tip) >= 3:        # 先端＝明るいスチールのベベル(尖った切っ先)
            poly = QPolygonF(self._poly(self.needle_tip, c, s, b))
            p.setBrush(QColor(226, 232, 244)); p.setPen(QPen(QColor(150, 158, 172), 1.2)); p.drawPolygon(poly)
        if self.entry is not None and self.aim_tip is not None:   # 実際の針＝円柱風の二重線（画面座標系の太さは回転しても細く見えない）
            seg = [np.asarray(self.entry, float), np.asarray(self.aim_tip, float)]
            self._stroke(p, seg, c, s, b, QColor(255, 250, 235), 6)      # 外周(明るい実際の針色)
            self._stroke(p, seg, c, s, b, QColor(196, 190, 168), 2)      # 芯の陰影で丸みを出す
        elif self.aim_outline is not None and len(self.aim_outline) >= 3:   # 旧データ互換（entry/aim_tip 未供給時）
            poly = QPolygonF(self._poly(self.aim_outline, c, s, b))
            p.setBrush(QColor(255, 250, 235, 130)); p.setPen(QPen(QColor(255, 250, 235), 1.5)); p.drawPolygon(poly)
        self._flow(p, self.needle_pred, c, s, b, QColor(255, 255, 255, 235), 3.2)   # 進行方向(流れるダッシュ)
        if self.aim_pred is not None and len(self.aim_pred) >= 2:         # 実際の針の想定2cm予測も流す
            self._flow(p, self.aim_pred, c, s, b, QColor(230, 230, 230, 190), 2.0)
        for pt, col in ((self.entry, GREENC), (self.target, REDC), (self.apex, REDC)):
            if pt is not None:
                w = self._proj(pt, c, s, b); p.setBrush(col); p.setPen(QPen(Qt.white, 1))
                p.drawEllipse(w, 4, 4)
        h = QColor(160, 160, 160); p.setPen(h)
        p.drawText(8, self.height() - 8, L("metal=cannula & needle  white dashes=advance dir  cyan=ICE sector",
                                           "金属=外筒・針  白破線=進行方向  水色=ICE扇"))
        self._draw_orient_cube(p, b)                          # 解剖方位キューブ（A/P・R/L・S/I）
        _frame(p, self, self.active)

    def _try_pick(self, e, sticky=False):
        """経腹モード: プローブ/体表近傍を掴めたらプローブ設置/移動を発火。拾えたら True。
        sticky=True（移動中）は許容半径を広げ、半径外でも最近傍で追従し続ける。"""
        if not (self.show_body and self.body is not None and (e.buttons() & Qt.LeftButton)):
            return False
        b = self.rect(); c, s = self._surf_frame(b)
        idx = self._pick_body(e.position(), c, s, b, radius=(46.0 if sticky else 28.0), nearest=sticky)
        if idx is None:
            return False
        self.surfacePicked.emit(idx); return True

    def event(self, e):
        # ピンチ=カーソル中心ズーム。position()がペイン基準でない環境があるため globalPosition から逆算する
        if isinstance(e, QNativeGestureEvent) and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            gp = getattr(e, "globalPosition", None)
            pos = self.mapFromGlobal(gp()) if gp is not None else e.position()
            self._zoom_at(1.0 + float(e.value()), pos, self.rect())
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        self._last = e.position(); self._press = e.position(); self._moved = False
        self._moving_probe = self._try_pick(e)                # 押した瞬間にプローブ/体表近傍なら掴む

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        if (e.position() - getattr(self, "_press", e.position())).manhattanLength() > 2.5:
            self._moved = True
        if getattr(self, "_moving_probe", False):            # 掴んだら体表をなぞってプローブ移動（半径外でも追従・回転しない）
            self._try_pick(e, sticky=True); self._last = e.position(); return
        d = e.position() - self._last                        # 掴んでいない＝視点回転
        self.az += d.x() * 0.5; self.el = max(-89, min(89, self.el + d.y() * 0.5))
        self._last = e.position(); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and not getattr(self, "_moved", False):   # 右クリック=ズームリセット
            self.zoom3d = 1.0; self.pan3d = QPointF(0, 0)
        self._last = None; self._moving_probe = False
        self.update()                                        # 指を離した＝体表を高画質(超解像)で描き直す

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        self._zoom_at(float(np.exp(dy * 0.0016)), e.position(), self.rect())


# ============ 画像ペイン ============
class ImagePane(QWidget):
    wlChanged = Signal(float, float)
    clicked = Signal(float, float)          # image col,row
    wheelMoved = Signal(int)
    movePoint = Signal(str, float, float)   # id, col, row
    activated = Signal(object)              # マウスが入った＝アクティブ画面

    def __init__(self, caption=""):
        super().__init__()
        self.caption = caption
        self.active = False
        self.img = None; self.phys_w = 1.0; self.phys_h = 1.0
        self.overlay_fn = None; self.hit_points = []        # [(id, col, row)]
        self.placeholder = "Open a CT to begin"
        self.zoom = 1.0; self.pan = QPointF(0, 0); self.roll_deg = 0.0   # 無段階ロール（ICE用）
        self._press = None; self._moved = False; self._drag_id = None
        self._press_panmod = False                           # 押下時のパン意図をラッチ（離すレース対策）
        self._scroll_accum = 0.0; self._space = False        # トラックパッド: スクロール積算 / Space長押し=パン
        self.setMinimumSize(200, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)                  # Space長押しパンのためキー入力を受ける

    def set_image(self, img, phys_w, phys_h):
        self.img = img; self.phys_w = max(phys_w, 1e-3); self.phys_h = max(phys_h, 1e-3); self.update()

    def reset_view(self):
        self.zoom = 1.0; self.pan = QPointF(0, 0); self.update()

    def _fit(self):
        if self.img is None:
            return None
        rows, cols = self.img.shape
        aspect = self.phys_w / self.phys_h
        Wd, Hd = self.width(), self.height() - 18
        if Wd <= 0 or Hd <= 0:
            return None
        if Wd / Hd > aspect:
            th0 = Hd; tw0 = th0 * aspect
        else:
            tw0 = Wd; th0 = tw0 / aspect
        tw = tw0 * self.zoom; th = th0 * self.zoom
        cx = Wd / 2 + self.pan.x(); cy = 18 + Hd / 2 + self.pan.y()
        return cx - tw / 2, cy - th / 2, tw, th, cols, rows

    def to_image(self, wx, wy):
        f = self._fit()
        if not f:
            return None
        x, y, tw, th, cols, rows = f
        if self.roll_deg:                                    # 表示ロールを逆回し
            wx, wy = _roll_xy(wx, wy, x + tw / 2, y + th / 2, -self.roll_deg)
        return (wx - x) / tw * cols, (wy - y) / th * rows

    def to_widget(self, col, row):
        f = self._fit()
        if not f:
            return QPointF(0, 0)
        x, y, tw, th, cols, rows = f
        wx = x + col / cols * tw; wy = y + row / rows * th
        if self.roll_deg:                                    # オーバーレイも同じだけ回す
            wx, wy = _roll_xy(wx, wy, x + tw / 2, y + th / 2, self.roll_deg)
        return QPointF(wx, wy)

    def zoom_at(self, factor, pos):
        ic = self.to_image(pos.x(), pos.y())
        self.zoom = max(0.2, min(12.0, self.zoom * factor))
        if ic:
            w = self.to_widget(ic[0], ic[1]); self.pan += (pos - w)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.fillRect(self.rect(), QColor(7, 10, 14)); p.setPen(TERRA)
        p.drawText(8, 14, self.caption)
        f = self._fit()
        if not f:
            p.setPen(QColor(140, 140, 140)); p.drawText(10, self.height() // 2, self.placeholder)
            _frame(p, self, self.active); return
        x, y, tw, th, cols, rows = f
        qimg = QImage(self.img.tobytes(), cols, rows, cols, QImage.Format_Grayscale8)
        if self.roll_deg:                                    # 画像を中心まわりに無段階ロール
            p.save(); p.translate(x + tw / 2, y + th / 2); p.rotate(self.roll_deg)
            p.translate(-(x + tw / 2), -(y + th / 2)); p.drawImage(QRectF(x, y, tw, th), qimg); p.restore()
        else:
            p.drawImage(QRectF(x, y, tw, th), qimg)
        if self.overlay_fn:
            p.setRenderHint(QPainter.Antialiasing, True); self.overlay_fn(p, self.to_widget)
        _frame(p, self, self.active)                         # アクティブ画面=太いテラコッタ枠

    def enterEvent(self, e):
        self.activated.emit(self); super().enterEvent(e)

    def _near_point(self, wx, wy):
        for pid, col, row in self.hit_points:
            w = self.to_widget(col, row)
            if abs(w.x() - wx) < 9 and abs(w.y() - wy) < 9:
                return pid
        return None

    def _pan_mod(self, e):
        """Space長押し or ⌥Option(macのみ)＝パン操作（トラックパッドで中ボタンの代替）。
        Alt は Windows でメニューバーAltと競合するため macOS 限定にする。"""
        alt = bool(e.modifiers() & Qt.AltModifier) and sys.platform == "darwin"
        return self._space or alt

    def mousePressEvent(self, e):
        self._press = e.position(); self._moved = False
        self._press_panmod = self._pan_mod(e)                # パン意図は押下時に確定（atomic）
        self._drag_id = (self._near_point(e.position().x(), e.position().y())
                         if e.button() == Qt.LeftButton and not self._press_panmod else None)

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        d = e.position() - self._press
        if abs(d.x()) + abs(d.y()) > 2.5:
            self._moved = True
        if (e.buttons() & Qt.LeftButton) and self._press_panmod:  # Space/Option+ドラッグ=パン（押下時の意図で固定）
            self.pan += d; self._press = e.position()
            self.setCursor(Qt.ClosedHandCursor); self.update()
        elif self._drag_id is not None and (e.buttons() & Qt.LeftButton):
            ic = self.to_image(e.position().x(), e.position().y())
            if ic:
                self.movePoint.emit(self._drag_id, ic[0], ic[1])
        elif e.buttons() & Qt.LeftButton:                       # 左ドラッグ=W/L
            self.wlChanged.emit(d.x() * 3.0, -d.y() * 3.0); self._press = e.position()
        elif e.buttons() & Qt.RightButton:                      # 右ドラッグ=拡大縮小（カーソル中心）
            self.zoom_at(float(np.exp(-d.y() * 0.01)), e.position()); self._press = e.position()
        elif e.buttons() & Qt.MiddleButton:                     # 中ドラッグ=パン（マウス）
            self.pan += d; self._press = e.position(); self.update()

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._moved:
            if e.button() == Qt.LeftButton and self._drag_id is None and not self._press_panmod:
                ic = self.to_image(e.position().x(), e.position().y())
                if ic:
                    _log_click(self, e, ic)                     # 位置ズレ調査用の診断ログ（軽量・失敗無害）
                    self.clicked.emit(ic[0], ic[1])
            elif e.button() == Qt.RightButton:                  # 右クリック=ズーム/パンをリセット
                self.reset_view()
        self._press = None; self._drag_id = None; self._press_panmod = False
        self.setCursor(Qt.OpenHandCursor if self._space else Qt.ArrowCursor)

    # ---- トラックパッド: ピンチ=ズーム / Cmd・Ctrl+スクロール=ズーム / スクロール=スライス（積算）----
    def event(self, e):
        if isinstance(e, QNativeGestureEvent) and self._native_gesture(e):
            return True
        return super().event(e)

    def _native_gesture(self, e):
        gt = e.gestureType()
        # ピンチの position() はペイン基準にならない環境がある（Axialだけ正しく見え、他ペインで中心がズレる）
        # → OS由来の globalPosition からこのペイン座標へ逆算して常に正しいカーソル中心にする
        gp = getattr(e, "globalPosition", None)
        pos = self.mapFromGlobal(gp()) if gp is not None else e.position()
        if gt == Qt.NativeGestureType.ZoomNativeGesture:         # ピンチ=ズーム（カーソル中心）
            self.zoom_at(1.0 + float(e.value()), pos); return True
        if gt == Qt.NativeGestureType.SmartZoomNativeGesture:    # 2本指ダブルタップ=フィット/2倍トグル
            if self.zoom > 1.05:
                self.reset_view()
            else:
                self.zoom_at(2.0, pos)
            return True
        return False

    def _wheel_zoom(self, dy, pos):
        if dy:
            self.zoom_at(float(np.exp(dy * 0.0016)), pos)       # カーソル中心ズーム

    def _accum_step(self, dy, unit):
        if (dy < 0) != (self._scroll_accum < 0):                # 方向反転でリセット（デッドゾーン解消）
            self._scroll_accum = 0.0
        self._scroll_accum += dy
        step = int(self._scroll_accum / unit)
        if step:
            self._scroll_accum -= step * unit
            self.wheelMoved.emit(step)

    def _wheel_slice(self, pixel_dy, angle_dy):
        if pixel_dy:                                            # トラックパッド: pixelDeltaを28px/段で積算
            self._accum_step(pixel_dy, 28.0)
        elif angle_dy:                                          # ホイール/精密TP: angleDeltaを120/段で積算（Winの飛びすぎ対策）
            self._accum_step(angle_dy, 120.0)

    def wheelEvent(self, e):
        pd = e.pixelDelta(); ad = e.angleDelta()
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):   # Cmd/Ctrl(+Winのピンチ)+スクロール=ズーム
            self._wheel_zoom(pd.y() if not pd.isNull() else ad.y(), e.position())
        else:
            self._wheel_slice(pd.y() if not pd.isNull() else 0, ad.y())
        e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True; self.setCursor(Qt.OpenHandCursor)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False; self.setCursor(Qt.ArrowCursor)
        else:
            super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        # フォーカスが外れると Space の keyRelease が届かず固着する（ボタン押下/ダイアログ/Cmd-Tab）→ 防御的に解除
        self._space = False; self.setCursor(Qt.ArrowCursor); super().focusOutEvent(e)


class PaneCell(QWidget):
    """ペイン＋右の縦スクロールバー（薄切りナビ）。"""
    def __init__(self, pane, on_scroll):
        super().__init__()
        self.pane = pane
        self.bar = QScrollBar(Qt.Vertical); self.bar.setMinimum(0); self.bar.setMaximum(0)
        self.bar.valueChanged.connect(on_scroll)
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(2)
        h.addWidget(pane, 1); h.addWidget(self.bar)

    def set_range(self, n, val):
        self.bar.blockSignals(True); self.bar.setMaximum(max(0, n - 1)); self.bar.setValue(int(val)); self.bar.blockSignals(False)


class _CrossHandle(QWidget):
    """4画面の中央（縦の仕切りと横の仕切りが交わる点）に置く掴み手。
    これを引くと縦と横の仕切りが同時に動き、4画面の大きさが一度に変わる。"""
    def __init__(self, quad):
        super().__init__(quad)
        self.quad = quad
        self.setFixedSize(24, 24)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(L("Drag to resize all four panes at once",
                          "ドラッグすると4画面の大きさを同時に変えられます"))
        self._drag = False

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(30, 52, 78, 235)); p.setPen(QPen(QColor(120, 170, 220), 1.2))
        p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        p.setPen(QPen(QColor(214, 232, 248), 1.6))
        c = self.rect().center()
        p.drawLine(c.x() - 6, c.y(), c.x() + 6, c.y())        # ＋の印＝縦横どちらにも動く
        p.drawLine(c.x(), c.y() - 6, c.x(), c.y() + 6)

    def mousePressEvent(self, _e):
        self._drag = True

    def mouseReleaseEvent(self, _e):
        self._drag = False

    def mouseMoveEvent(self, e):
        if self._drag:
            self.quad.set_cross(self.quad.mapFromGlobal(e.globalPosition().toPoint()))

    def mouseDoubleClickEvent(self, _e):
        self.quad.reset()                                     # ダブルクリックで4等分に戻す


class QuadPanes(QWidget):
    """4画面を自由な比率に変えられる格子。

    QGridLayout は比率を固定するので仕切りを掴めなかった。上下2段の QSplitter を、さらに
    縦の QSplitter に入れて作る。ただし素直に入れ子にすると上段と下段の縦仕切りが別々に動き、
    真ん中の線が食い違って見える → 片方が動いたらもう片方の幅を合わせ、常に1本の通った線にする。
    交差点には掴み手（_CrossHandle）を重ねて置き、縦横を同時に動かせるようにする。
    """
    def __init__(self, tl, tr, bl, br):
        super().__init__()
        self.top = QSplitter(Qt.Horizontal); self.top.addWidget(tl); self.top.addWidget(tr)
        self.bot = QSplitter(Qt.Horizontal); self.bot.addWidget(bl); self.bot.addWidget(br)
        self.vs = QSplitter(Qt.Vertical); self.vs.addWidget(self.top); self.vs.addWidget(self.bot)
        for s in (self.top, self.bot, self.vs):
            s.setChildrenCollapsible(False)                   # 画面をゼロまで潰せないようにする
            s.setHandleWidth(6)
            s.setOpaqueResize(True)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.vs)
        self.top.splitterMoved.connect(self._sync_from_top)
        self.bot.splitterMoved.connect(self._sync_from_bot)
        self.vs.splitterMoved.connect(lambda *_: self._place())
        self.cross = _CrossHandle(self)

    # setSizes() は splitterMoved を出さないので、下の2つが呼び合って無限ループにはならない
    def _sync_from_top(self, *_):
        self.bot.setSizes(self.top.sizes()); self._place()

    def _sync_from_bot(self, *_):
        self.top.setSizes(self.bot.sizes()); self._place()

    def resizeEvent(self, e):
        super().resizeEvent(e); self._place()

    def showEvent(self, e):
        super().showEvent(e); self._place()

    def _place(self):
        """掴み手を実際の交差点へ置き直す。Qt が最小サイズで丸めた後の値を読むので、
        引っぱりすぎて画面が潰れそうな時も掴み手は必ず仕切りの上に乗る。"""
        st = self.top.sizes(); sv = self.vs.sizes()
        if len(st) < 2 or len(sv) < 2:
            return
        x = st[0] + self.top.handleWidth() / 2.0
        y = sv[0] + self.vs.handleWidth() / 2.0
        self.cross.move(int(round(x - self.cross.width() / 2.0)),
                        int(round(y - self.cross.height() / 2.0)))
        self.cross.raise_()

    def set_cross(self, pos):
        W, H = self.width(), self.height()
        hw, vh = self.top.handleWidth(), self.vs.handleWidth()
        x = int(min(max(pos.x(), 0), W)); y = int(min(max(pos.y(), 0), H))
        cols = [max(0, x - hw // 2), max(0, W - x - hw // 2)]
        self.top.setSizes(cols); self.bot.setSizes(cols)
        self.vs.setSizes([max(0, y - vh // 2), max(0, H - y - vh // 2)])
        self._place()

    def reset(self):
        W, H = self.width(), self.height()
        self.top.setSizes([W // 2, W // 2]); self.bot.setSizes([W // 2, W // 2])
        self.vs.setSizes([H // 2, H // 2]); self._place()

    def sizes(self):
        return dict(cols=self.top.sizes(), rows=self.vs.sizes())

    def set_sizes(self, d):
        if not d:
            return
        cols, rows = d.get("cols"), d.get("rows")
        if cols and len(cols) == 2 and min(cols) >= 0 and sum(cols) > 0:
            self.top.setSizes(list(cols)); self.bot.setSizes(list(cols))
        if rows and len(rows) == 2 and min(rows) >= 0 and sum(rows) > 0:
            self.vs.setSizes(list(rows))
        self._place()


def _sep():
    f = QFrame(); f.setFrameShape(QFrame.VLine); f.setStyleSheet("color:#33506e"); return f


class MainWindow(QMainWindow):
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
        self.lock3 = False; self._lock3 = None      # 3点固定モード（θを自動で解く）と、その残差(mm)
        self.path = []; self.flip = False
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
        self.current_study_uid = None; self._current_files = []   # 今開いている患者(検査)＝作業保存の紐付け先
        self._pending_restore = None                              # ロード完了後に流し込む保存済み作業状態

        self.ax = ImagePane("Axial  (click = IVC path / Entry-Target)")
        self.cor = ImagePane("Coronal"); self.sag = ImagePane("Sagittal")
        self.ice = ImagePane("ICE view  (wheel = Rotate θ)")
        self.ice.placeholder = "Draw the IVC path on Axial (>= 2 clicks)"
        self.p3d = Pane3D()
        for pane, pl in ((self.ax, 0), (self.cor, 1), (self.sag, 2)):
            pane.overlay_fn = lambda p, tw, _pl=pl: self._overlay(p, tw, _pl)
            pane.movePoint.connect(lambda pid, c, r, _pl=pl: self._move_point(pid, c, r, _pl))
        self.ice.overlay_fn = self._ice_overlay
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
        self.db.langToggled.connect(self._toggle_lang)
        self.p3d.surfacePicked.connect(self._pick_surface_3d)   # 3D体表ドラッグ→プローブ設置
        self.stack = QStackedWidget(); self.stack.addWidget(self.db); self.stack.addWidget(self.viewer_page)
        self.setCentralWidget(self.stack); self.stack.setCurrentWidget(self.db)
        self.setStyleSheet(STYLE)
        self._build_menu()
        self._update_step_ui()
        self._apply_language()                               # 保存済み言語でUI文字列を確定（EN/JA）
        QApplication.instance().aboutToQuit.connect(self._stop_workers)   # Cmd+Q は closeEvent を通らない

    # ---------- 下部コントロール ----------
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

    def _controls(self):
        """Step2(穿刺針)専用の操作。患者リスト/DICOMを開く/言語/挿入方向は上部メニューバーに集約済み。
        Step/Echo/保存/クリアは _bottom_strip() 側（画像直下の複合帯）に置く。
        戻り値のneedleRowWは _footer() の左側（注記の左・同じ高さ）に配置され、専用の行を持たない
        （Step1では畳んで0幅＝CT/ICE画像を圧迫しない）。"""
        rN = QHBoxLayout(); rN.setContentsMargins(0, 0, 0, 0)
        self.entryBtn = self._btn("1·Entry (puncture)", lambda: self._set_ptmode(0), ja="1·Entry（刺入点）")
        self.targetBtn = self._btn("2·Target (portal)", lambda: self._set_ptmode(1), ja="2·Target（門脈側）")
        self.aimBtn = self._btn("Actual tip (click ICE)", self._toggle_aim, checkable=True, ja="実際の針先（ICEをクリック）")
        self.torqueGroup, self.torqueLBtn, self.torqueRBtn, self.hubWidget = self._torque_group()
        self.lblSet = self._lbl("Set:", "設定:")
        self.needleTypeBtn = self._btn("Needle: Colapinto", self._toggle_needletype)
        self.lblAdv = self._acc("Advance"); self.sAdvance = self._slider(20, 160, 90, self._set_advance, 100)
        self.advVal = QLabel("90 mm")
        self.lblCurve = self._acc("Colapinto curve"); self.sCurve = self._slider(25, 90, int(self.colaR), self._set_colaR, 100)
        self.curveVal = QLabel(f"R {int(self.colaR)} mm")
        # Plot(予習)モード：実際の針先をプロット→前方予測（RUPS直進/Colapinto頭側弧）
        self.plotBtn = self._btn("Plot tip", self._toggle_plot)
        self.predNeedleBtn = self._btn("Pred: RUPS", self._toggle_predneedle)
        for w in (self.lblSet, self.entryBtn, self.targetBtn, self.aimBtn, self.torqueGroup,
                  self.needleTypeBtn,
                  self.lblAdv, self.sAdvance, self.advVal, self.lblCurve, self.sCurve, self.curveVal,
                  self.plotBtn, self.predNeedleBtn):
            rN.addWidget(w)
        rN.addStretch(1)
        self.needleRowW = QWidget(); self.needleRowW.setLayout(rN)   # Step1では丸ごと畳んで空白を作らない
        return self.needleRowW

    def _bottom_strip(self):
        """画像直下の複合帯＝凡例(縦)/手順/エコー/Handle操作/ロール・肝臓/保存/クリアを1本に集約。
        患者リスト・DICOMを開く・言語・挿入方向は上部メニューバーへ移設済み（施設固定・毎回操作しないため）。
        従来の複数行(Row1+ハンドル帯+ロール行)を1本の帯に統合し、縦方向を節約してCT/ICE表示を最大化。
        操作方式はHandle（絵をドラッグ）に一本化済み（旧Classic=スライダー表示は廃止・先生指示2026-07-14）。
        ただしθ/偏向/プローブ位置のスライダー(sTheta/sB1/sB2/sProbe)自体は、Handle操作の値が実際に
        流れ込む先＝状態の一次保持先として内部的に温存する（表示はしない・_restore_state等が直接参照するため）。"""
        strip = QWidget(); strip.setStyleSheet("background:#14253a;")
        h = QHBoxLayout(strip); h.setContentsMargins(8, 2, 8, 2); h.setSpacing(8)
        self.gbar = GestureBar(vertical=True)
        h.addWidget(self.gbar); h.addWidget(_sep())
        h.addWidget(self._step_group()); h.addWidget(self._echo_group()); h.addWidget(_sep())
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
        self._vestigial = QWidget(self); self._vestigial.hide()
        for _w in (self.sTheta, self.sProbe, self.sB1, self.sB2, self.b1Val, self.b2Val,
                   self.lblTheta, self.lblProbe, self.probeFoot, self.probeHead,
                   self.lblAP, self.lblLR):
            _w.setParent(self._vestigial)
        # ICEのモック（AcuNavハンドル）の**すぐ下**に3点固定モードのスイッチを置く（先生指定 2026-07-15）
        self.lock3Btn = self._btn("◎ 3-point lock: OFF", self._toggle_lock3, checkable=True,
                                  ja="◎ 3点固定: OFF")
        self.lock3Btn.setToolTip(L("Keep Entry and Target on the ICE image plane by solving θ automatically. "
                                   "Push/pull and deflection stay in your hands.",
                                   "θを自動で解いて Entry と Target を ICE画像面に乗せ続けます。"
                                   "押し引きと偏向は先生が動かします。"))
        self.handleBox = QWidget()
        hbv = QVBoxLayout(self.handleBox); hbv.setContentsMargins(0, 0, 0, 0); hbv.setSpacing(3)
        hbv.addWidget(self.handleCtl, 1); hbv.addWidget(self.lock3Btn, 0, Qt.AlignHCenter)
        # ICE用と経腹用を QStackedWidget で重ねる。並べて片方を隠す作りだと、3点固定スイッチのぶん
        # ICE側だけ背が高くなり、**モードを切り替えるたびに操作帯の高さが変わって4画面が飛び跳ねる**
        # （実際そうなった）。スタックは常に一番高いページぶんの高さを確保するので、切り替えても動かない。
        self.ctlStack = QStackedWidget()
        self.ctlStack.addWidget(self.handleBox)              # index 0 = 血管内ICE
        self.ctlStack.addWidget(self.surfCtl)                # index 1 = 経腹
        h.addWidget(self.ctlStack, 2)
        h.addWidget(self._btn("Zero deflect", self._zero_defl, ja="偏向ゼロ"))
        h.addWidget(_sep())
        # --- 右ブロック: ロール／反転＋肝臓ゴースト（2行・常時表示） ---
        rl = QWidget(); g = QVBoxLayout(rl); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(2); g.addStretch(1)
        rw1 = QHBoxLayout(); rw1.setSpacing(6)
        rw1.addWidget(self._acc("Roll", ja="ロール")); self.sRoll = self._slider(-180, 180, 0, self._set_roll, 100)
        rw1.addWidget(self.sRoll); self.rollVal = QLabel("0°"); rw1.addWidget(self.rollVal)
        rw1.addWidget(self._btn("Roll 0", self._reset_roll, ja="ロール0"))
        rw1.addWidget(self._btn("Flip ICE L/R", self._toggle_flip, ja="ICE左右反転")); rw1.addStretch(1)
        g.addLayout(rw1)
        rw2 = QHBoxLayout(); rw2.setSpacing(6)
        self.liverBtn = self._btn("Liver", self._toggle_liver, checkable=True, ja="肝臓"); self.liverBtn.setChecked(True)
        self.liverModeBtn = self._btn("Liver: Haze", self._toggle_liver_mode)
        self.sLiverOp = self._slider(10, 90, int(self.liver_opacity * 100), self._set_liver_opacity, 80)
        rw2.addWidget(self.liverBtn); rw2.addWidget(self.liverModeBtn)
        rw2.addWidget(self._acc("Opacity", ja="不透明度")); rw2.addWidget(self.sLiverOp); rw2.addStretch(1)
        g.addLayout(rw2); g.addStretch(1)
        h.addWidget(rl); h.addWidget(_sep())
        h.addWidget(self._undo_group()); h.addWidget(self._save_group()); h.addWidget(self._clear_group())
        return strip

    def _step_group(self):
        """手順(Step1/2)を縦に並べたグループ（凡例の右）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._acc("Step", ja="手順"); lbl.setAlignment(Qt.AlignCenter); v.addWidget(lbl)
        self.step1Btn = self._btn("1. ICE setup", lambda: self._set_step(0), ja="1. ICEセットアップ")
        self.step2Btn = self._btn("2. Needle", lambda: self._set_step(1), ja="2. 穿刺針")
        for b in (self.step1Btn, self.step2Btn):
            b.setMaximumHeight(20); v.addWidget(b)
        return box

    def _echo_group(self):
        """エコーモード(血管内ICE/経腹)を縦に並べたグループ（手順の右）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._acc("Echo", ja="エコー"); lbl.setAlignment(Qt.AlignCenter); v.addWidget(lbl)
        self.iceBtn = self._btn("Intravascular ICE", lambda: self._set_viewmode("ice"), ja="血管内ICE")
        self.surfBtn = self._btn("Transabdominal (surface)", lambda: self._set_viewmode("surface"), ja="経腹（体表）")
        for b in (self.iceBtn, self.surfBtn):
            b.setMaximumHeight(20); v.addWidget(b)
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
        """『実際の針』の回旋操作＝タイトル＋(左回転ボタン・手元ハブの絵・右回転ボタン)。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(4, 2, 4, 2); v.setSpacing(2)
        title = QLabel(); title.setStyleSheet("color:#F08F69;font-weight:bold;")
        title.setAlignment(Qt.AlignCenter)
        self._reg(title, "Stiffening cannula — Rotate", "スタイフニングカニューラ — 回旋")
        v.addWidget(title)
        row = QHBoxLayout(); row.setSpacing(6)
        lbtn = self._btn("↺ Left", lambda: self._nudge_torque(-15), ja="↺ 左")
        hub = CannulaHubWidget()
        rbtn = self._btn("Right ↻", lambda: self._nudge_torque(15), ja="右 ↻")
        row.addWidget(lbtn); row.addWidget(hub); row.addWidget(rbtn)
        v.addLayout(row)
        return box, lbtn, rbtn, hub

    def _clear_group(self):
        """消去4種を縦に並べたグループ（Open DICOMの左）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._acc("Clear", ja="クリア"); lbl.setAlignment(Qt.AlignCenter); v.addWidget(lbl)
        self.btnClearPath = self._btn("IVC path", self._clear_path, ja="IVCパス")
        self.btnClearNeedle = self._btn("Needle", self._clear_needle, ja="針")
        self.btnClearPlots = self._btn("Plots", self._clear_plots, ja="プロット")
        self.btnClearAll = self._btn("All", self._clear_all, ja="すべて")
        for b in (self.btnClearPath, self.btnClearNeedle, self.btnClearPlots, self.btnClearAll):
            b.setMaximumHeight(20); v.addWidget(b)
        return box

    def _undo_group(self):
        """直前の1手だけ戻すボタン（Saveの左）。クリック/点の設定やClear系・偏向ゼロ・ロール0・
        挿入方向の変更を対象とする（スライダー/Handleのドラッグ操作は対象外）。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._acc("Undo", ja="元に戻す"); lbl.setAlignment(Qt.AlignCenter); v.addWidget(lbl)
        tip = L("Undo the last click/point-set or Clear/Zero/Roll-0/insertion change (one step back). "
                "Slider and Handle drags are not covered.",
                "直前のクリック・点の設定や、クリア／偏向ゼロ／ロール0／挿入方向の変更を1つだけ元に戻します"
                "（スライダー／Handleのドラッグ操作は対象外）。")
        self.undoBtn = self._btn("↺ Undo", self._undo, ja="↺ 元に戻す")
        self.undoBtn.setEnabled(False); self.undoBtn.setStyleSheet(SS_OFF); self.undoBtn.setMaximumHeight(20)
        self.undoBtn.setToolTip(tip); box.setToolTip(tip)
        v.addWidget(self.undoBtn)
        return box

    def _save_group(self):
        """作業状態の保存スロット1/2/3を縦に並べたグループ（Clearの左）。患者ごとに独立して保存される
        （同じ「1」でも患者が違えば別物）。右クリックでそのスロットを削除できる。"""
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(1)
        lbl = self._acc("Save state", ja="状態保存"); lbl.setAlignment(Qt.AlignCenter); v.addWidget(lbl)
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
        return box

    def _current_patient_label(self):
        """今開いている患者の表示名（保存/復元ボタンのツールチップに使う）。未取得ならNone。"""
        if not self.current_study_uid:
            return None
        for st in self.catalog.studies():
            if st["study_uid"] == self.current_study_uid:
                return st.get("patient_name") or st.get("patient_id") or None
        return None

    def _clear_all(self):
        self._snap_undo()
        self.path = []; self.sProbe.setEnabled(False)
        self.entry = self.target = None; self.ptMode = 0; self.obs = []
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0
        self.contact = None; self.normal = None
        self.liver = None; self._liver_key = None; self.p3d.liver = None
        self._update_step_ui(); self._refresh()

    def _refresh_toggles(self):
        for b, on in ((self.step1Btn, self.step == 0), (self.step2Btn, self.step == 1),
                      (self.entryBtn, self.ptMode == 0), (self.targetBtn, self.ptMode == 1),
                      (self.aimBtn, self.aimMode),
                      (self.iceBtn, self.viewMode == "ice"), (self.surfBtn, self.viewMode == "surface"),
                      (self.plotBtn, self.predict)):
            b.setStyleSheet(SS_ON if on else SS_OFF)
        if hasattr(self, "actInsFem"):                       # 挿入方向は設定メニューのチェック状態で表示
            self.actInsFem.setChecked(self.tipHighZ); self.actInsJug.setChecked(not self.tipHighZ)

    def _footer(self):
        """最下段＝左:Step2の針操作（Step1では畳む）／右:研究・教育用の注記（常に右下・固定）。
        1本の帯にまとめることで、注記専用の行を持たせず画像表示を圧迫しない。"""
        bar = QWidget(); bar.setStyleSheet("background:#14253a;")
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

    def _activate(self, pane):
        """マウスが入った画面をアクティブ（太枠）にし、他を非アクティブにする。"""
        for pn in self._panes:
            on = (pn is pane)
            if pn.active != on:
                pn.active = on; pn.update()

    # ---------- 上部メニュー（FAQ / About） ----------
    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("File"); self._reg(fm.menuAction(), "File", "ファイル")
        self._reg(fm.addAction("", self._go_database), "Patient list", "患者リスト")
        self._reg(fm.addAction("", self._open_dicom), "Open DICOM…", "DICOMを開く…")
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
        em.addSeparator()
        self._reg(em.addAction("", self._clear_all), "Clear all", "すべて消去")
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
        self._reg(sm.addAction("", self._toggle_lang), "Switch language (EN / 日本語)", "表示言語を切替 (EN / 日本語)")
        m = mb.addMenu("Help"); self._reg(m.menuAction(), "Help", "ヘルプ")
        self._reg(m.addAction("", self._open_manual), "User manual (PDF)…", "使い方説明書（PDF）を開く…")
        self._reg(m.addAction("", lambda: self._show_tip_dialog(startup=False)), "Tip of the day", "今日のヒント")
        m.addSeparator()
        self._reg(m.addAction("", self._show_faq), "FAQ / How to use", "FAQ / 使い方")
        self._reg(m.addAction("", self._show_about), "About / Author", "このアプリについて / 作者")
        self._reg(m.addAction("", self._check_updates), "Check for updates…", "アップデートを確認…")

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
        self.db.retranslate()
        self.gbar.update()
        for pn in self._panes:
            pn.update()

    def _update_liver_btn(self):
        self.liverModeBtn.setText(L("Liver: ", "肝臓: ")
                                  + (L("Surface", "表面") if self.liver_mode == "surface" else L("Haze", "もや")))

    def _check_updates(self, silent=False):
        """新版を探し、見つかればワンクリックで入れ替え・再起動する。
        silent=True（起動時の自動チェック）はローカル配布フォルダのみ・最新時は無言。
        silent=False（メニュー）はインターネット(UPDATE_URL)も見る・最新時も通知。"""
        import updater
        try:
            info = updater.find_update(VERSION, None if silent else UPDATE_URL)
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
        <p style="color:#888">作者については <b>ヘルプ &rarr; このアプリについて</b> を参照してください。</p>
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

    # ---------- 起動時の「今日のヒント」（VS Code風・チェックボックスでオフ可）----------
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

    def _maybe_show_tip_at_startup(self):
        if bool(settings_store.store().value("show_tips_on_startup", True, type=bool)):
            self._show_tip_dialog(startup=True)

    def _update_step_ui(self):
        s1 = (self.step == 0)
        if hasattr(self, "needleRowW"):
            self.needleRowW.setVisible(not s1)               # Step1(ICE)では針操作行を畳む＝下の空白を無くし画像を大きく
        for w in (self.lblSet, self.entryBtn, self.targetBtn, self.aimBtn,
                  self.torqueGroup):   # Step2=Entry/Target＋実際の針先＋回旋
            w.setVisible(not s1)
        # 曲げ系(RUPS/Colapinto・前進量・曲率)とPlot予習は一旦封印（直線穿刺へ巻き戻し・再実装まで非表示）
        for w in (self.needleTypeBtn, self.lblAdv, self.sAdvance, self.advVal,
                  self.lblCurve, self.sCurve, self.curveVal, self.plotBtn, self.predNeedleBtn):
            w.setVisible(False)
        self.predict = False                                  # Plot予習(ピンク点線)もオフに固定
        self._refresh_toggles()

    # ---------- データ ----------
    def _open_series_files(self, files, study_uid=""):
        self.current_study_uid = study_uid or None; self._current_files = list(files)
        import bg
        bg.run_with_progress(self, L(f"Loading series… ({len(files)} images)", f"シリーズを読み込み中…（{len(files)}枚）"),
            lambda prog: dicom_io.load_series_files(files, progress=prog),
            self._on_series_loaded,
            on_fail=lambda m: self.statusBar().showMessage(L("Load error: ", "読み込みエラー: ") + m.splitlines()[0], 8000))

    def _on_series_loaded(self, vol):
        self._set_volume(vol); self.stack.setCurrentWidget(self.viewer_page)
        st = self._pending_restore; self._pending_restore = None
        if st is not None:
            self._restore_state(st)

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

    def open_external_path(self, path):
        """外部アプリから渡されたフォルダ/ファイルを TIPS ICE Planner に
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
            on_fail=lambda m: self.statusBar().showMessage(L("Import error: ", "取り込みエラー: ") + m.splitlines()[0], 8000))

    def _import_external(self, src, progress=None):
        """src 内のDICOMをアプリ専用領域へコピー(=永久保存)し、カタログへ相ごとに取り込む。
        返り値 (追加シリーズ数, study_uid|None)。バックグラウンドWorkerから呼ばれる。"""
        import os, glob, shutil, uuid
        store = os.path.join(self.catalog.dir, "studies"); os.makedirs(store, exist_ok=True)
        dst = os.path.join(store, "import_" + uuid.uuid4().hex[:12]); os.makedirs(dst, exist_ok=True)
        files = [p for p in glob.glob(os.path.join(src, "**", "*"), recursive=True)
                 if os.path.isfile(p) and dicom_io.is_dicom(p)]
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
        return (added, study_uid)

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

    def _set_volume(self, vol):
        self.vol = vol; nz, H, W = vol.shape
        self.p3d.orient = vol.meta.get("orient")             # 方位キューブ（無ければ標準axial仮定）
        self.cz, self.cy, self.cx = nz // 2, H // 2, W // 2
        self.path = []; self.entry = None; self.target = None; self.obs = []; self.sProbe.setEnabled(False)
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0    # 実際の針も患者ごとにリセット
        self.contact = None; self.normal = None              # 経腹プローブ接触点も患者ごとにリセット
        self.liver = None; self._liver_key = None; self.p3d.liver = None
        self.body = None; self._body_key = None; self.p3d.body = None
        self.step = 0; self.ptMode = 0                       # 新規患者は必ず「1. ICEセットアップ」から
        note = vol.meta.get("note", "")
        self.statusBar().showMessage(
            f"Loaded {nz}×{H}×{W}  spacing {vol.sx:.2f}/{vol.sy:.2f}/{vol.dz:.2f} mm" + (f"   ⚠ {note}" if note else ""), 12000)
        self._update_step_ui()
        self._refresh()
        self._compute_body()                                 # 読込時に体表シェルを背景抽出
        self._update_save_buttons()                          # 患者が変わったので保存スロットの表示も更新

    # ---------- 作業状態の保存・復元（患者ごとにスロット1/2/3）----------
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
            quad=self.quad.sizes(), main=self.mainSplit.sizes())   # 4画面の枠そのものの大きさ

    def _restore_view(self, v):
        """_capture_view の内容を戻す。古い保存データには 'view' が無いので、その場合は何もしない
        （＝従来どおり初期表示）。壊れた値で画面が飛ばないよう、範囲は必ずクランプする。"""
        if not v or self.vol is None:
            return
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
        self.quad.set_sizes(v.get("quad"))                    # 4画面の枠の大きさ（仕切りの位置）
        m = v.get("main")
        if m and len(m) == 2 and min(m) >= 0 and sum(m) > 0:
            self.mainSplit.setSizes(list(m))

    def _restore_state(self, st):
        """_capture_state の辞書から作業状態を復元し、画面を更新する。"""
        def _a(v):
            return None if v is None else np.array(v, float)
        self.path = [list(p) for p in st.get("path", [])]; self.zP = st.get("zP", 0.0)
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
        self.aimBtn.setChecked(self.aim_tip is not None); self.hubWidget.set_torque(self.aim_torque)
        self.liver = None; self._liver_key = None; self.p3d.liver = None      # 復元後に幾何から再計算させる
        self.body = None; self._body_key = None; self.p3d.body = None
        self._restore_view(st.get("view"))                    # CT の拡大率・表示位置・スライス・窓値・3D視点
        self._update_step_ui(); self._update_mode_ui(); self._refresh_toggles()
        self._refresh(); self._compute_body()
        self.statusBar().showMessage(L("Restored saved state.", "保存された作業状態を復元しました。"), 6000)

    # ---------- Undo（直前の1手だけ戻す・多段履歴ではない） ----------
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

    def _save_slot(self, n, notify=True):
        """状態保存スロットn(1/2/3)へ保存。notify=True（既定）なら保存後に「スロットN」を明示するポップアップを出す
        （先生要望：ステータスバーの一瞬のメッセージだと見落とすため、必ず気づく形にする）。
        テスト等で内部的に呼ぶ場合は notify=False でポップアップ(モーダル)を出さない。"""
        if self.vol is None or not self.current_study_uid:
            self.statusBar().showMessage(L("Open a patient from the list first.", "先に患者リストから患者を開いてください。"), 6000)
            return False
        patient = self._current_patient_label() or L("this patient", "この患者")
        if self.catalog.has_session(self.current_study_uid, n):
            if QMessageBox.question(self, L("Overwrite?", "上書きの確認"),
                    L(f"Slot {n} already has a saved state for {patient}. Overwrite it?",
                      f"このスロット{n}には、{patient}の保存済み状態が既にあります。上書きしますか？"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
        self.catalog.set_session(self.current_study_uid, n, self._capture_state())
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
            self.statusBar().showMessage(L(f"Slot {n} deleted.", f"スロット{n}を削除しました。"), 5000)

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

    def _axial_click(self, col, row):
        if self.vol is None:
            return
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

    def _probe_to(self, v):
        if len(self.path) >= 2:
            zs = [p[0] for p in self.path]; self.zP = min(zs) + (max(zs) - min(zs)) * v / 100.0
            self.cz = int(np.clip(round(self.zP), 0, self.vol.shape[0] - 1)); self._refresh()

    def _set_probe(self, v): self._probe_to(v)
    # θ を手で回す入口。3点固定モード中は θ が「解かれる量」なので、どの入口からも受け付けない
    # （受け付けて直後に上書きすると、ホイールが効かない・壊れている、という見え方になる）。
    def _spin_theta(self, d):
        if self.lock3:
            return
        self.theta = (self.theta + d * 5) % 360; self.sTheta.setValue(int(self.theta)); self._refresh()

    def _set_theta(self, v):
        if self.lock3:
            return
        self.theta = float(v); self._refresh()

    def _toggle_lock3(self):
        """3点固定モード。ONの間、θ は自動で解かれ、Entry と Target が ICE画像面に乗り続ける。
        押し引きと偏向は先生の手に残る（先生決裁 2026-07-15）。"""
        self.lock3 = self.lock3Btn.isChecked()
        self.lock3Btn.setText(L("◎ 3-point lock: ON", "◎ 3点固定: ON") if self.lock3
                              else L("◎ 3-point lock: OFF", "◎ 3点固定: OFF"))
        if not self.lock3:
            self._lock3 = None
        self._refresh()
        if self.lock3 and self._lock3 is None:               # ONにしたのに解けなかった＝条件が足りない
            self.statusBar().showMessage(
                L("3-point lock needs an IVC path, an Entry and a Target in intravascular ICE mode.",
                  "3点固定には、血管内ICEモードで IVCパス・Entry・Target が必要です。"), 6000)

    def _apply_lock3(self):
        """θ を解いて Entry/Target を ICE画像面に乗せる。_refresh の先頭で毎回呼ぶ。

        自由度4（押し引き・θ・A/P偏向・L/R偏向）に対し拘束は2本なので、θ だけでは残差が残りうる。
        残差は隠さず mm で出す（先生決裁）。押し引きの位置が良ければ 0.0mm まで落ちる。"""
        self._lock3 = None
        if not (self.lock3 and self.viewMode == "ice" and self.vol is not None and len(self.path) >= 2
                and self.entry is not None and self.target is not None):
            return
        v = self.vol
        s = core.solve_theta_3points(self.path, self.zP, self.b1, self.b2, v.sx, v.sy, v.dz,
                                     self.entry, self.target, tip_high_z=self.tipHighZ)
        if s is None:
            return
        self.theta = float(s["theta"]) % 360.0
        self._lock3 = s
        self.sTheta.blockSignals(True)                       # 解いた値をスライダへ（信号は出さない＝再入しない）
        self.sTheta.setValue(int(round(self.theta)) % 360)
        self.sTheta.blockSignals(False)
    def _set_b1(self, v): self.b1 = float(v); self._refresh()
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
    def _clear_needle(self):
        self._snap_undo()
        self.entry = None; self.target = None; self.ptMode = 0
        self.aim_tip = None; self.aimMode = False; self.aim_torque = 0.0
        self._update_step_ui(); self._refresh()
    def _set_step(self, s): self.step = s; self._update_step_ui(); self._refresh()
    def _set_ptmode(self, m): self.ptMode = m; self._update_step_ui()

    def _toggle_aim(self):
        """『実際の針先』モード：ONの間、ICEクリックはEntry/Targetでなくaim_tipを更新する。"""
        self.aimMode = self.aimBtn.isChecked(); self._refresh_toggles()

    def _nudge_torque(self, deg):
        """手元でカニューラを右/左に回した想定角度を進める。予測点線(2cm)の曲がる向きに反映。"""
        self.aim_torque = (self.aim_torque + deg + 180.0) % 360.0 - 180.0
        self.hubWidget.set_torque(self.aim_torque); self._refresh()

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
            col = "#5fd282" if res < 2.0 else ("#ffd246" if res < 8.0 else "#F08F69")
            lines.append(f"<span style='color:{col};'>"
                         + L(f"◎ 3-point lock ON — θ solved to {self.theta:.0f}°. "
                             f"Off-plane: Entry {oe:+.1f} mm / Target {ot:+.1f} mm",
                             f"◎ 3点固定 ON — θを {self.theta:.0f}° に自動調整。"
                             f"面外のズレ: Entry {oe:+.1f} mm / Target {ot:+.1f} mm") + "</span>")
            if res >= 2.0:
                lines.append("<span style='color:#9fb4c8;'>"
                             + L("Push/pull the catheter to reduce the remaining offset "
                                 "(θ alone cannot close it at this position).",
                                 "押し引きで残りのズレを詰められます"
                                 "（この位置では θ だけでは合わせきれません）") + "</span>")
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

    def _refresh(self):
        if self.vol is None:
            return
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

    # ---------- オーバーレイ（CT 3断面） ----------
    def _overlay(self, p, to_widget, plane):
        if self.vol is None:
            return
        v = self.vol; nz = v.shape[0]; g = self._geom()
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
        if self.viewMode != "surface":
            if len(self.path) >= 2:
                sp = sorted(self.path, key=lambda q: q[0])
                line = [to_widget(*core.proj_mm([x * v.sx, y * v.sy, z * v.dz], v.sx, v.sy, v.dz, plane, nz))
                        for (z, y, x) in sp]
                ivc_alpha = 255 if self.step == 0 else 120        # Step1(IVCパス編集中)は濃く、Step2以降は薄く＝重なるICE扇を隠さない
                p.setPen(QPen(QColor(95, 205, 235, ivc_alpha), 2)); p.setBrush(Qt.NoBrush); p.drawPolyline(QPolygonF(line))
            p.setBrush(Qt.NoBrush); p.setPen(QPen(REDC, 2))
            for (z, y, x) in self.path:
                cc, rr = core.proj_mm([x * v.sx, y * v.sy, z * v.dz], v.sx, v.sy, v.dz, plane, nz)
                p.drawEllipse(to_widget(cc, rr), 4, 4)
        # Entry / Target（緑/赤・ラベル・ドラッグ可）。現在スライスが真の点と一致したら強調
        hits = []
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
            f = QFont(); f.setPointSize(16 if emph else 13); f.setBold(emph); p.setFont(f)
            p.setPen(col); p.drawText(w + QPointF(r + 4, -r), pid.capitalize() + (" ◀ on slice" if emph else ""))
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
                f = QFont(); f.setPointSize(15 if et else 12); f.setBold(et); p.setFont(f); p.setPen(NEEDLE_COL)
                p.drawText(tw + QPointF(11, -6), L("Actual needle", "実際の針") + (" ◀ on slice" if et else "")); p.setFont(QFont())
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
                    f = QFont(); f.setPointSize(15); f.setBold(True); p.setFont(f); p.setPen(NEEDLE_COL)
                    p.drawText(w + QPointF(11, -6), L("Actual needle", "実際の針") + " ◀ on plane"); p.setFont(QFont())
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
                f = QFont(); f.setPointSize(16 if emph else 13); f.setBold(emph); p.setFont(f)
                p.setPen(col); p.drawText(w + QPointF(rr2 + 4, -rr2), pid.capitalize() + (" ◀ on plane" if emph else ""))
                hits.append((pid, c, r))
        p.setFont(QFont())
        self.ice.hit_points = hits
        pw = self._pred_world()                              # 予習モード：プロット追跡＋前方予測
        if pw is not None:
            self._paint_pred(p, pw, lambda Q: to_widget(*to_px(Q)[:2]), label=True)

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

    def _on_liver_done(self, res):
        self.liver = res; self.p3d.liver = res; self.p3d.update()
        self._maybe_compute_liver()                      # 計算中にパスが変わっていれば追従

    def _stop_workers(self):
        """走行中の背景スレッド(肝抽出/DICOM読込)に中断を頼み、終わるまで待つ。

        QThread が走行中に破棄されると Qt は qFatal→abort() する。closeEvent だけでは足りない:
        Cmd+Q や更新後の再起動は QApplication.quit() を通り、closeEvent を呼ばずに終了するため、
        aboutToQuit からも必ずここを通す。
        """
        for w in [getattr(self, "_liver_worker", None), getattr(self, "_body_worker", None)] \
                + list(getattr(self, "_bg_workers", [])):
            try:
                if w is not None and w.isRunning():
                    w.requestInterruption(); w.wait(3000)
            except RuntimeError:                         # 既に C++ 側破棄済み等は無視
                pass

    def closeEvent(self, e):
        """終了時、①患者の作業状態が開いていれば保存するか確認（先生要望）、②走行中の背景スレッド
        (肝抽出/DICOM読込)を待つ。QThread が走行中に破棄されると Qt が abort() するため②は必須。"""
        if self.stack.currentWidget() is self.viewer_page and self.vol is not None and self.current_study_uid:
            resp = QMessageBox.question(self, L("Save before closing?", "閉じる前に保存しますか？"),
                L("Save your current work state (IVC path, Entry/Target, actual needle tip, "
                  "view settings) for this patient before closing?",
                  "閉じる前に、この患者の今の作業状態（IVCパス・Entry/Target・実際の針先・表示設定）を"
                  "保存しますか？"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if resp == QMessageBox.Cancel:
                e.ignore(); return
            if resp == QMessageBox.Yes:
                self._save_slot(self._pick_save_slot())
        self._stop_workers()
        super().closeEvent(e)

    # ---------- 3D linkage 構築（plugin update3D 相当） ----------
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


def path_from_open_event(qurl, qfile):
    """macOSの「開く」イベント(QFileOpenEvent)からローカルパスを取り出す。
    - tipsiceplanner://open?dir=<urlencoded path>  → dir/path/file クエリを返す
    - ローカルファイル/フォルダの直接オープン         → そのパスを返す
    純粋関数（テスト容易）。受け取れなければ None。"""
    if qurl is not None and isinstance(qurl, QUrl) and qurl.scheme() == URL_SCHEME:
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


class TIPSApp(QApplication):
    """FileOpen(=macOSの開く/URLスキーム)を捕まえて、用意ができ次第ハンドラへ渡す。
    起動直後(ウィンドウ未生成)に届いたイベントは _OpenDispatcher がバッファして後で流す。"""
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


def main():
    import os
    app = TIPSApp(sys.argv)
    # 設定/カタログの保存先(QStandardPaths.AppDataLocation)はQCoreApplication.applicationName()に依存し、
    # 未設定だと実行環境(凍結アプリ/素のpython等)でexe名から暗黙に決まり、環境によってブレうる。
    # 明示指定して固定＝配布アプリの実際の保存先(~/Library/Application Support/TIPS ICE Planner/TIPSPlanner)と
    # 完全一致させる。ここがずれると保存先フォルダごと変わり、更新のたびに設定が読めなくなる。
    app.setApplicationName("TIPS ICE Planner")
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
    win = MainWindow(); win.show()
    app.set_open_handler(win.open_external_path)             # 外部アプリからの「開く」を受け付ける
    for a in sys.argv[1:]:                                   # 新規起動時に渡されたフォルダ/ファイルも開く
        if not a.startswith("-") and os.path.exists(a):
            win.open_external_path(a); break
    QMessageBox.information(win, "TIPS ICE Planner — research / education tool",
        "Research, education and self-training only.\n\n"
        "· Not a certified medical device.\n· Not intra-procedural navigation.\n"
        "· The operator makes all final clinical decisions.")
    win._check_updates(silent=True)             # 起動時に配布フォルダの新版を自動チェック（あればワンクリック更新を提案）
    win._maybe_show_tip_at_startup()            # 今日のヒント（VS Code風・チェックボックスでオフ可）
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
