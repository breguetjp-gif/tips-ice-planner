"""Pane3D（3D linkage）の正本（engine/core/pane3d.py。各アプリへは sync_core.py で物理コピー配布）。

tips-planner / eus-planner 両版の**上位集合**として統一（Phase 3b, 2026-07-18）：
  - 描画状態は両術式の和集合（ICE: shaft/orange/arr、EUS: 先端硬性部/探触子/起上台針/経路 等）
  - AI解剖は「固定3構造（tips: IVC/門脈/肝血管）」と「ts_organs 辞書（eus: 色分け splat）」の両対応
  - 手描き管（hep_manual）は旧形式 [arr,…]（tips: 肝静脈ローズ）と新形式 [(種別, arr),…]
    （eus: 胆管/膵管を ORGAN_COLORS で色分け）を項目ごとに自動判別
  - 術式ごとの定数・文言は preset.py の PANE3D_* から読む：
      PANE3D_AI_BTN        左上AIボタンのラベル（"Vessel AI" / "GI tract AI"）
      PANE3D_SCOPE_STYLE   "ice"=明灰シャフト＋偏向先端＋アレイ / "eus"=軟性内視鏡（暗色太線＋先端硬性部）
      PANE3D_GHOST_COLOR / PANE3D_GHOST_OPACITY   AI ゴースト（肝 / 腸管）の色と不透明度
      PANE3D_TARGET_COLOR  Target/apex マーカー色
      PANE3D_EMPTY_HINT    パス未設定時の案内 (en, ja)
      PANE3D_LEGEND        下端の凡例 (en, ja)
"""
from __future__ import annotations
import sys
import numpy as np

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPolygonF, QBrush, QFont, QNativeGestureEvent
from PySide6.QtCore import Qt, QPointF, QRectF, Signal

from i18n import L
from panes import TERRA, GREENC, _frame
import tips_core as core
from tips_core import liver as liver_core
import preset


