"""共通ウィジェット層（正本 = engine/core/panes.py。各アプリへは sync_core.py で物理コピー配布）。

main.py から独立させた「術式に依存しない」画面部品：
  - 共通パレット定数（TERRA 等）と全体 QSS（STYLE / SS_ON / SS_OFF）
  - 画面枠・操作アイコン・クリック診断ログなどのヘルパ
  - GestureBar（操作凡例。slim=スリム版 / 旧・説明文つき版の両対応）
  - CannulaHubWidget（RUPS-100 手元ハブの実機忠実図・スケール対応）
  - ImagePane / PaneCell / _CrossHandle / QuadPanes（4画面格子。sync_cross で連動/独立を選択）

Phase 3 の方針どおり、ここは「どの術式か」を一切知らない。
"""
from __future__ import annotations
import os
import sys
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QScrollBar, QHBoxLayout, QVBoxLayout, QSizePolicy, QFrame, QSplitter)
from PySide6.QtGui import (QImage, QPainter, QColor, QPen, QPolygonF, QFont,
                           QNativeGestureEvent, QPainterPath, QFontMetrics)
from PySide6.QtCore import Qt, QPointF, QRectF, Signal

from i18n import L
try:
    import settings_store
except Exception:                                        # pragma: no cover
    settings_store = None

# ── 共通パレット・スタイル ──────────────────────────────────────
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

ACTIVE = QColor(240, 143, 105)            # アクティブ画面の枠色（テラコッタ）
INACTIVE_FRAME = QColor(34, 52, 74)


