"""AcuNav ハンドルの絵をそのまま操作パネルにした対話式ウィジェット。
各パーツをマウスでドラッグして操作する：
  φ1 サムホイール ↕ = A-P 偏向(b1) / φ2 サムホイール ↕ = L-R 偏向(b2) /
  本体(青、持ち手) ↕ = θ 回転 / 本体 ↔ = プローブ前後(頭尾方向)。
描画は mockup/acunav_handle_anim.py の見た目を QPainter に移植（先生承認済みデザイン）。
"""
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget, QSizePolicy

try:
    from i18n import L
except Exception:                                    # 単体テスト等でi18nが無くても動く
    def L(en, ja):
        return ja

# 配色（mock と同一。BGだけは下部帯 #14253a に合わせて一体化）
BG = QColor(0x14, 0x25, 0x3a)
TEAL = QColor(43, 91, 113); TEAL_D = QColor(28, 59, 77); TEAL_L = QColor(63, 125, 149)
STEEL = QColor(201, 210, 221); STEEL_D = QColor(139, 150, 165); STEEL_H = QColor(238, 242, 247)
STEEL_M = QColor(92, 102, 117)
SH = QColor(20, 24, 29); SHL = QColor(43, 50, 59)
CY = QColor(95, 200, 220); GREY = QColor(154, 166, 178)
TER = QColor(240, 143, 105); AMB = QColor(245, 177, 60); WHT = QColor(230, 238, 244)

KX, KY = 0.72, 0.16          # 斜め投影：depth(L-R) は主に横へ
AXY = 30.0                   # ハンドル軸のモックy
MW, MH = 138.0, 62.0         # モック座標系の幅・高さ


def _rot3(v, k, ang):
    v = np.asarray(v, float); k = np.asarray(k, float); k = k / (np.linalg.norm(k) + 1e-12)
    return v * np.cos(ang) + np.cross(k, v) * np.sin(ang) + k * (k @ v) * (1 - np.cos(ang))