# ============ 3D linkage（QPainter・自由回転） ============
class Pane3D(QWidget):
    activated = Signal(object)            # マウスが入った＝アクティブ画面
    surfacePicked = Signal(int)          # 経腹: 3D体表上でプローブ設置/移動（body["surf"]のindex）

    def __init__(self):
        super().__init__()
        self.az = 0.0; self.el = -75.0; self._last = None    # 初期＝正面(AP/冠状・頭側上)からスタート
        self.shaft = None; self.orange = None; self.arr = None
        self.tip_outline = None; self.transducer = None      # EUS内視鏡の先端硬性部＋探触子（凸グリフ）
        self.scope_center = None; self.scope_dia = 14.6      # 内視鏡の芯線＋太さ＝円柱状の太いミミズで一体描画
        self.eus_needle = None; self.eus_needle_exit = None  # 穿刺針（探触子出口→起上台角で面内に伸展）
        self.route = None; self.show_route = True            # 内視鏡の進む経路（薄い参照ライン・On/Off）
        self.fan = None; self.needle = None
        self.probe_outline = None; self.probe_face = None    # 経腹コンベックス・プローブ本体（3D）
        self.probe_array = None; self.probe_button = None    # 青いアレイ帯・操作ボタン（実機風の詳細）
        self.entry = None; self.target = None; self.apex = None
        self.valid = False; self.active = False
        self.liver = None; self.show_liver = True            # 肝臓ゴースト
        self.liver_mode = "haze"; self.liver_opacity = 0.5
        self._liver_qimg = None; self._liver_buf = None
        self.ts_liver = None; self.ts_ivc = None; self.ts_portal = None   # TotalSegmentator: 肝/IVC/門脈（tips 固定3構造）
        self.ts_hepatic = None                               # 肝血管ツリー(liver_vessels=門脈枝+肝静脈枝・要ライセンス)
        self.ts_organs = {}                                  # {構造名: Nx3 mm点群}＝設定で表示ONの臓器/血管を色分け描画（eus）
        self.hep_manual = None                               # 手描きの管。[arr,…]（旧: 肝静脈）or [(種別, arr),…]（新: 胆管等）
        self.hep_drawing = False                             # 描画中＝点＋直線を出す／終了＝滑らか曲線のみ
        self.show_ts = True; self._ts_bufs = []              # AIセグメントを重ねる（有れば優先）
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
        self._space = False; self._press_panmod = False       # CTと同じパン操作（Space長押し / 中ドラッグ / ⌥Option）
        self.setFocusPolicy(Qt.StrongFocus)                   # Space長押しパンのためキー入力を受ける（CTペインと同じ）
        self.setMinimumSize(260, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 3Dパネル左上のAIボタン（押すと造影CTから解剖セグメントを生成→重ねる。ラベルは術式 preset から）
        from PySide6.QtWidgets import QPushButton
        self.vesselBtn = QPushButton(preset.PANE3D_AI_BTN, self)
        self.vesselBtn.setCheckable(True); self.vesselBtn.setFocusPolicy(Qt.NoFocus)
        self.vesselBtn.setCursor(Qt.PointingHandCursor)
        self.vesselBtn.setStyleSheet(
            "QPushButton{background:rgba(16,24,40,0.85);color:#F2B79E;border:1px solid #F08F69;"
            "border-radius:7px;padding:4px 11px;font-weight:600;}"
            "QPushButton:checked{background:#F08F69;color:#10182c;border-color:#F08F69;}"
            "QPushButton:disabled{color:#6b7180;border-color:#39414f;background:rgba(16,24,40,0.6);}")
        self.vesselBtn.adjustSize(); self.vesselBtn.move(8, 8)
        # 再レンダリング（設定変更を今のAI結果に反映＝キャッシュから作り直し、足りなければ再解析）
        self.rerenderBtn = QPushButton("⟳", self)
        self.rerenderBtn.setFocusPolicy(Qt.NoFocus); self.rerenderBtn.setCursor(Qt.PointingHandCursor)
        self.rerenderBtn.setStyleSheet(
            "QPushButton{background:rgba(16,24,40,0.85);color:#F2B79E;border:1px solid #F08F69;"
            "border-radius:7px;padding:4px 8px;font-weight:700;}"
            "QPushButton:disabled{color:#6b7180;border-color:#39414f;background:rgba(16,24,40,0.6);}")
        self.rerenderBtn.setToolTip("")   # MainWindow 側でi18nツールチップを設定
        self.rerenderBtn.adjustSize()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.vesselBtn.move(8, 8); self.vesselBtn.raise_()
        self.rerenderBtn.move(8 + self.vesselBtn.width() + 6, 8); self.rerenderBtn.raise_()

    def enterEvent(self, e):
        self.activated.emit(self); super().enterEvent(e)

    def set_geom(self, d):
        if d is None:
            self.valid = False
            # 有効なデバイス/針形状が無いときは古い形状を残さない。残すと apex・扇・オレンジ線・針が
            # 幽霊として描かれ、AI解剖だけ出す場面で「中心がずれる／謎の線が出る／回転中心が狂う」。
            self.shaft = self.orange = self.arr = self.fan = self.needle = None
            self.tip_outline = self.transducer = None        # EUS内視鏡の先端硬性部＋探触子グリフ
            self.scope_center = None
            self.eus_needle = self.eus_needle_exit = None
            self.route = None
            self.probe_outline = self.probe_face = self.probe_array = self.probe_button = None
            self.entry = self.target = self.apex = None
            self.aim_outline = self.aim_pred = self.aim_tip = None
            self.cannula_rod = self.needle_body = self.needle_tip = self.needle_pred = None
        else:
            self.valid = True
            self.shaft = d.get("shaft"); self.orange = d.get("orange"); self.arr = d.get("arr")
            self.tip_outline = d.get("tip_outline"); self.transducer = d.get("transducer")
            self.scope_center = d.get("scope_center"); self.scope_dia = d.get("scope_dia", 14.6)
            self.eus_needle = d.get("eus_needle"); self.eus_needle_exit = d.get("eus_needle_exit")
            self.route = d.get("route")
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
        """進行方向の『流れるダッシュ』（CT/エコーの予測線と同じ表現・先端→進行方向へ流す）。"""
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

    def _hep_items(self):
        """hep_manual の項目を (種別 or None, np.ndarray) に正規化して返す（新旧両形式に対応）。"""
        for v in (self.hep_manual or []):
            if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], str):
                yield v[0], np.asarray(v[1], float)
            else:
                yield None, np.asarray(v, float)

    def _draw_ts(self, p, c, s, b):
        """AI解剖を重ねる: ゴースト（色/不透明度は preset）＋固定3構造（tips: IVC/門脈/肝血管）
        ＋ ts_organs 辞書（eus: ORGAN_COLORS で色分け splat）。
        ビュー(視点・ズーム・パン・中心・サイズ・データ)が変わらない限り再計算せずキャッシュ画像を再描画する
        ＝進行方向ダッシュのアニメ等で毎フレーム数千点を描き直して100%CPUになるのを防ぐ。"""
        w, h = b.width(), b.height(); seff = s * self.zoom3d
        off = (self.pan3d.x(), self.pan3d.y())
        ck = tuple(np.round(np.asarray(c, float), 1)) if c is not None else (0.0, 0.0, 0.0)
        organs = self.ts_organs or {}
        okey = tuple(sorted((n, id(v)) for n, v in organs.items()))
        key = (round(self.az, 2), round(self.el, 2), round(self.zoom3d, 3),
               round(off[0], 1), round(off[1], 1), w, h, ck,
               id(self.ts_liver), id(self.ts_ivc), id(self.ts_portal), id(self.ts_hepatic),
               okey, self._last is not None)
        if key == getattr(self, "_ts_ckey", None) and self._ts_bufs:
            for buf in self._ts_bufs:                        # ビュー不変＝キャッシュ再描画（再計算しない）
                p.drawImage(0, 0, QImage(buf.data, w, h, 4 * w, QImage.Format_RGBA8888))
            return
        self._ts_ckey = key; self._ts_bufs = []
        if self.ts_liver is not None:                        # ゴースト（tips: 肝 / eus: 腸管＝スコープの通り道）
            rgba = liver_core.render_ghost(self.ts_liver, self.az, self.el, c, seff, w, h,
                                           mode="surface", opacity=preset.PANE3D_GHOST_OPACITY,
                                           color=preset.PANE3D_GHOST_COLOR,
                                           offset=off, splat_rad=2, ss=1 if self._last is not None else 2)
            if rgba is not None:
                buf = np.ascontiguousarray(rgba); self._ts_bufs.append(buf)
                p.drawImage(0, 0, QImage(buf.data, w, h, 4 * w, QImage.Format_RGBA8888))
        # 固定3構造（tips）: IVC=灰緑 / 門脈本幹=青 / 肝血管ツリー(肝静脈含む)=ローズ。肝血管は門脈より上に薄く重ねる。
        for pts, col in ((self.ts_ivc, (150, 176, 188)), (self.ts_portal, (74, 150, 236)),
                         (self.ts_hepatic, (222, 104, 120))):
            if pts is None:
                continue
            rgba = liver_core.render_points(pts, self.az, self.el, c, seff, w, h, col, off, rad=2)
            if rgba is not None:
                buf = np.ascontiguousarray(rgba); self._ts_bufs.append(buf)
                p.drawImage(0, 0, QImage(buf.data, w, h, 4 * w, QImage.Format_RGBA8888))
        if organs:                                           # 選択された各構造を固有色で（eus）
            import ts_seg
            for name, pts in organs.items():
                if pts is None or not len(pts):
                    continue
                col = ts_seg.ORGAN_COLORS.get(name, (200, 200, 200))
                rgba = liver_core.render_points(pts, self.az, self.el, c, seff, w, h, col, off, rad=2)
                if rgba is not None:
                    buf = np.ascontiguousarray(rgba); self._ts_bufs.append(buf)
                    p.drawImage(0, 0, QImage(buf.data, w, h, 4 * w, QImage.Format_RGBA8888))

    def _has_ts(self):
        return self.show_ts and (self.ts_liver is not None or self.ts_ivc is not None
                                 or self.ts_portal is not None or self.ts_hepatic is not None
                                 or bool(self.ts_organs))

    def _has_hep(self):
        return bool(self.hep_manual) and any(len(v) >= 1 for v in self.hep_manual)

    def _anat_center(self):
        """デバイスパスが無いときの3D中心＝AI解剖 or 肝ゴースト or 手描き管の重心（mm）。何も無ければ None。"""
        lv = self.ts_liver
        if isinstance(lv, dict) and lv.get("surf") is not None and len(lv["surf"]):
            return np.asarray(lv["surf"], float).mean(0)
        for pts in (self.ts_portal, self.ts_hepatic, self.ts_ivc):
            if pts is not None and len(pts):
                return np.asarray(pts, float).mean(0)
        for pts in (self.ts_organs or {}).values():
            if pts is not None and len(pts):
                return np.asarray(pts, float).mean(0)
        lg = self.liver
        if isinstance(lg, dict):
            if lg.get("center") is not None:
                return np.asarray(lg["center"], float)
            if lg.get("surf") is not None and len(lg["surf"]):
                return np.asarray(lg["surf"], float).mean(0)
        if self._has_hep():                                  # 手描き管しか無いときはその重心を中心に
            arrs = [a for _k, a in self._hep_items() if len(a)]
            if arrs:
                return np.vstack(arrs).mean(0)
        return None

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

    def _draw_scope(self, p, c, s, b):
        """デバイス本体の描画（術式スタイルで分岐）。
        "ice": 明灰シャフト＋偏向で曲がるオレンジ先端＋青アレイ（AcuNav ICE）。
        "eus": 軟性内視鏡は *平面* 表示（先生「平面の方がわかりやすい」）＝暗色太線シャフト＋
               先端硬性部ポリゴン＋探触子（透明度75%程度・先生要望 2026-07-17）＋起上台針。"""
        if preset.PANE3D_SCOPE_STYLE == "eus":
            if self.show_route and self.route is not None and len(self.route) >= 2:   # 内視鏡の進む経路（薄い参照ライン）
                self._stroke(p, self.route, c, s, b, QColor(150, 200, 235, 120), 2)
            self._stroke(p, self.shaft, c, s, b, QColor(70, 78, 92), 11)          # 軟性シャフト（太い暗色線）
            self._stroke(p, self.shaft, c, s, b, QColor(150, 160, 175), 3)        # シャフトのハイライト
            if self.tip_outline is not None and len(self.tip_outline) > 2:        # 先端硬性部（まっすぐな筐体・平面）
                poly = QPolygonF(self._poly(self.tip_outline, c, s, b))
                p.setBrush(QColor(58, 66, 80)); p.setPen(QPen(QColor(150, 160, 175), 1.6)); p.drawPolygon(poly)
            if self.transducer is not None and len(self.transducer) > 2:          # 探触子（凸の出っ張り＝ビーム出射面）
                poly = QPolygonF(self._poly(self.transducer, c, s, b))
                p.setBrush(QColor(64, 184, 250, 64)); p.setPen(QPen(QColor(120, 214, 255, 170), 1.6)); p.drawPolygon(poly)
            if self.eus_needle is not None and len(self.eus_needle) >= 2:         # 穿刺針＝針の形状（細いシャフト＋尖ったベベル先端）
                w0 = self._proj(self.eus_needle[0], c, s, b); w1 = self._proj(self.eus_needle[-1], c, s, b)
                p.setPen(QPen(QColor(232, 238, 248), 2.6)); p.setBrush(Qt.NoBrush); p.drawLine(w0, w1)   # 金属シャフト
                p.setPen(QPen(QColor(150, 158, 172), 1.0)); p.drawLine(w0, w1)                            # 芯の陰影
                d = w1 - w0; ll = (d.x() ** 2 + d.y() ** 2) ** 0.5
                if ll > 1e-3:                                                     # 尖った針先の三角（ベベル）
                    ux, uy = d.x() / ll, d.y() / ll
                    base = w1 - QPointF(ux * 8, uy * 8)
                    t1 = base + QPointF(-uy * 2.4, ux * 2.4); t2 = base - QPointF(-uy * 2.4, ux * 2.4)
                    p.setBrush(QColor(240, 244, 252)); p.setPen(QPen(QColor(150, 158, 172), 1))
                    p.drawPolygon(QPolygonF([w1, t1, t2]))
                if self.eus_needle_exit is not None:                              # 出口（起上台）に小マーカー
                    p.setBrush(QColor(245, 177, 60)); p.setPen(QPen(Qt.white, 1))
                    p.drawEllipse(self._proj(self.eus_needle_exit, c, s, b), 2.6, 2.6)
            self._stroke(p, self.orange, c, s, b, QColor(245, 140, 50), 8)        # 旧・偏向先端（後方互換・通常はNone）
            self._stroke(p, self.arr, c, s, b, QColor(64, 184, 250), 5)           # 旧・アレイ（後方互換・通常はNone）
        else:
            self._stroke(p, self.shaft, c, s, b, QColor(204, 217, 230), 5)        # 灰シャフト
            self._stroke(p, self.orange, c, s, b, QColor(245, 140, 50), 8)        # 偏向で曲がる先端
            self._stroke(p, self.arr, c, s, b, QColor(64, 184, 250), 5)           # アレイ

    def paintEvent(self, _):
        p = QPainter(self); p.fillRect(self.rect(), QColor(7, 10, 14)); p.setPen(TERRA)
        surf_mode = self.show_body and self.body is not None
        hy = self.vesselBtn.geometry().bottom() + 16          # 操作説明はAIボタンの下へ（ボタンと重ならないように）
        p.drawText(8, hy, L("3D body surface — drag probe/body = move · empty-drag = rotate · middle / space-drag = pan · wheel = zoom · right-click = reset",
                            "3D体表 — プローブ/体表ドラッグ=移動 / 余白ドラッグ=回転 / 中ドラッグ・スペース+ドラッグ=画面移動 / ホイール=拡大縮小 / 右クリック=リセット")
                   if surf_mode else L("3D — drag = rotate · middle / space-drag = pan · wheel = zoom · right-click = reset",
                                       "3D — ドラッグ=回転 / 中ドラッグ・スペース+ドラッグ=画面移動 / ホイール=拡大縮小 / 右クリック=リセット"))
        have_anat = self._has_ts() or (self.show_liver and self.liver is not None) or self._has_hep()
        if not surf_mode and (not self.valid or self.apex is None) and not have_anat:
            p.setPen(QColor(140, 140, 140))
            p.drawText(10, self.height() // 2, L(*preset.PANE3D_EMPTY_HINT))
            p.setRenderHint(QPainter.Antialiasing, True); self._draw_orient_cube(p, self.rect())
            _frame(p, self, self.active); return
        p.setRenderHint(QPainter.Antialiasing, True)
        b = self.rect()
        if surf_mode:
            c, s = self._surf_frame(b); self._draw_body(p, c, s, b)   # 体表シェルを先に
        elif self.apex is not None:
            c = self.apex; s = min(b.width(), b.height()) / 210.0
        else:                                                # デバイスパス未設定でもAI解剖/肝ゴーストは重心中心で3D表示
            c = self._anat_center(); s = min(b.width(), b.height()) / 210.0
            if c is None:
                c = np.zeros(3, float)
        if self._has_ts():                                    # AIセグメントが有れば最背面に優先
            self._draw_ts(p, c, s, b)
        elif self.show_liver and self.liver is not None:      # 無ければ従来の軽量ゴースト
            self._draw_liver(p, c, s, b)
        if self.hep_manual:                                   # 手描きの管（旧: 肝静脈ローズ / 新: 種別ごと ORGAN_COLORS）
            for kind, vv in self._hep_items():
                if kind is None:
                    qcol = QColor(226, 110, 128)
                else:
                    import ts_seg
                    qcol = QColor(*ts_seg.ORGAN_COLORS.get(kind, (226, 110, 128)))
                if self.hep_drawing:                          # 描画中＝カクカクの直線＋節点（打った位置が見える）
                    if len(vv) >= 2:
                        self._stroke(p, vv, c, s, b, qcol, 4)
                    p.setBrush(qcol); p.setPen(QPen(Qt.white, 1))
                    for P in vv:
                        p.drawEllipse(self._proj(P, c, s, b), 2.6, 2.6)
                elif len(vv) >= 2:                            # 終了＝なだらかな管曲線・点なし
                    self._stroke(p, core.catmull_rom(vv), c, s, b, qcol, 5)
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
        self._draw_scope(p, c, s, b)                          # デバイス本体（術式スタイル分岐）
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
        elif self.aim_outline is not None and len(self.aim_outline) >= 3:   # 旧データ互換（entry/aim_tip未供給時）
            poly = QPolygonF(self._poly(self.aim_outline, c, s, b))
            p.setBrush(QColor(255, 250, 235, 130)); p.setPen(QPen(QColor(255, 250, 235), 1.5)); p.drawPolygon(poly)
        self._flow(p, self.needle_pred, c, s, b, QColor(255, 255, 255, 235), 3.2)   # 進行方向(流れるダッシュ)
        if self.aim_pred is not None and len(self.aim_pred) >= 2:         # 実際の針の想定2cm予測も流す
            self._flow(p, self.aim_pred, c, s, b, QColor(230, 230, 230, 190), 2.0)
        tgt = QColor(*preset.PANE3D_TARGET_COLOR)
        for pt, col in ((self.entry, GREENC), (self.target, tgt), (self.apex, tgt)):
            if pt is not None:
                w = self._proj(pt, c, s, b); p.setBrush(col); p.setPen(QPen(Qt.white, 1))
                p.drawEllipse(w, 4, 4)
        h = QColor(160, 160, 160); p.setPen(h)
        p.drawText(8, self.height() - 8, L(*preset.PANE3D_LEGEND))
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

    def _pan_mod(self, e):
        """CTと同じ: Space長押し or ⌥Option(Macのみ)＝パン操作（トラックパッドで中ボタンの代替）。"""
        alt = bool(e.modifiers() & Qt.AltModifier) and sys.platform == "darwin"
        return self._space or alt

    def mousePressEvent(self, e):
        self._last = e.position(); self._press = e.position(); self._moved = False
        self._press_panmod = self._pan_mod(e)                 # パン意図は押下時に確定（CTと同じ・atomic）
        self._moving_probe = (not self._press_panmod) and self._try_pick(e)   # パンでないときだけ経腹プローブ掴み

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        if (e.position() - getattr(self, "_press", e.position())).manhattanLength() > 2.5:
            self._moved = True
        if getattr(self, "_moving_probe", False):            # 掴んだら体表をなぞってプローブ移動（半径外でも追従・回転しない）
            self._try_pick(e, sticky=True); self._last = e.position(); return
        d = e.position() - self._last
        pan = ((e.buttons() & Qt.LeftButton) and self._press_panmod) or bool(e.buttons() & Qt.MiddleButton)
        if pan:                                              # CTと同じ: 中ドラッグ / Space・Option+ドラッグ=画像全体を平行移動
            self.pan3d = self.pan3d + d; self.setCursor(Qt.ClosedHandCursor)
            self._last = e.position(); self.update(); return
        if e.buttons() & Qt.LeftButton:                      # 掴んでいない左ドラッグ=視点回転
            self.az += d.x() * 0.5; self.el = max(-89, min(89, self.el + d.y() * 0.5))
            self._last = e.position(); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and not getattr(self, "_moved", False):   # 右クリック=ズーム/パンをリセット
            self.zoom3d = 1.0; self.pan3d = QPointF(0, 0)
        self._last = None; self._moving_probe = False; self._press_panmod = False
        self.setCursor(Qt.OpenHandCursor if self._space else Qt.ArrowCursor)
        self.update()                                        # 指を離した＝体表を高画質(超解像)で描き直す

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        self._zoom_at(float(np.exp(dy * 0.0016)), e.position(), self.rect())

    # ---- Space長押しパン（CTペインと同じ操作感）----
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
        # フォーカスが外れると Space の keyRelease が届かず固着する→防御的に解除（CTペインと同じ）
        self._space = False; self.setCursor(Qt.ArrowCursor); super().focusOutEvent(e)
