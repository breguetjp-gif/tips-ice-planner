"""tips_core スモークテスト（合成ボリューム・患者データ不要・numpyのみで完結）。"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tips_core as core


def _synthetic():
    nz, H, W = 60, 128, 128
    vol = np.full((nz, H, W), -1000.0, np.float32)     # 空気
    zz, yy, xx = np.mgrid[0:nz, 0:H, 0:W]
    blob = (xx - 64) ** 2 + (yy - 64) ** 2 + (zz - 30) ** 2 < 25 ** 2
    vol[blob] = 50.0                                    # 軟部
    return vol, 0.7, 0.7, 1.0


def test_ortho():
    vol, sx, sy, dz = _synthetic()
    for plane, idx in ((0, 30), (1, 64), (2, 64)):
        img, w, h = core.ortho_image(vol, sx, sy, dz, plane, idx)
        assert img.dtype == np.uint8 and img.ndim == 2 and w > 0 and h > 0


def test_ice_and_geom():
    vol, sx, sy, dz = _synthetic()
    path = [[10, 90, 70], [30, 64, 64], [50, 40, 58]]   # [z,y,x]
    g = core.ice_geometry(path, 50, 180, 0, 0, sx, sy, dz)
    assert g is not None
    for k in ("Tp", "S", "Vp", "Sp"):
        assert np.all(np.isfinite(g[k]))
    assert abs(np.linalg.norm(g["Vp"]) - 1) < 1e-6
    out = core.ice_image(vol, sx, sy, dz, g, 40, 400, flip=False)
    assert out is not None
    im, pw, ph = out
    assert im.dtype == np.uint8 and im.ndim == 2 and pw > 0 and ph > 0


def test_deflection():
    """ICE偏向の物理: 頂点が動く / 側射(ビーム⊥接線) / L/Rがθ回転と別物。"""
    vol, sx, sy, dz = _synthetic()
    path = [[10, 90, 70], [30, 64, 64], [50, 40, 58]]
    g0 = core.ice_geometry(path, 30, 40, 0, 0, sx, sy, dz)
    gAP = core.ice_geometry(path, 30, 40, 40, 0, sx, sy, dz)
    gLR = core.ice_geometry(path, 30, 40, 0, 40, sx, sy, dz)
    # 偏向で頂点が動く（旧バグ=頂点固定 を防ぐ）
    assert np.linalg.norm(gAP["Tp"] - g0["Tp"]) > 2.0, "A/P must move the apex"
    assert np.linalg.norm(gLR["Tp"] - g0["Tp"]) > 2.0, "L/R must move the apex"
    # 側射: ビーム ⊥ 扇接線
    for g in (g0, gAP, gLR):
        assert abs(float(g["Vp"] @ g["Sp"])) < 1e-6, "beam must be perpendicular to fan axis (side-firing)"
    # L/R(θ=40,b2=40) は θ=80 の純回転とは別物（旧バグ=L/RがただのθでΞ を防ぐ）
    gTheta = core.ice_geometry(path, 30, 80, 0, 0, sx, sy, dz)
    assert np.linalg.norm(gLR["Tp"] - gTheta["Tp"]) > 2.0, "L/R must differ from a pure theta rotation"
    # A/P と L/R の頂点変位は概ね直交（独立した2軸偏向）
    dAP = core.nrm(gAP["Tp"] - g0["Tp"]); dLR = core.nrm(gLR["Tp"] - g0["Tp"])
    assert abs(float(dAP @ dLR)) < 0.3, "A/P and L/R should move the apex in ~orthogonal directions"


def test_bend_monotonic():
    vol, sx, sy, dz = _synthetic()
    path = [[10, 90, 70], [30, 64, 64], [50, 40, 58]]
    prev = -1
    for ang in (0, 20, 40, 60, 80):
        b = core.bend_tip(path, 50, 180, ang, 0, sx, sy, dz, tip_high_z=True)
        d = float(np.linalg.norm(b["Tp"] - b["apOn"]))   # 偏向前apexからの先端変位
        assert d >= prev - 1e-6, f"bend not monotonic at {ang} (d={d:.2f})"
        prev = d
    # 偏向ゼロは先端=apex（変位ゼロ）
    b0 = core.bend_tip(path, 50, 180, 0, 0, sx, sy, dz)
    assert float(np.linalg.norm(b0["Tp"] - b0["apOn"])) < 1e-6
    # 連続性: 灰↔橙が支点Fで接続
    bb = core.bend_tip(path, 50, 180, 50, 30, sx, sy, dz)
    assert float(np.linalg.norm(bb["orange"][0] - bb["F"])) < 1e-6


def test_needle():
    straight = core.needle_path([0, 0, 0], [0, 0, 50], 0)
    assert straight is not None and straight.shape[1] == 3
    # 直線: 始終点を結ぶ
    assert np.linalg.norm(straight[0] - np.array([0, 0, 0])) < 1e-6
    assert np.linalg.norm(straight[-1] - np.array([0, 0, 50])) < 1e-6
    arc = core.needle_path([0, 0, 0], [0, 0, 50], 20)
    assert arc is not None
    assert np.linalg.norm(arc[0]) < 1e-6 and np.linalg.norm(arc[-1] - np.array([0, 0, 50])) < 1e-3


def test_needle3():
    axis = np.array([0., 0, 0]); entry = np.array([0., 0, 30]); target = np.array([0., 20, 60])
    # RUPS=軸延長(axis→entry=+z)へ直進。targetへは向かわず軸方向に伸びる
    rups = core.needle_path3(axis, entry, target, straight=True)
    assert rups is not None and np.allclose(rups[0], entry)
    assert np.allclose(core.nrm(rups[1] - rups[0]), [0, 0, 1])
    # Colapinto=entry発・初期接線=軸延長(+z)・targetを通る円弧
    cola = core.needle_path3(axis, entry, target, straight=False)
    assert cola is not None and np.allclose(cola[0], entry) and np.linalg.norm(cola[-1] - target) < 1e-3
    t0 = core.nrm(cola[1] - cola[0])                      # 出発時の接線は軸延長(+z)に一致
    assert np.allclose(t0, [0, 0, 1], atol=0.05)
    # 軸未設定（axis=entry）なら entry→target を初期接線にする（後方互換）
    cola2 = core.needle_path3(entry, entry, target, straight=False)
    assert cola2 is not None and np.linalg.norm(cola2[-1] - target) < 1e-3


def test_needle_assembly():
    entry = np.array([0., 0, 30]); target = np.array([-20., 10, 15])
    asm = core.needle_assembly(entry, target, cannula_len=60.0, fillet=9.0)
    # 外筒: entryから頭側(+z)へ立つ直棒。上端は entry+60*up
    assert asm["cannula"].shape == (2, 3)
    assert np.allclose(asm["cannula"][0], entry + np.array([0, 0, 60]))
    assert np.allclose(core.nrm(asm["cannula"][0] - asm["cannula"][1]), [0, 0, 1], atol=1e-6)
    # blade: 連続ポリラインで、末端は必ず target に到達
    assert asm["blade"] is not None and asm["blade"].shape[1] == 3
    assert np.linalg.norm(asm["blade"][-1] - target) < 1e-6
    # 接続部で『かくかく』しない＝隣接セグメントの角度がどこも緩い（>150度）
    b = asm["blade"]
    for i in range(1, len(b) - 1):
        u = core.nrm(b[i] - b[i - 1]); v = core.nrm(b[i + 1] - b[i])
        assert float(u @ v) > np.cos(np.radians(30)), "junction must be smooth (no sharp corner)"
    # body(太い芯線)は連続で、末端はベベル手前（targetより手前）
    assert asm["body"] is not None and asm["body"].shape[1] == 3
    assert np.linalg.norm(asm["body"][-1] - target) < np.linalg.norm(asm["body"][-1] - entry)
    # 進行方向pred: 針先から entry→target 方向へ伸びる
    assert asm["pred"].shape == (2, 3)
    assert np.linalg.norm(asm["pred"][0] - target) < 1e-6
    assert np.allclose(core.nrm(asm["pred"][1] - asm["pred"][0]), core.nrm(target - entry), atol=1e-6)
    # 金属ベベル先端(tip): 三角形で先端点(target)を含む＝尖った切っ先
    assert asm["tip"] is not None and asm["tip"].shape == (3, 3)
    assert min(np.linalg.norm(pt - target) for pt in asm["tip"]) < 1e-6
    # Target未設定なら外筒のみ（body/tip/pred=None）
    only = core.needle_assembly(entry, None)
    assert only["body"] is None and only["tip"] is None and only["pred"] is None
    assert only["cannula"].shape == (2, 3)


def test_rups_colapinto():
    D = np.array([0., 0, 30]); t0 = np.array([0., 0, -1.0])      # 肝静脈で足側へ向かう
    target = np.array([-20., 0, 10])                            # 門脈(目印)
    r = core.rups_path(D, t0, target, throw=80)
    assert r["cannula"] is not None and r["needle"].shape == (2, 3)
    assert np.allclose(r["needle"][0], D)                       # 針はDから出る
    # RUPS=直進: 針の2点を結ぶ直線
    seg = r["needle"][1] - r["needle"][0]
    assert abs(np.linalg.norm(np.cross(seg, core.nrm(seg)))) < 1e-6
    # カニューラ弧の先端＝D（針の起点に連続）
    assert np.linalg.norm(r["cannula"][-1] - D) < 1e-6
    assert r["miss"] >= 0
    c = core.colapinto_path(D, t0, target, R_c=55, s_adv=90)
    assert c["cannula"] is None and c["needle"].shape[0] >= 3
    assert np.linalg.norm(c["needle"][0] - D) < 1e-6            # 弧はDから
    # Colapinto=曲がる: 弧長が弦長より長い（直線でない）
    chord = np.linalg.norm(c["needle"][-1] - c["needle"][0])
    arclen = sum(np.linalg.norm(c["needle"][i + 1] - c["needle"][i]) for i in range(len(c["needle"]) - 1))
    assert arclen > chord * 1.02, "Colapinto needle must curve (arc > chord)"
    # 初期接線が t0 に一致（肝静脈で接線連続）
    assert np.allclose(core.nrm(c["needle"][1] - c["needle"][0]), core.nrm(t0), atol=0.05)
    # 曲率Rが小さいほど鋭く曲がる（弦長が短くなる＝強く湾曲）
    sharp = core.colapinto_path(D, t0, target, R_c=30, s_adv=90)
    gentle = core.colapinto_path(D, t0, target, R_c=80, s_adv=90)
    chord_s = np.linalg.norm(sharp["needle"][-1] - sharp["needle"][0])
    chord_g = np.linalg.norm(gentle["needle"][-1] - gentle["needle"][0])
    assert chord_s < chord_g, "smaller R_c must curve more sharply"
    # path_tangent: IVCパス接線が3D単位ベクトル
    t = core.path_tangent([[10, 90, 70], [30, 64, 64], [50, 40, 58]], 30.0, 0.7, 0.7, 1.0)
    assert t is not None and abs(np.linalg.norm(t) - 1.0) < 1e-6 and abs(t[2]) > 0.1


def test_predict():
    p0 = np.array([0., 10, 10]); p1 = np.array([0., 6, 8])   # 進行＝前方(-y)＋足側(-z)（非退化）
    u = core.nrm(p1 - p0)
    ray = core.predict_straight(p0, p1, length=50)
    assert ray.shape[1] == 3 and np.allclose(core.nrm(ray[1] - ray[0]), u)
    # Colapinto＝頭側(+z)へ deflect → 直進より高z（頭側へ曲がる）
    arc = core.predict_curve(p0, p1, radius=55, span_deg=60)
    assert arc.shape[1] == 3 and arc[-1][2] > (p1 + 50 * u)[2]
    # readout: 標的が頭側(+z)側なら side>0、足側なら<0
    assert core.predict_readout(p0, p1, p1 + np.array([0, 0, 20.]))["side"] > 0
    assert core.predict_readout(p0, p1, p1 + np.array([0, 0, -20.]))["side"] < 0
    # 円フィット：既知半径40の円弧 → 近い半径
    th = np.linspace(0, 1.0, 6); pts = np.c_[40 * np.cos(th), 40 * np.sin(th), np.zeros(6)]
    R = core.fit_circle_radius(pts)
    assert R is not None and abs(R - 40) < 2


def test_liver():
    from tips_core import liver
    nz, H, W = 40, 80, 80
    vol = np.full((nz, H, W), -200.0, np.float32)            # 背景=空気/脂肪相当
    zz, yy, xx = np.ogrid[:nz, :H, :W]
    blob = ((zz - 20) ** 2 + (yy - 40) ** 2 / 4.0 + (xx - 34) ** 2 / 4.0) <= 100   # 楕円体=肝実質
    vol[blob] = 110.0
    vol[18:23, 38:42, 30:34] = 190.0                         # 内部の造影血管（穴埋め対象）
    path = [[14, 40, 34], [20, 40, 34], [26, 40, 34]]        # [z,y,x] 肝内を貫くIVCパス
    L = liver.estimate(vol, path, 1.0, 1.0, 1.0)
    assert L is not None and L["surf"].shape[1] == 3 and len(L["interior"]) > 0
    assert L["liters"] > 0
    apex = np.array([34.0, 40.0, 20.0])                      # mm=(x,y,z)（Pane3Dのapex相当）
    for mode in ("haze", "surface"):
        rgba = liver.render_ghost(L, -60, 18, apex, 2.0, 200, 180, mode=mode, opacity=0.5)
        assert rgba is not None and rgba.shape == (180, 200, 4) and rgba.dtype == np.uint8
        assert int(rgba[..., 3].max()) > 0, f"{mode} ghost must produce visible pixels"
    Lf = liver.estimate(vol, None, 1.0, 1.0, 1.0)            # パス無し=pathfreeへフォールバック
    assert Lf is None or "surf" in Lf
    assert liver.render_ghost(None, 0, 0, apex, 2.0, 100, 100) is None   # データ無し=None
    print("✅ liver estimate/render ok  (L=%.2f surf=%d)" % (L["liters"], len(L["surf"])))


def test_body_surface_shell():
    """体表シェル: ①CT寝台を拾わない ②撮影範囲の端（上下の切断面）を面として閉じる
       ③点がボクセル格子に量子化されていない（サブボクセル）④法線が mm 空間で正しい向き。

    ①②を落とすと 3D は「中が丸見えの筒＋宙に浮いた板」になり、③を落とすと腹壁に等高線状の
    縞が出る。④は体表を掴んだときのプローブの向き（体内向き法線）に直接効く。
    """
    from tips_core import liver
    nz, H, W = 30, 90, 90
    sx = sy = 1.0; dz = 3.0                                  # 面内1mm / スライス3mm＝異方性
    vol = np.full((nz, H, W), -1000.0, np.float32)           # 空気
    zz, yy, xx = np.ogrid[:nz, :H, :W]
    r = np.sqrt((yy - 40) ** 2 + (xx - 45) ** 2)             # z方向に一様な太い円柱＝体幹
    vol[np.broadcast_to(r <= 28, vol.shape)] = 40.0
    vol[:, 74:77, 20:70] = 60.0                              # 体幹から離れた薄い板＝CT寝台

    b = liver.body_surface(vol, sx, sy, dz, ds=(1, 1, 1))
    assert b is not None and len(b["surf"]) > 500
    pts = b["surf"]

    # ① 寝台(y≈75mm)の点を拾っていない。体幹の背側の縁は y=68mm までしか無い
    assert int((pts[:, 1] > 71.0).sum()) == 0, "CT寝台が体表シェルに混入している"

    # ② 撮影範囲の端が「面」として閉じている。円柱の外周(r=28mm)ではなく、切断面の **内側**
    #    （r<20mm）に点が要る。外周リングだけなら筒のままで、3Dで中が丸見えになる
    zmm = pts[:, 2]
    rr = np.hypot(pts[:, 0] - 45.0, pts[:, 1] - 40.0)
    for lab, sel in (("先頭", zmm < dz * 0.6), ("末尾", zmm > (nz - 1) * dz - dz * 0.6)):
        assert int((sel & (rr < 20.0)).sum()) > 50, f"{lab}スライスの切断面が閉じていない（筒抜け）"

    # ③ サブボクセル: 点が格子(1mm)にぴったり乗っていない＝深度が階段にならない
    frac = np.abs(pts[:, 0] / sx - np.round(pts[:, 0] / sx))
    assert float(frac.max()) > 0.05, "表面点がボクセル格子のまま＝深度が階段になり縞が出る"

    # 描画: 内側に穴が空かないこと（点群のまま撒くと虫食いになる）
    c = b["center"]; scale = 200.0 / (b["extent"] * 1.2)
    rgba = liver.render_ghost(b, 0.0, -75.0, c, scale, 200, 200, mode="surface", opacity=0.97)
    a = rgba[..., 3]
    op = a > 200
    lr = np.maximum.accumulate(op, 1) & np.maximum.accumulate(op[:, ::-1], 1)[:, ::-1]
    ud = np.maximum.accumulate(op, 0) & np.maximum.accumulate(op[::-1], 0)[::-1]
    holes = int(((a < 40) & lr & ud).sum())
    assert op.sum() > 2000 and holes == 0, f"体表の面に穴がある ({holes}px)"

    # ④ 法線は **mm 空間** の外向き単位ベクトル。異方性ボクセル(面内1mm/スライス3mm)で
    #    index 空間の勾配のまま出すと z 成分が3倍に効き、斜めの面で向きが狂う。
    #    体表を掴んだときのプローブの向き（体内向き法線）がこれなので、狂うと撮像面が傾く。
    sph = np.full((nz, H, W), -1000.0, np.float32)
    cz, cy, cx = 19.5, 42.0, 45.0                            # index / mm 中心 = (45,42,58.5)mm
    dmm = np.sqrt(((zz - cz) * dz) ** 2 + ((yy - cy) * sy) ** 2 + ((xx - cx) * sx) ** 2)
    sph[dmm <= 30.0] = 40.0                                  # 半径30mm の真球（mm空間で等方）
    bs = liver.body_surface(sph, sx, sy, dz, ds=(1, 1, 1))
    P, N = bs["surf"], bs["nrm"]
    radial = P - np.array([cx * sx, cy * sy, cz * dz], np.float32)
    radial /= np.linalg.norm(radial, axis=1, keepdims=True)
    dot = np.einsum("ij,ij->i", N, radial)
    assert float(np.median(dot)) > 0.97, \
        f"球の法線が半径方向を向いていない (median={np.median(dot):.3f}) — 勾配を mm に直していない"
    print("✅ body surface ok  (pts=%d spacing=%.1fmm 穴=0 法線dot=%.3f)"
          % (len(pts), b["spacing"], float(np.median(dot))))


def test_features():
    """新機能の核: aim_readout(方位/距離) / surface_geometry(凸扇) / snap_to_skin / ice_image r0後方互換。"""
    # --- aim_readout: 標準axial orient（恒等 index→LPS: +x=L,+y=背側,+z=頭側）---
    orient = np.eye(3).tolist()
    tip = np.array([10.0, 10.0, 10.0])
    r = core.aim_readout(tip, tip + np.array([0, 0, 20.0]), orient)        # +z=頭側20
    assert r["has_orient"] and abs(r["dist"] - 20) < 1e-6
    d = dict(r["comps"]); assert "頭側" in d and abs(d["頭側"] - 20) < 1e-6
    r2 = core.aim_readout(tip, tip + np.array([0, -15.0, 0]), orient)      # -y=腹側15
    assert abs(dict(r2["comps"])["腹側"] - 15) < 1e-6
    flip = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]                            # 左右反転＋頭尾反転
    d3 = dict(core.aim_readout(tip, tip + np.array([5.0, 0, 8.0]), flip)["comps"])
    assert abs(d3["右"] - 5) < 1e-6 and abs(d3["尾側"] - 8) < 1e-6        # +x→右, +z→尾側
    rn = core.aim_readout(tip, tip + np.array([3, 4, 0.0]), None)         # orient無し=距離のみ
    assert rn["has_orient"] is False and abs(rn["dist"] - 5) < 1e-6 and rn["comps"] == []
    # --- surface_geometry: 正規直交フレーム + 凸 r0/depth ---
    g = core.surface_geometry([100., 100., 50.], [0., 1., 0.], 0, 0, 0, 0.7, 0.7, 1.0)
    Vp = np.array(g["Vp"]); Sp = np.array(g["Sp"])
    assert abs(np.linalg.norm(Vp) - 1) < 1e-6 and abs(np.linalg.norm(Sp) - 1) < 1e-6 and abs(Vp @ Sp) < 1e-6
    assert abs(g["R"] - (core.CONVEX_R0 + core.CONVEX_DEPTH)) < 1e-6 and g["r0"] == core.CONVEX_R0
    g2 = core.surface_geometry([100., 100., 50.], [0., 1., 0.], 0, 20, 0, 0.7, 0.7, 1.0)
    assert np.linalg.norm(np.array(g2["Vp"]) - Vp) > 1e-3, "tilt must change beam direction"
    # plane_axis: 扇の側方Spが指定面内（Coronal=y法線→Spはy成分ゼロ）
    gc = core.surface_geometry([100., 100., 50.], [0., 0., 1.0], 0, 0, 0, 0.7, 0.7, 1.0, plane_axis=(0, 1, 0))
    assert abs(np.array(gc["Sp"])[1]) < 1e-6, "coronal placement: lateral stays in the coronal plane"
    # probe_glyph: コンベックス外形（凸面の中央≈接触点／筐体込みで点数十分）＋実機風の詳細(青アレイ帯/ボタン)
    gl = core.probe_glyph(g)
    assert gl["face"].shape[1] == 3 and gl["outline"].shape[1] == 3 and len(gl["outline"]) >= 10
    assert np.linalg.norm(gl["face"][len(gl["face"]) // 2] - np.array(g["Tp"])) < 5.0, "face center ≈ contact"
    assert gl["array"].shape[1] == 3 and len(gl["array"]) == 2 * len(gl["face"]), "アレイ帯=凸面弧＋内側弧の閉じた帯"
    assert np.asarray(gl["button"]).shape == (3,), "操作ボタンは1つのworld点"
    # ボタン・筐体頂部は体外側(-Vp)＝接触点よりビーム逆方向にある
    assert (np.asarray(gl["button"]) - np.array(g["Tp"])) @ np.array(g["Vp"]) < 0, "ボタンは筐体(体外側)にある"
    # needle_glyph: 実際の針の外形（シャフト+先端のテーパー、5点の閉ポリゴン、先端=tip）
    ngl = core.needle_glyph([0., 0., 0.], [0., 0., 30.], width=2.0, taper=6.0)
    assert ngl["outline"].shape == (5, 3) and np.allclose(ngl["tip"], [0., 0., 30.])
    assert np.linalg.norm(ngl["outline"][2] - np.array([0., 0., 30.])) < 1e-6, "middle vertex is the sharp tip"
    _w = np.linalg.norm(ngl["outline"][0] - ngl["outline"][4])                # シャフト側の全幅
    assert abs(_w - 2.0) < 1e-6
    ngl0 = core.needle_glyph([5., 5., 5.], [5., 5., 5.], width=2.0)           # p1==tip（退化）でも例外にならない
    assert ngl0["outline"].shape[0] == 3
    # body_surface: 合成ボリュームから外郭シェル（3D体表用）
    from tips_core import liver as _lv
    bvol = np.full((30, 64, 64), -1000.0, np.float32); bvol[:, 16:48, 16:48] = 50.0
    bs = _lv.body_surface(bvol, 1.0, 1.0, 1.0)
    assert bs is not None and bs["surf"].shape[1] == 3 and bs["extent"] > 0 and len(bs["nrm"]) == len(bs["surf"])
    # ice_coplanarity: 同一断面(面外~0) / 面外あり(best_offが現状より小)
    gp = dict(Tp=[0, 0, 0], Sp=[0, 0, 1.0], Vp=[1, 0, 0.0])
    ip = core.ice_coplanarity(gp, [3, 0, 2], [2, 0, -1])              # y=0 → 同一断面
    assert abs(ip["off_entry"]) < 1e-6 and abs(ip["off_target"]) < 1e-6
    op = core.ice_coplanarity(gp, [3, 0, 2], [2, 5, -1])             # target y=5 → 面外
    assert abs(op["off_target"]) > 4.0 and op["best_off"] < max(abs(op["off_entry"]), abs(op["off_target"]))
    # best_surface_theta: 経腹プローブ設置時に Entry/Target/接触点の3点が乗る断面(θ)を返す
    C = np.array([100.0, 50.0, 80.0]); n0 = np.array([0.0, 1.0, 0.0])   # 体内向き法線
    E = C + np.array([10.0, 20.0, 0.0]); T = C + np.array([-8.0, 40.0, 0.0])  # z=一定→ある面内
    th = core.best_surface_theta(C, n0, E, T, plane_axis=(0, 0, 1))
    g_s = core.surface_geometry(C, n0, th, 0, 0, 1, 1, 1, plane_axis=(0, 0, 1))
    w = np.cross(g_s["Vp"], g_s["Sp"]); w = w / np.linalg.norm(w)       # 扇平面の法線
    assert abs((E - C) @ w) < 0.5 and abs((T - C) @ w) < 0.5, "3点が同一断面に乗るθを返す"
    # ねじれ配置でも最小の面外ズレ(minimax)を返す＝2点の面外距離がほぼ均衡
    T2 = C + np.array([-8.0, 40.0, 50.0]); th2 = core.best_surface_theta(C, n0, E, T2, plane_axis=(0, 0, 1))
    g2 = core.surface_geometry(C, n0, th2, 0, 0, 1, 1, 1, plane_axis=(0, 0, 1))
    w2 = np.cross(g2["Vp"], g2["Sp"]); w2 = w2 / np.linalg.norm(w2)
    assert abs(abs((E - C) @ w2) - abs((T2 - C) @ w2)) < 1.0, "minimaxで2点の面外ズレが均衡"
    # --- ice_image: 凸geom と r0=0(後方互換) の両方で描ける ---
    vol = np.full((40, 128, 128), -1000.0, np.float32); vol[:, 40:90, 40:90] = 40.0
    out = core.ice_image(vol, 0.7, 0.7, 1.0, g, 40, 400)
    assert out is not None and out[0].dtype == np.uint8 and out[0].ndim == 2
    g0 = dict(g); g0["r0"] = 0.0; g0["R"] = 85.0
    assert core.ice_image(vol, 0.7, 0.7, 1.0, g0, 40, 400) is not None
    # --- snap_to_skin: 合成円板。体外クリック→皮膚へ吸着、内向き法線は重心向き ---
    sl = np.full((128, 128), -1000.0, np.float32)
    yy, xx = np.ogrid[:128, :128]; sl[(xx - 64) ** 2 + (yy - 64) ** 2 <= 40 ** 2] = 30.0
    (cc, rr), (nx, ny) = core.snap_to_skin(sl, 64, 5, 0.7, 0.7)           # 上の体外からクリック
    assert sl[int(round(rr)), int(round(cc))] > -300, "contact must land on tissue (skin)"
    assert ny > 0 and abs(nx) < 0.3, "inward normal points toward centroid (downward here)"
    print("✅ features ok  (aim_readout / surface_geometry / snap_to_skin / ice_image r0)")


def test_updater():
    """自己更新: バージョン比較・ローカル配布フォルダ検出・新bundleの用意（実I/O・一時dir隔離）。"""
    import tempfile, json, zipfile, updater
    assert updater.ver_tuple("0.4.10") > updater.ver_tuple("0.4.9"), "数値比較（文字列比較でない）"
    assert updater.ver_tuple("1.0.0") > updater.ver_tuple("0.9.9")
    tmp = tempfile.mkdtemp(prefix="tips_upd_")
    dist = os.path.join(tmp, "TIPS ICE Planner 配布"); os.makedirs(dist)
    _kit = "TIPS ICE Planner (Mac)/" + updater.APP_NAME                  # build_release のzip構造を模す
    with zipfile.ZipFile(os.path.join(dist, updater.DIST_ZIP), "w") as z:
        z.writestr(_kit + "/Contents/MacOS/TIPS ICE Planner", "x")       # ダミー実体
        z.writestr(_kit + "/Contents/Info.plist", "<plist/>")
    json.dump({"version": "9.9.9", "notes": "test", "url": ""},
              open(os.path.join(dist, "version.json"), "w"))
    _saved = updater.LOCAL_DIRS
    try:
        updater.LOCAL_DIRS = [dist]
        info = updater.find_update("0.4.5", update_url=None)          # ローカルに新版あり
        assert info and info["version"] == "9.9.9" and info["source"] == "local", info
        assert updater.find_update("9.9.9", update_url=None) is None, "同版以上なら更新なし"
        staged = updater.stage_new_bundle(info)                       # 新bundleを一時領域へ用意
        assert os.path.isdir(staged) and os.path.exists(
            os.path.join(staged, "Contents", "MacOS", "TIPS ICE Planner")), "staged app must be a full bundle copy"
    finally:
        updater.LOCAL_DIRS = _saved
    assert updater.current_app_bundle() is None, "ソース実行時は .app 外＝自己置換しない"
    print("✅ updater ok  (version compare / local detect / stage bundle)")


def test_updater_apply_and_relaunch():
    """自己更新の実置換（先生報告「OKを押してもバージョンが変わらない」の回帰防止）。
    旧: `set -e` + `[ -d "$TGT" ] && mv ...` が、条件を満たさないとログも残さず無言で
    スクリプト全体を中断していた。実際にbashスクリプトを走らせ、置換が完了しログが残ることを検証する。
    `open` は本物のFinderを起動させないよう偽コマンドに差し替える。"""
    import time
    import tempfile as _tempfile
    import subprocess as _subprocess
    from unittest.mock import patch as _patch_upd
    import updater
    import catalog as catmod

    work = _tempfile.mkdtemp(prefix="tips_swap_test_")
    data_dir = os.path.join(work, "appdata"); os.makedirs(data_dir)

    old_app = os.path.join(work, "TIPS ICE Planner.app")
    os.makedirs(os.path.join(old_app, "Contents", "MacOS"))
    open(os.path.join(old_app, "Contents", "MacOS", "MARKER_OLD"), "w").close()
    new_app = os.path.join(work, "staged", "TIPS ICE Planner.app")
    os.makedirs(os.path.join(new_app, "Contents", "MacOS"))
    open(os.path.join(new_app, "Contents", "MacOS", "MARKER_NEW"), "w").close()

    fakebin = os.path.join(work, "fakebin"); os.makedirs(fakebin)
    fake_open = os.path.join(fakebin, "open")
    with open(fake_open, "w") as f:
        f.write("#!/bin/bash\necho \"fake-open $*\" >&2\nexit 0\n")
    os.chmod(fake_open, 0o755)

    dummy = _subprocess.Popen(["/usr/bin/true"]); dummy.wait()   # 既に終了済みのpid＝待ち時間ゼロで即swap開始
    dead_pid = dummy.pid

    with _patch_upd.object(catmod, "app_data_dir",
                          lambda: (os.makedirs(os.path.join(data_dir, "thumbs"), exist_ok=True) or data_dir)), \
         _patch_upd.object(os, "getpid", return_value=dead_pid):
        old_path_env = os.environ.get("PATH", "")
        os.environ["PATH"] = fakebin + os.pathsep + old_path_env
        try:
            updater.apply_update_and_relaunch(new_app, old_app)
        finally:
            os.environ["PATH"] = old_path_env

    # 完了の待ち条件は「ヘルパーが最後に書くログ行」にする。swapスクリプトの順序は
    # 新bundle配置(MARKER_NEW出現) → .old削除 → ログ"OK"出力 なので、MARKER_NEWだけを
    # 待つと .old 削除前に検証が走るレースになる（高負荷時に flaky）。ログ"OK"出現＝全工程完了。
    marker_new = os.path.join(old_app, "Contents", "MacOS", "MARKER_NEW")
    log_path = os.path.join(data_dir, "logs", "update.log")   # patch解除後もパスを直接組み立てて検証
    log_text = ""
    for _ in range(200):                                      # バックグラウンドスクリプトの完了を待つ(最大10秒)
        if os.path.exists(log_path):
            log_text = open(log_path, encoding="utf-8").read()
            if "OK: swapped to new version" in log_text:
                break
        time.sleep(0.05)
    assert os.path.exists(marker_new), "target app must contain the NEW marker after the swap actually runs"
    assert not os.path.exists(old_app + ".old"), "backup dir must be cleaned up after a successful swap"
    assert "OK: swapped to new version" in log_text, f"swap must log success; log was:\n{log_text}"
    print("✅ updater apply_and_relaunch ok  (real swap script, logged, no silent abort)")


def test_updater_windows():
    """自己更新のWindows分岐（先生報告「Windowsは自動アップデーターが機能していない」の修正）。
    旧: current_app_bundle()がWindowsでは常にNoneを返し（.app前提のmac専用ロジック）、
    find_updateもMAC_ZIP固定でWindowsの配布zipを見つけられなかった＝自己更新が原理的に不可能だった。
    sys.platform/frozenをこの場で偽装し、Windows分岐のパス選択・zip展開ロジックを実I/Oで検証する。"""
    import tempfile, json, zipfile, importlib
    import updater

    try:
        updater.IS_WIN = True
        updater.APP_NAME = "TIPS ICE Planner.exe"
        updater.DIST_ZIP = "TIPS-ICE-Planner-Windows.zip"

        # current_app_bundle: 凍結(frozen)でなければNone、frozenならexeの親フォルダを返す
        _frozen = getattr(sys, "frozen", None)
        try:
            if hasattr(sys, "frozen"):
                del sys.frozen
            assert updater.current_app_bundle() is None, "ソース実行(frozenでない)ならWindowsでもNone"
            sys.frozen = True
            fake_exe = os.path.join(tempfile.mkdtemp(prefix="tips_win_"), "TIPS ICE Planner", "TIPS ICE Planner.exe")
            os.makedirs(os.path.dirname(fake_exe))
            open(fake_exe, "w").close()
            _saved_exe = sys.executable
            sys.executable = fake_exe
            try:
                assert updater.current_app_bundle() == os.path.dirname(fake_exe), \
                    "frozen時はexeを含むonedirフォルダそのものを返す"
            finally:
                sys.executable = _saved_exe
        finally:
            if _frozen is None:
                if hasattr(sys, "frozen"):
                    del sys.frozen
            else:
                sys.frozen = _frozen

        # find_update: Windows版zip名(DIST_ZIP)で配布フォルダを見つけられる
        tmp = tempfile.mkdtemp(prefix="tips_upd_win_")
        dist = os.path.join(tmp, "TIPS ICE Planner 配布"); os.makedirs(dist)
        with zipfile.ZipFile(os.path.join(dist, updater.DIST_ZIP), "w") as z:
            z.writestr("TIPS ICE Planner/TIPS ICE Planner.exe", "dummy-exe-bytes")
            z.writestr("TIPS ICE Planner/_internal/data.bin", "x")
        json.dump({"version": "9.9.9", "notes": "test", "url": ""},
                  open(os.path.join(dist, "version.json"), "w"))
        _saved_dirs = updater.LOCAL_DIRS
        try:
            updater.LOCAL_DIRS = [dist]
            info = updater.find_update("0.4.5", update_url=None)
            assert info and info["version"] == "9.9.9" and info["source"] == "local", info
            # stage_new_bundle: zipfileで展開(ditto不使用)し、exeの親フォルダを返す
            staged = updater.stage_new_bundle(info)
            assert os.path.isdir(staged), "staged must be a directory"
            assert os.path.exists(os.path.join(staged, "TIPS ICE Planner.exe")), \
                "stage_new_bundle must return the folder CONTAINING the exe, not the exe itself"
            assert os.path.exists(os.path.join(staged, "_internal", "data.bin")), \
                "sibling files (_internal etc.) must be staged alongside the exe"
        finally:
            updater.LOCAL_DIRS = _saved_dirs
    finally:
        importlib.reload(updater)                    # IS_WIN/APP_NAME/DIST_ZIPを実環境の値に戻す
    print("✅ updater windows branch ok  (frozen-exe detection / zip name / zipfile extraction)")


def test_updater_apply_and_relaunch_windows():
    """Windows版swapスクリプト(PowerShell)の実行検証。先生報告のWindows不具合の核心＝
    current_app_bundle()が直っても、実際にアプリを入れ替えるヘルパーがbash/ditto前提のままでは
    Windowsで動かない。PowerShell Core(pwsh)がこの開発機にあれば実際にスクリプトを走らせて検証し、
    無ければ（doctorの環境や将来のCI等でpwsh未導入でも壊れないよう）静かにスキップする。"""
    import shutil as _shutil
    pwsh = _shutil.which("pwsh") or _shutil.which("powershell.exe") or _shutil.which("powershell")
    if not pwsh:
        print("⚠️  pwsh/powershell not found on PATH — Windows swap script execution test skipped"
              " (structure-only checks in test_updater_windows still ran)")
        return

    import time, importlib
    import tempfile as _tempfile
    import subprocess as _subprocess
    import updater

    work = _tempfile.mkdtemp(prefix="tips_swap_win_test_")
    old_app = os.path.join(work, "old_target")
    os.makedirs(os.path.join(old_app, "_internal"))
    with open(os.path.join(old_app, "TIPS ICE Planner.exe"), "w") as f:
        f.write("#!/bin/bash\necho OLD >> \"$(dirname \"$0\")/../relaunch.log\"\n")
    os.chmod(os.path.join(old_app, "TIPS ICE Planner.exe"), 0o755)

    new_app = os.path.join(work, "staged_new")
    os.makedirs(os.path.join(new_app, "_internal"))
    with open(os.path.join(new_app, "TIPS ICE Planner.exe"), "w") as f:
        f.write("#!/bin/bash\necho NEW >> \"$(dirname \"$0\")/../relaunch.log\"\n")
    os.chmod(os.path.join(new_app, "TIPS ICE Planner.exe"), 0o755)

    log = os.path.join(work, "update.log")
    dummy = _subprocess.Popen(["/usr/bin/true"]); dummy.wait()   # 既に終了済みpid＝待ち時間ゼロ

    try:
        updater.IS_WIN = True
        updater.APP_NAME = "TIPS ICE Planner.exe"
        captured = {}
        real_popen = _subprocess.Popen

        class _CapturePopen:                          # powershell.exe起動そのものは肩代わりし、
            def __init__(self, args, **kw):            # 生成された.ps1のパスだけ横取りして自前で実行する
                captured["ps1"] = args[-1]

        updater.subprocess.Popen = _CapturePopen
        try:
            updater._apply_update_and_relaunch_win(new_app, old_app, log, dummy.pid)
        finally:
            updater.subprocess.Popen = real_popen

        assert captured.get("ps1") and os.path.exists(captured["ps1"]), "must generate a .ps1 script file"
        _subprocess.run([pwsh, "-NoProfile", "-File", captured["ps1"]], timeout=15)
    finally:
        importlib.reload(updater)

    exe_path = os.path.join(old_app, "TIPS ICE Planner.exe")
    with open(exe_path) as f:
        assert "NEW" in f.read(), "target exe must be replaced with the NEW staged content"
    assert not os.path.exists(old_app + ".old"), "backup dir must be cleaned up after a successful swap"
    log_text = open(log, encoding="utf-8").read()
    assert "OK: swapped to new version" in log_text, f"swap must log success; log was:\n{log_text}"
    # Start-Processは非同期(fire-and-forget)なので、pwsh呼び出しの完了直後は
    # 再起動された子プロセスがまだrelaunch.logに書き込む前のことがある→ポーリングで待つ。
    relaunch_log = os.path.join(work, "relaunch.log")
    relaunched = False
    for _ in range(40):
        if os.path.exists(relaunch_log) and "NEW" in open(relaunch_log).read():
            relaunched = True
            break
        time.sleep(0.05)
    assert relaunched, "the NEW exe must actually be relaunched after the swap"
    print("✅ updater windows apply_and_relaunch ok  (real PowerShell swap script, logged, relaunches NEW exe)")


if __name__ == "__main__":
    test_ortho(); test_ice_and_geom(); test_deflection(); test_bend_monotonic(); test_needle(); test_needle3(); test_rups_colapinto(); test_predict(); test_liver(); test_features(); test_updater(); test_updater_apply_and_relaunch(); test_updater_windows(); test_updater_apply_and_relaunch_windows()
    print("✅ tips_core smoke tests passed")