def _roll_xy(wx, wy, cx, cy, deg):
    """ウィジェット点(wx,wy)を中心(cx,cy)まわりに deg 度回す（エコー表示の無段階ロール）。"""
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
    """操作の凡例（拡大縮小 / 階調 / 移動）。horizontal=横1行 / vertical=複合帯の左ブロック（3行積み）。

    vertical のとき slim=True はスリム版（アイコン＋タイトルのみ・説明文はツールチップ、
    先生指摘 2026-07-18）、slim=False は旧・説明文つき版（eus-planner の現行表示）。
    見た目統一（3c-3 で eus もスリム化）まで両対応で持つ。"""

    def __init__(self, vertical=False, slim=True):
        super().__init__(); self.vertical = vertical; self.slim = slim
        if vertical:
            if slim:
                self.setFixedWidth(112)                 # スリム化（説明文はツールチップへ・先生指摘 2026-07-18）
                self.setToolTip(L("Zoom: pinch / ⌘+scroll\nWindow/Level: drag\nMove: ⌥ / Space + drag",
                                  "拡大縮小: ピンチ / ⌘+スクロール\n階調 (W/L): ドラッグ\n移動: ⌥ / Space + ドラッグ"))
            else:
                self.setFixedWidth(198)                 # 複合帯の左端に縦積み＝縦方向の行数を増やさない
                self.setMinimumHeight(110)              # 3行(bh34×3)が収まる高さ。下端揃え時に潰れないよう固定
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
        if self.vertical and self.slim:                  # 縦積み（アイコン＋タイトルのみ・操作方法はツールチップ）
            f1 = QFont(); f1.setPointSize(10); f1.setBold(True)
            ents = self._entries()
            bh = 22; y0 = max(2, (self.height() - bh * len(ents)) // 2)
            for i, (draw, title, _how) in enumerate(ents):
                y = y0 + i * bh
                draw(p, 6, y + 3, 14, ACTIVE)
                p.setFont(f1); p.setPen(ACTIVE); p.drawText(26, y + 15, title)
            return
        if self.vertical:                                # 縦積み（アイコン＋タイトル、下に操作方法）＝旧版
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
    def __init__(self, scale=1.0):
        super().__init__(); self._k = float(scale)                             # 一様スケール（絵は論理74×82で描く）
        self.setFixedSize(int(74 * self._k), int(82 * self._k)); self.torque_deg = 0.0
        self.setToolTip("")   # _torque_group で術者視点の説明を設定

    def set_torque(self, deg):
        self.torque_deg = deg; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(0x14, 0x25, 0x3a))
        p.scale(self._k, self._k)                            # 以降は論理74×82座標で描く（拡大対応）
        W, H = 74.0, 82.0
        cx, cy = W / 2, H / 2 + 6
        # 文字サイズはアプリのフォントサイズ設定に追従（先生報告：小さすぎ＆変更に追従しないバグ）。
        # self.font() は _set_font_size の findChildren(setFont) を反映する（QSS の据え置きは効かない）。
        fp = self.font().pointSize()
        if fp <= 0:
            fp = 12
        s_lr = max(9, round(fp * 0.92)); s_ang = max(10, round(fp * 1.0))
        # 「術者から見た手元」＝上端に眼マークで視点を明示（英語版では Operator）。ラベルはフォント設定に
        # 追従して大きくするが、長い "Operator" は 74px 幅に収まるまで自動縮小する（術者=短いので設定サイズまで拡大）。
        op = "\U0001F441 " + L("Operator", "術者")
        f0 = QFont(); s_op = min(max(8, round(fp * 0.75)), 11)
        while s_op > 6:
            f0.setPointSize(s_op)
            if QFontMetrics(f0).horizontalAdvance(op) <= W - 2:
                break
            s_op -= 1
        f0.setPointSize(s_op); p.setFont(f0); p.setPen(QColor(160, 176, 192))
        p.drawText(QRectF(0, 0, W, 15), Qt.AlignCenter, op)
        # シャフト（体内へ続く方向・常に上向き＝0°の基準）
        p.setPen(QPen(QColor(120, 150, 180), 4)); p.drawLine(QPointF(cx, cy), QPointF(cx, 15))
        p.setPen(QPen(QColor(160, 170, 180), 2)); p.drawLine(QPointF(cx, cy), QPointF(cx, H - 18))
        # ハブ（回転体）
        p.save(); p.translate(cx, cy); p.rotate(self.torque_deg)
        p.setBrush(QColor(0xc9, 0xd3, 0xdd)); p.setPen(QPen(QColor(0x8a, 0x96, 0xa4), 1.5))
        p.drawEllipse(QPointF(0, 0), 9, 9)
        # ウイング（羽根状ハンドル）＋矢印＝カーブの向き
        wing = QPolygonF([QPointF(-28, -5), QPointF(28, -5), QPointF(28, 5), QPointF(-28, 5)])
        p.setBrush(QColor(0xe9, 0xed, 0xf2)); p.setPen(QPen(QColor(0x8a, 0x96, 0xa4), 1.2)); p.drawPolygon(wing)
        p.setPen(QPen(TERRA, 2.4)); p.drawLine(QPointF(0, 0), QPointF(24, 0))
        p.drawLine(QPointF(24, 0), QPointF(17, -6)); p.drawLine(QPointF(24, 0), QPointF(17, 6))
        p.restore()
        # 術者視点の固定 L/R 目印（回転しない）＝どちらが術者の左右かを常に示す
        fLR = QFont(); fLR.setPointSize(s_lr); fLR.setBold(True); p.setFont(fLR); p.setPen(QColor(120, 200, 235))
        p.drawText(QRectF(0, cy - 10, 14, 20), Qt.AlignCenter, "L")
        p.drawText(QRectF(W - 14, cy - 10, 14, 20), Qt.AlignCenter, "R")
        f = QFont(); f.setPointSize(s_ang); f.setBold(True); p.setFont(f); p.setPen(TERRA)
        p.drawText(QRectF(0, H - 17, W, 16), Qt.AlignCenter, f"{self.torque_deg:+.0f}°")


# ============ 画像ペイン ============
# マウスの「調整ジェスチャ」→どのボタンか。設定で入れ替え可能（左クリック=点の配置は不変）。
# 既定は従来どおり: 階調(W/L)=左ドラッグ / 拡大縮小=右ドラッグ / 移動(パン)=中ドラッグ。
_GESTURE_DEFAULTS = {"wl": "left", "zoom": "right", "pan": "middle"}


