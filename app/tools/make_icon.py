"""TIPS Planner アプリアイコン生成（QPainterベクター描画）。
肝臓に Viatorr(TIPSステントグラフト) が門脈→肝静脈/IVC へ正しく留置された図。
出力: icon_1024.png （後段で sips/iconutil により .icns 化）。
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import PySide6  # noqa
os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                      os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins", "platforms"))
from PySide6.QtGui import (QImage, QPainter, QColor, QPen, QBrush, QPainterPath,
                           QLinearGradient, QRadialGradient)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
S = 1024
img = QImage(S, S, QImage.Format_ARGB32); img.fill(Qt.transparent)
p = QPainter(img); p.setRenderHint(QPainter.Antialiasing, True)


def P(x, y):
    return QPointF(x, y)


# ---- 背景: 角丸スクエア（ネイビー→ティールのグラデ） ----
bg = QPainterPath(); bg.addRoundedRect(QRectF(28, 28, S - 56, S - 56), 200, 200)
g = QLinearGradient(0, 0, 0, S)
g.setColorAt(0, QColor(22, 48, 74)); g.setColorAt(0.55, QColor(14, 34, 54)); g.setColorAt(1, QColor(9, 22, 38))
p.fillPath(bg, QBrush(g))
p.setPen(QPen(QColor(240, 143, 105, 150), 6)); p.drawPath(bg)   # テラコッタの細枠
p.setClipPath(bg)
# 上部の柔らかいハイライト
rg = QRadialGradient(P(380, 300), 620)
rg.setColorAt(0, QColor(255, 255, 255, 26)); rg.setColorAt(1, QColor(255, 255, 255, 0))
p.fillRect(QRectF(0, 0, S, S), QBrush(rg))

# ---- 血管（肝の後ろ：端が肝からはみ出る） ----
# 肝静脈/IVC（上・青）: ステント上端から上（心臓側）へ
hv = QPainterPath(); hv.moveTo(560, 360)
hv.cubicTo(560, 250, 590, 210, 600, 150)
p.setPen(QPen(QColor(70, 120, 200), 70, Qt.SolidLine, Qt.RoundCap)); p.drawPath(hv)
p.setPen(QPen(QColor(110, 165, 235), 40, Qt.SolidLine, Qt.RoundCap)); p.drawPath(hv)
# 門脈（下・紫）: ステント下端から下（門脈本幹）へ
pv = QPainterPath(); pv.moveTo(560, 680)
pv.cubicTo(560, 790, 520, 840, 470, 900)
p.setPen(QPen(QColor(120, 80, 160), 74, Qt.SolidLine, Qt.RoundCap)); p.drawPath(pv)
p.setPen(QPen(QColor(160, 120, 200), 44, Qt.SolidLine, Qt.RoundCap)); p.drawPath(pv)

# ---- 肝臓（大きめ・解剖的な輪郭） ----
liver = QPainterPath()
liver.moveTo(150, 470)
liver.cubicTo(240, 322, 560, 300, 770, 350)        # 広いドーム状の上縁
liver.cubicTo(862, 374, 918, 420, 900, 502)        # 右上の張り出し（横隔面）
liver.cubicTo(884, 580, 800, 628, 712, 650)        # 右下へ
liver.cubicTo(600, 678, 500, 716, 432, 712)        # 下縁（臓側面）
liver.cubicTo(408, 736, 372, 736, 356, 704)        # 胆嚢切痕の窪み
liver.cubicTo(300, 700, 232, 660, 196, 600)        # 左下（左葉先端へ）
liver.cubicTo(150, 556, 132, 520, 150, 470)        # 左へ閉じる
lg = QLinearGradient(230, 330, 700, 700)
lg.setColorAt(0, QColor(176, 78, 62)); lg.setColorAt(0.5, QColor(150, 58, 48)); lg.setColorAt(1, QColor(120, 44, 40))
p.fillPath(liver, QBrush(lg))
p.setPen(QPen(QColor(95, 32, 28), 5)); p.drawPath(liver)
# 肝のハイライト（上面）
p.save(); p.setClipPath(liver)
hg = QRadialGradient(P(420, 410), 360)
hg.setColorAt(0, QColor(255, 200, 170, 70)); hg.setColorAt(1, QColor(255, 200, 170, 0))
p.fillRect(QRectF(0, 0, S, S), QBrush(hg))
p.restore()

# ---- Viatorr ステント（門脈→肝静脈の TIPS 短絡） ----
stent = QPainterPath(); stent.moveTo(560, 680); stent.cubicTo(548, 560, 548, 470, 560, 360)
# 外殻（被覆部=銀）
p.setPen(QPen(QColor(40, 40, 48), 56, Qt.SolidLine, Qt.RoundCap)); p.drawPath(stent)
sg = QPen(QColor(210, 216, 224), 48, Qt.SolidLine, Qt.RoundCap)
p.setPen(sg); p.drawPath(stent)
# 内腔の陰影＋ハイライト
p.setPen(QPen(QColor(150, 158, 168), 30, Qt.SolidLine, Qt.RoundCap)); p.drawPath(stent)
p.setPen(QPen(QColor(245, 248, 252), 10, Qt.SolidLine, Qt.RoundCap)); p.drawPath(stent)
# 被覆部のダイヤモンドメッシュ（上側＝covered stent）
p.save()
clip = QPainterPath(); clip.addRect(QRectF(534, 345, 52, 245)); p.setClipPath(clip)
p.setPen(QPen(QColor(120, 128, 140, 170), 3))
for yy in range(350, 600, 22):                       # ＼ と ／ を交差させた菱目
    p.drawLine(P(534, yy), P(586, yy + 22))
    p.drawLine(P(586, yy), P(534, yy + 22))
p.restore()
# 門脈側ベア部（下端）= 露出ワイヤ＋軽いフレア
bare = QPainterPath(); bare.moveTo(560, 600); bare.lineTo(560, 686)
p.setPen(QPen(QColor(228, 231, 236), 46, Qt.SolidLine, Qt.RoundCap)); p.drawPath(bare)
p.save(); bc = QPainterPath(); bc.addRect(QRectF(534, 598, 52, 96)); p.setClipPath(bc)
p.setPen(QPen(QColor(95, 100, 112, 200), 4))
for yy in range(600, 690, 16):
    p.drawLine(P(536, yy), P(584, yy + 16)); p.drawLine(P(584, yy), P(536, yy + 16))
p.restore()
# 両端の開口リング
for cy, col in ((360, QColor(150, 195, 240)), (685, QColor(180, 140, 215))):
    p.setBrush(Qt.NoBrush); p.setPen(QPen(col, 6)); p.drawEllipse(P(560, cy), 24, 12)

p.end()
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon_1024.png")
img.save(out)
print("saved", out)