class HandleControl(QWidget):
    b1Changed = Signal(float)        # A-P 偏向(°)
    b2Changed = Signal(float)        # L-R 偏向(°)
    thetaChanged = Signal(float)     # θ 回転(°)
    probeChanged = Signal(float)     # プローブ前後(0-100)

    def __init__(self):
        super().__init__()
        self.b1 = 0.0; self.b2 = 0.0; self.theta = 180.0; self.probe = 50.0
        self.probe_enabled = False
        self._drag = None; self._p0 = None; self._v0 = None
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(L("Drag the wheels = deflect A-P/L-R.  Drag the body: up/down = rotate θ, left/right = push-pull.",
                          "ホイールをドラッグ=A-P/L-R偏向。本体をドラッグ：上下=θ回転、左右=プローブ前後。"))

    # 外部から現在値を反映（スライダー/復元と同期）
    def set_state(self, b1, b2, theta, probe, probe_enabled=None):
        self.b1 = float(b1); self.b2 = float(b2); self.theta = float(theta); self.probe = float(probe)
        if probe_enabled is not None:
            self.probe_enabled = bool(probe_enabled)
        self.update()

    # ---------- 座標変換（モック座標 ⇔ ウィジェットpx） ----------
    # 横長の器具なので X は最大2.6倍まで引き伸ばして帯の幅を活用（ラベルの重なりも解消）。Yは高さ基準。
    def _frame(self):
        W, H = self.width(), self.height()
        sy = H / MH
        sx = min(W / MW, sy * 2.6)
        ox = (W - MW * sx) / 2.0; oy = (H - MH * sy) / 2.0
        return sx, sy, ox, oy

    def _px(self, x, y):
        sx, sy, ox, oy = self._frame(); return QPointF(ox + x * sx, oy + (MH - y) * sy)

    def _mock(self, pos):
        sx, sy, ox, oy = self._frame(); return (pos.x() - ox) / sx, MH - (pos.y() - oy) / sy

    # ---------- 幾何（先端の曲がり・扇） ----------
    def _tip_geom(self):
        t = np.array([-1.0, 0, 0]); up = np.array([0.0, 1, 0]); dep = np.array([0.0, 0, 1])
        ap = np.radians(self.b1); ay = np.radians(self.b2); A = float(np.hypot(ap, ay)); Ltip = 20.0
        S = np.array([30.0, AXY, 0.0])
        if A < 1e-4:
            s = np.linspace(0, Ltip, 20); arc = S[None, :] + np.outer(s, t); ttan = t.copy()
        else:
            n = (ap * up + ay * dep) / A; R = Ltip / A; s = np.linspace(0, Ltip, 20)
            arc = S[None, :] + (R * np.sin(s / R))[:, None] * t + (R * (1 - np.cos(s / R)))[:, None] * n
            ttan = t * np.cos(A) + n * np.sin(A)
        return S, arc, ttan / (np.linalg.norm(ttan) + 1e-12)

    @staticmethod
    def _prj(P):
        P = np.asarray(P, float)
        return np.array([P[..., 0] + KX * P[..., 2], P[..., 1] + KY * P[..., 2]])

    # ---------- 描画 ----------
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        self._paint(p, labels=True)

    def _paint(self, p, labels=True):
        """実際の描画本体。paintEvent(自分自身への描画)と、他ペインの隅への縮小参考表示
        (main.py MainWindow._draw_handle_ref)の両方から、同じpainterへ直接呼べるよう分離。
        後者は別ウィジェット/QPixmapを介さない＝入れ子のpaint device生成によるQtの警告を避けるため。"""
        p.fillRect(self.rect(), BG)
        sx, sy, ox, oy = self._frame()
        u = sy                                      # 線幅の基準（縦スケール・引き伸ばしでも太らない）

        def poly(pts2):                              # モック2D点列(N,2)→QPolygonF
            return QPolygonF([self._px(x, y) for x, y in pts2])

        def line(pts2, col, w):
            pen = QPen(col, max(1.0, w * u)); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPolyline(poly(pts2))

        def taper(x0, x1, prof, fc, ec, w=1.0):
            xs = np.linspace(x0, x1, 60); rs = np.array([prof(x) for x in xs])
            top = np.column_stack([xs, AXY + rs]); bot = np.column_stack([xs[::-1], AXY - rs[::-1]])
            p.setBrush(QBrush(fc)); p.setPen(QPen(ec, max(1.0, w * u)))
            p.drawPolygon(poly(np.vstack([top, bot])))

        # ===== カテーテル：straight + 3D bend tip + 扇 =====
        S, arc, ttan = self._tip_geom()
        line([[47, AXY], [30, AXY]], SH, 3.6); line([[47, AXY], [30, AXY]], SHL, 1.3)
        gh = self._prj(S[None, :] + np.outer(np.linspace(0, 20, 2), np.array([-1.0, 0, 0]))).T
        pen = QPen(GREY, max(1.0, 0.5 * u)); pen.setStyle(Qt.DashLine); p.setPen(pen); p.setBrush(Qt.NoBrush)
        p.drawPolyline(poly(gh))
        A2 = self._prj(arc).T
        line(A2, SH, 3.2); line(A2, SHL, 1.0)
        tip = arc[-1]
        perp0 = np.cross(ttan, [0.0, 0, 1])
        if np.linalg.norm(perp0) < 1e-6:
            perp0 = np.cross(ttan, [1.0, 0, 0])
        perp0 = perp0 / (np.linalg.norm(perp0) + 1e-12)
        beam = _rot3(perp0, ttan, np.radians(self.theta))
        c3 = tip + ttan * 1.2
        quad = np.array([c3 + perp0 * 1.2, c3 + perp0 * 1.2 + ttan * 4.0,
                         c3 - perp0 * 1.2 + ttan * 4.0, c3 - perp0 * 1.2])
        p.setBrush(QBrush(STEEL)); p.setPen(QPen(STEEL_D, max(1.0, 0.4 * u)))
        p.drawPolygon(poly(self._prj(quad).T))
        apex = tip + ttan * 2.0; depth = 19.0; half = np.radians(33)
        fan = [apex] + [apex + (np.cos(a) * beam + np.sin(a) * ttan) * depth
                        for a in np.linspace(-half, half, 20)]
        fc = QColor(CY); fc.setAlpha(55); p.setBrush(QBrush(fc)); p.setPen(QPen(CY, max(1.0, 0.4 * u)))
        p.drawPolygon(poly(self._prj(np.array(fan)).T))
        line([tuple(self._prj(apex)), tuple(self._prj(apex + beam * depth))], CY, 0.5)

        # ===== 歪み除け =====
        taper(47, 53, lambda x: np.interp(x, [47, 53], [1.2, 4.2]), WHT, STEEL_D)

        # ===== 曲げノブ（サムホイール）：横リッジが偏向角で縦スクロール =====
        def knob(cx, w, spin, active):
            R = 5.6; m = 0.9
            rect = QRectF(self._px(cx - w / 2, AXY + R), self._px(cx + w / 2, AXY - R))
            p.setBrush(QBrush(QColor(143, 154, 168) if not active else QColor(175, 186, 200)))
            p.setPen(QPen(STEEL_D, max(1.0, 1.2 * u))); p.drawRoundedRect(rect, 1.6 * u, 1.6 * u)
            N = 18
            for i in range(N):
                th = 2 * np.pi * i / N + spin; c = float(np.cos(th))
                if c <= 0.06:
                    continue
                Y = AXY + (R - 0.7) * np.sin(th)
                shade = STEEL_H if i % 2 == 0 else STEEL_M
                pen = QPen(shade, max(0.8, (0.5 + 1.6 * c) * u * 0.55)); pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(self._px(cx - w / 2 + m, Y), self._px(cx + w / 2 - m, Y))
            p.setBrush(Qt.NoBrush); p.setPen(QPen(TER if active else STEEL_D, max(1.0, (1.7 if active else 1.2) * u)))
            p.drawRoundedRect(rect, 1.6 * u, 1.6 * u)

        k1x, k2x = 58, 69
        knob(k1x, 7.6, np.radians(self.b1) * 2.2, self._drag == 'b1')
        knob(k2x, 7.6, np.radians(self.b2) * 2.2, self._drag == 'b2')

        # ===== 本体（青）＋ TIPS ICE PLANNER 刻印（θで回り込む） =====
        def bprof(x): return np.interp(x, [74, 82, 100, 112, 118], [4.6, 7.4, 7.6, 5.0, 2.2])
        taper(74, 118, bprof, TEAL, TEAL_D, 1.4)
        xs = np.linspace(76, 116, 40); line(np.column_stack([xs, AXY + np.array([bprof(x) for x in xs]) - 1.3]), TEAL_L, 0.9)
        # 円筒(本体)に巻いた文字の回転を正確に反映：横スケール=cos(theta)で前後を表現
        # → 正面(fr=+1)は等倍、真横(fr=0)で線状に消え、背面(fr<0)は符号反転で鏡像(後ろから見た向き)。
        # 背面は薄く(alphaの下限を低く設定)して「奥にある」ことを示す。
        br = np.radians(self.theta); fr = float(np.cos(br))
        f = QFont(); f.setPixelSize(max(7, int(1.6 * u))); f.setItalic(True)
        tw = QFontMetrics(f).horizontalAdvance("TIPS ICE PLANNER")
        pc = self._px(96, AXY + 3.0 * np.sin(br))
        alpha = 0.15 + 0.85 * max(0.0, fr)                      # 正面=1.0 / 背面=0.15で薄く
        tc = QColor(230, 238, 244); tc.setAlphaF(alpha)
        sxScale = fr if abs(fr) > 0.03 else (0.03 if fr >= 0 else -0.03)   # 縮退変換(scale=0)を回避
        p.save()
        p.translate(pc); p.scale(sxScale, 1.0)
        p.setFont(f); p.setPen(tc)
        p.drawText(QPointF(-tw / 2, f.pixelSize() * 0.35), "TIPS ICE PLANNER")
        p.restore()

        # ===== 近位ケーブル =====
        taper(118, 124, lambda x: np.interp(x, [118, 124], [2.2, 1.0]), STEEL, STEEL_D)
        line([[124, AXY], [130, AXY]], SH, 2.0)

        # ===== ラベル＋掴み方 =====
        if labels:
            self._labels(p, u)

    def _labels(self, p, u):
        """ラベル＝各コントロールの近くに2行（1行目:名前+現在値 / 2行目:操作方法）。
        帯の高さは一定なのでフォントは固定px・行間は実pxで確保（重なり防止）。"""
        f = QFont(); f.setPixelSize(11); f.setBold(True)
        fs = QFont(); fs.setPixelSize(9)

        def txt2(x, y, col, main, sub, above=True):
            """(x,y)=モック座標のアンカー。above=True なら上に向かって2行、False なら下に向かって2行。"""
            pt = self._px(x, y)
            wm = QFontMetrics(f).horizontalAdvance(main); ws = QFontMetrics(fs).horizontalAdvance(sub)
            if above:
                y_sub, y_main = pt.y(), pt.y() - 12               # サブが下・メインが上
            else:
                y_main, y_sub = pt.y(), pt.y() + 12
            p.setFont(f); p.setPen(col); p.drawText(QPointF(pt.x() - wm / 2, y_main), main)
            p.setFont(fs); p.setPen(GREY); p.drawText(QPointF(pt.x() - ws / 2, y_sub), sub)

        txt2(56, AXY + 9.0, TER,
             L("A-P deflect", "A-P偏向") + f"  {self.b1:+.0f}°", L("drag ↕", "上下ドラッグ"), above=True)
        txt2(71, AXY - 10.5, TER,
             L("L-R deflect", "L-R偏向") + f"  {self.b2:+.0f}°", L("drag ↕", "上下ドラッグ"), above=False)
        # 持ち手＝θ回転とプローブ前後を1箇所に統合（上下=回転／左右=前後、大きく掴みやすい本体で操作）
        body_main = L("θ", "θ") + f" {self.theta:.0f}°"
        if self.probe_enabled:
            body_main += "  ·  " + L("Push/Pull", "前後") + f" {self.probe:.0f}%"
        txt2(98, AXY + 9.0, AMB, body_main, L("↕ rotate   ↔ push/pull", "↕回転　↔前後"), above=True)

    # ---------- マウス（各パーツをドラッグ） ----------
    def mousePressEvent(self, e):
        mx, my = self._mock(e.position()); reg = None
        if 54 <= mx <= 62 and 23 <= my <= 37:
            reg = 'b1'
        elif 65 <= mx <= 73 and 23 <= my <= 37:
            reg = 'b2'
        elif 74 <= mx <= 118 and 21 <= my <= 39:
            reg = 'body'                          # 持ち手：上下=θ回転／左右=push-pull（1領域に統合）
        self._drag = reg; self._p0 = e.position(); self._v0 = (self.b1, self.b2, self.theta, self.probe)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        d = e.position() - self._p0; b1, b2, th, pr = self._v0
        if self._drag == 'b1':
            self.b1 = max(-80.0, min(80.0, b1 + (-d.y()) * 0.5)); self.b1Changed.emit(self.b1)
        elif self._drag == 'b2':
            self.b2 = max(-80.0, min(80.0, b2 + (-d.y()) * 0.5)); self.b2Changed.emit(self.b2)
        elif self._drag == 'body':
            self.theta = (th + (-d.y()) * 0.7) % 360.0; self.thetaChanged.emit(self.theta)
            if self.probe_enabled:                # 左右ドラッグ＝プローブ前後（IVCパス確定後のみ有効）
                self.probe = max(0.0, min(100.0, pr + (-d.x()) * 0.4)); self.probeChanged.emit(self.probe)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None; self.update()