def gesture_button(action):
    """action('wl'|'zoom'|'pan') が割り当てられているマウスボタン名を返す（設定→既定）。"""
    d = _GESTURE_DEFAULTS.get(action, "left")
    if settings_store is None:
        return d
    try:
        v = settings_store.store().value("gesture_%s" % action, d)
        return v if v in ("left", "right", "middle") else d
    except Exception:
        return d


def _btn_flag(name):
    return {"left": Qt.LeftButton, "right": Qt.RightButton, "middle": Qt.MiddleButton}.get(name, Qt.LeftButton)


class ImagePane(QWidget):
    wlChanged = Signal(float, float)
    clicked = Signal(float, float)          # image col,row
    wheelMoved = Signal(int)
    movePoint = Signal(str, float, float)   # id, col, row
    moveLabel = Signal(str, float, float)   # ラベルid, ウィジェット座標の移動量(dx,dy)＝画像上の文字をドラッグ
    activated = Signal(object)              # マウスが入った＝アクティブ画面

    def __init__(self, caption=""):
        super().__init__()
        self.caption = caption
        self.active = False
        self.img = None; self.phys_w = 1.0; self.phys_h = 1.0
        self.overlay_fn = None; self.hit_points = []        # [(id, col, row)]
        self.label_boxes = []                               # [(id, QRectF)] 画像上の文字ラベルの矩形（ドラッグ判定用）
        self.placeholder = "Open a CT to begin"
        self.zoom = 1.0; self.pan = QPointF(0, 0); self.roll_deg = 0.0   # 無段階ロール（エコー画面用）
        self._press = None; self._moved = False; self._drag_id = None; self._drag_label = None
        self._press_panmod = False                           # 押下時のパン意図をラッチ（離すレース対策）
        self._scroll_accum = 0.0; self._space = False        # トラックパッド: スクロール積算 / Space長押し=パン
        self.setMinimumSize(200, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)                  # Space長押しパンのためキー入力を受ける
        self.setMouseTracking(True)                          # ボタンを押さなくても移動イベントを受ける（ラベル上のホバーカーソル）

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

    def _near_label(self, wx, wy):
        """画像上の文字ラベル（Entry/Target/実際の針）の矩形内なら、そのラベルidを返す。
        後に描いた(上にある)ラベルを優先。掴みやすいよう数px の余裕を持たせる。"""
        for lid, rect in reversed(self.label_boxes):
            if rect.adjusted(-4, -4, 4, 4).contains(wx, wy):
                return lid
        return None

    def _pan_mod(self, e):
        """Space長押し or ⌥Option(macのみ)＝パン操作（トラックパッドで中ボタンの代替）。
        Alt は Windows でメニューバーAltと競合するため macOS 限定にする。"""
        alt = bool(e.modifiers() & Qt.AltModifier) and sys.platform == "darwin"
        return self._space or alt

    def mousePressEvent(self, e):
        self._press = e.position(); self._moved = False
        self._press_panmod = self._pan_mod(e)                # パン意図は押下時に確定（atomic）
        # ラベルのドラッグは点の配置・点のドラッグより優先（文字の上を掴んだら文字を動かす）
        self._drag_label = (self._near_label(e.position().x(), e.position().y())
                            if e.button() == Qt.LeftButton and not self._press_panmod else None)
        self._drag_id = (self._near_point(e.position().x(), e.position().y())
                         if e.button() == Qt.LeftButton and not self._press_panmod and self._drag_label is None else None)

    def mouseMoveEvent(self, e):
        if self._press is None:
            # ホバー時：文字ラベルの上なら移動カーソル＝掴んで動かせると分かる（発見しやすさ）
            if not self._space:
                over = self._near_label(e.position().x(), e.position().y()) is not None
                if over:
                    self.setCursor(Qt.SizeAllCursor)
                elif self.cursor().shape() == Qt.SizeAllCursor:
                    self.setCursor(Qt.ArrowCursor)
            return
        d = e.position() - self._press
        if abs(d.x()) + abs(d.y()) > 2.5:
            self._moved = True
        if (e.buttons() & Qt.LeftButton) and self._press_panmod:  # Space/Option+ドラッグ=パン（押下時の意図で固定）
            self.pan += d; self._press = e.position()
            self.setCursor(Qt.ClosedHandCursor); self.update()
        elif self._drag_label is not None and (e.buttons() & Qt.LeftButton):   # 画像上の文字ラベルを移動
            self.moveLabel.emit(self._drag_label, d.x(), d.y())
            self._press = e.position(); self.setCursor(Qt.SizeAllCursor)
        elif self._drag_id is not None and (e.buttons() & Qt.LeftButton):
            ic = self.to_image(e.position().x(), e.position().y())
            if ic:
                self.movePoint.emit(self._drag_id, ic[0], ic[1])
        else:
            # 調整ジェスチャ（W/L・拡大縮小・移動）は設定で割り当てたボタンで行う（既定=左/右/中）。
            # 左クリックでの点の配置・点のドラッグは上の分岐で守られる＝設定に関係なく不変。
            wlb = _btn_flag(gesture_button("wl"))
            zb = _btn_flag(gesture_button("zoom"))
            pb = _btn_flag(gesture_button("pan"))
            if e.buttons() & wlb:                               # W/L（既定=左ドラッグ）
                self.wlChanged.emit(d.x() * 3.0, -d.y() * 3.0); self._press = e.position()
            elif e.buttons() & zb:                              # 拡大縮小（既定=右ドラッグ・カーソル中心）
                self.zoom_at(float(np.exp(-d.y() * 0.01)), e.position()); self._press = e.position()
            elif e.buttons() & pb:                              # 移動＝パン（既定=中ドラッグ）
                self.pan += d; self._press = e.position(); self.update()

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._moved:
            if (e.button() == Qt.LeftButton and self._drag_id is None
                    and self._drag_label is None and not self._press_panmod):
                ic = self.to_image(e.position().x(), e.position().y())
                if ic:
                    _log_click(self, e, ic)                     # 位置ズレ調査用の診断ログ（軽量・失敗無害）
                    self.clicked.emit(ic[0], ic[1])
            elif e.button() == Qt.RightButton:                  # 右クリック=ズーム/パンをリセット
                self.reset_view()
        self._press = None; self._drag_id = None; self._drag_label = None; self._press_panmod = False
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
    縦の QSplitter に入れて作る。sync_cross=True（tips-planner）は上段と下段の縦仕切りを
    連動させて常に1本の通った線にし、交差点に掴み手（_CrossHandle）を置いて縦横を同時に
    動かせるようにする。sync_cross=False（eus-planner・先生要望 2026-07-17）は上下の
    縦仕切りを独立に動かし、掴み手は置かない。
    """
    def __init__(self, tl, tr, bl, br, sync_cross=True):
        super().__init__()
        self.top = QSplitter(Qt.Horizontal); self.top.addWidget(tl); self.top.addWidget(tr)
        self.bot = QSplitter(Qt.Horizontal); self.bot.addWidget(bl); self.bot.addWidget(br)
        self.vs = QSplitter(Qt.Vertical); self.vs.addWidget(self.top); self.vs.addWidget(self.bot)
        for s in (self.top, self.bot, self.vs):
            s.setChildrenCollapsible(False)                   # 画面をゼロまで潰せないようにする
            s.setHandleWidth(6)
            s.setOpaqueResize(True)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.vs)
        if sync_cross:
            self.top.splitterMoved.connect(self._sync_from_top)
            self.bot.splitterMoved.connect(self._sync_from_bot)
            self.vs.splitterMoved.connect(lambda *_: self._place())
            self.cross = _CrossHandle(self)
        else:
            # 上下の縦仕切りは *独立* に動かす（先生要望 2026-07-17：Axi|Cor と Sag|Echo を別々に）。同期は外す。
            self.cross = None                                 # 中央の「＋」掴み手も無し（個別の仕切りドラッグは残る）

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
        """掴み手を実際の交差点へ置き直す（掴み手が無ければ何もしない）。Qt が最小サイズで
        丸めた後の値を読むので、引っぱりすぎて画面が潰れそうな時も掴み手は必ず仕切りの上に乗る。"""
        if self.cross is None:
            return
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
