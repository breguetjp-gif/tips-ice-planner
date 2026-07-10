"""TIPS Planner — 計算核（OS非依存・numpyのみ）。

現行 Miele プラグイン TIPSPlannerFilter.m の幾何を「唯一の正本」として移植したもの。
スタンドアロン版(PySide6)・将来のWeb版・Macプラグインはすべてこの核を共有する。

含むもの:
  - ベクトル小物 (nrm/cross/rot3 = Rodrigues)
  - ortho_image  : Axial/Coronal/Sagittal の窓処理済みグレー画像 + 物理サイズ
  - ice_geometry : ICE扇の幾何（先端Tp/軸S/ビームVp/展開Sp）＝直線ロッド＋2軸偏向
  - ice_image    : ICE扇のスキャン変換像（apex上・深さ下・90°セクター・扇外dim・枠線・flip・rot）
  - bend_tip     : 偏向で曲がる先端の定曲率円弧（3D linkage用、先端接線=β）
  - needle_path  : Entry→Target を結ぶ円弧針（curve角、0=直線）
  - proj_mm / fan_fill_for_plane / fan_beam_for_plane : 3断面への投影（向きマーカー）

すべて mm 空間で計算。座標系は plugin と一致:
  画像index (x=col, y=row, z=slice) → mm は (x*sx, y*sy, z*dz)。
"""
from __future__ import annotations
import numpy as np

R_DEPTH = 85.0                 # ICE 深達 mm（plugin R）
FAN_HALF = np.radians(45.0)    # 扇の半角（plugin FAN, 90°セクター）
PXMM = 0.6                     # ICE像の画素ピッチ mm
WL_DEFAULT, WW_DEFAULT = 40.0, 400.0
# 経腹コンベックス・プローブ（標準値で固定・先生決裁 2026-06-24）
CONVEX_R0 = 50.0               # 凸型の曲率半径＝仮想頂点を皮膚の奥 r0 mm に置く
CONVEX_DEPTH = 150.0           # 深達 mm（皮膚から）
CONVEX_FAN = np.radians(35.0)  # 扇の半角（≒70°セクター）


# ---- ベクトル小物（plugin nrm3/cross3/rot3 と一致）----
def nrm(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def rot3(v, k, a):
    """Rodrigues: ベクトル v を軸 k まわりに角 a 回転。"""
    v = np.asarray(v, float); k = nrm(k)
    c, s = np.cos(a), np.sin(a)
    return v * c + np.cross(k, v) * s + k * (k @ v) * (1 - c)


def _interp(pz, fp, z):
    return float(np.interp(z, pz, fp))


# ===== 窓処理（共通）=====
def _window_u8(arr, wl, ww):
    lo = wl - ww / 2.0
    rg = ww if ww != 0 else 1.0
    v = np.clip((arr.astype(np.float32) - lo) / rg, 0.0, 1.0)
    return (v * 255.0 + 0.5).astype(np.uint8)


# ===== Axial / Coronal / Sagittal =====
def ortho_image(vol, sx, sy, dz, plane, idx, wl=WL_DEFAULT, ww=WW_DEFAULT):
    """plane: 0=Axial(z=idx) / 1=Coronal(y=idx) / 2=Sagittal(x=idx)。
    返り値: (uint8 image [row,col], 物理幅mm, 物理高mm)。plugin の各 *Img と同一。"""
    nz, H, W = vol.shape
    if plane == 0:
        idx = int(np.clip(idx, 0, nz - 1))
        img = vol[idx]                              # [H,W]
        return _window_u8(img, wl, ww), W * sx, H * sy
    if plane == 1:
        idx = int(np.clip(idx, 0, H - 1))
        img = vol[::-1, idx, :]                     # [N,W], z反転(上=高z)
        return _window_u8(img, wl, ww), W * sx, nz * dz
    idx = int(np.clip(idx, 0, W - 1))
    img = vol[::-1, :, idx]                         # [N,H], z反転
    return _window_u8(img, wl, ww), H * sy, nz * dz


# ===== ICE 扇の幾何（直線ロッド＋2軸偏向）=====
def _path_frame(path_pts, zP, sx, sy, dz):
    """path上のzPでの頂点Fbと軸接線S（＋補間用配列）を返す。≥2点必須・無効ならNone。"""
    pts = np.asarray(path_pts, float)
    if pts.shape[0] < 2:
        return None
    order = np.argsort(pts[:, 0])
    pz, py, px = pts[order, 0], pts[order, 1], pts[order, 2]
    zmin, zmax = float(pz[0]), float(pz[-1])
    zP = float(np.clip(zP, zmin, zmax))
    Fb = np.array([_interp(pz, px, zP) * sx, _interp(pz, py, zP) * sy, zP * dz])
    zA, zB = min(zmax, zP + 2), max(zmin, zP - 2)
    S = nrm([(_interp(pz, px, zA) - _interp(pz, px, zB)) * sx,
             (_interp(pz, py, zA) - _interp(pz, py, zB)) * sy,
             (zA - zB) * dz])
    return Fb, S, pz, py, px, zmin, zmax, zP


def ice_geometry(path_pts, zP, theta_deg, b1_deg, b2_deg, sx, sy, dz, tip_high_z=True):
    """ICE扇の幾何。**物理的に曲がった先端(bend_tip)から導出**し3D表示と一本化する。
      頂点Tp=曲がった先端(偏向で動く) / ビームVp=先端接線t1に直交(側射) / 扇Sp=t1に沿って広がる。
      これにより A/P=画像面内で扇が振れ、L/R=画像面と直交方向に振れて頂点がIVCから外れる(FDA K042593仕様)。
      返り値 dict: Tp,S,Vp,Sp,fan_half,R。無効なら None。"""
    bt = bend_tip(path_pts, zP, theta_deg, b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
    if bt is None:
        return None
    Tp = bt["Tp"].copy(); t1 = nrm(bt["t1"])
    V0 = np.array([np.cos(np.radians(theta_deg)), np.sin(np.radians(theta_deg)), 0.0])
    Vp = V0 - float(V0 @ t1) * t1                    # 側射ビーム＝先端接線に直交
    if np.linalg.norm(Vp) < 1e-6:                    # 縮退(先端が軸方向)→接線に直交な任意方向
        Vp = np.cross(t1, [0.0, 0, 1.0])
        if np.linalg.norm(Vp) < 1e-6:
            Vp = np.cross(t1, [1.0, 0, 0])
    Vp = nrm(Vp); Sp = nrm(t1)
    return dict(Tp=Tp, S=bt["S"], Vp=Vp, Sp=Sp, fan_half=FAN_HALF, R=R_DEPTH)


def path_tangent(path_pts, z_world, sx, sy, dz, tip_high_z=True):
    """IVCパスの world z(mm) 位置での接線（先端向き・単位）。≥2点必須・無効ならNone。
      針の『来る向き t0』を、同一スライス上のクリックに依存せず**真の3D方向**で得るために使う。"""
    fr = _path_frame(path_pts, z_world / dz, sx, sy, dz)
    if fr is None:
        return None
    S = fr[1]
    return nrm(S) if tip_high_z else nrm(-S)


# ---- ベクトル化サンプラ: z最近傍 + 面内bilinear（plugin vsample と一致）----
def _sample(vol, vz, vy, vx, inb):
    nz, H, W = vol.shape
    z = np.clip(np.rint(vz).astype(np.int64), 0, nz - 1)
    x0 = np.floor(vx).astype(np.int64); y0 = np.floor(vy).astype(np.int64)
    fx = vx - x0; fy = vy - y0
    x0c = np.clip(x0, 0, W - 1); x1c = np.clip(x0 + 1, 0, W - 1)
    y0c = np.clip(y0, 0, H - 1); y1c = np.clip(y0 + 1, 0, H - 1)
    v00 = vol[z, y0c, x0c]; v01 = vol[z, y0c, x1c]
    v10 = vol[z, y1c, x0c]; v11 = vol[z, y1c, x1c]
    out = (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
           + v10 * (1 - fx) * fy + v11 * fx * fy)
    return out


def ice_image(vol, sx, sy, dz, geom, wl=WL_DEFAULT, ww=WW_DEFAULT, flip=False):
    """ICE扇のスキャン変換像（apex上・深さ下）。plugin iceImg のレンダリングと同一。
    返り値: (uint8 [Hi,Wi], 物理幅mm, 物理高mm)。回転(_rot)はUI側で行う。"""
    if geom is None:
        return None
    Tp, Vp, Sp = geom["Tp"], geom["Vp"], geom["Sp"]
    R, FAN = geom["R"], geom["fan_half"]
    r0 = float(geom.get("r0", 0.0))                        # 仮想頂点を皮膚の奥 r0 mm（凸型）。0=ICE(後方互換・画素一致)
    halfW = R * np.sin(FAN) + 12.0
    depth = (R - r0) + 10.0                                # 表示する深さ＝皮膚(=Tp)からの距離
    Wi = int(2 * halfW / PXMM); Hi = int(depth / PXMM)
    if Wi < 10 or Hi < 10:
        return None
    rr, cc = np.meshgrid(np.arange(Hi), np.arange(Wi), indexing="ij")
    Y = rr * PXMM; X = (cc - Wi / 2.0) * PXMM              # Y=皮膚からの深さ, X=側方。サンプリングは平面MPRのまま
    Px = Tp[0] + Y * Vp[0] + X * Sp[0]
    Py = Tp[1] + Y * Vp[1] + X * Sp[1]
    Pz = Tp[2] + Y * Vp[2] + X * Sp[2]
    vx, vy, vz = Px / sx, Py / sy, Pz / dz
    nz, H, W = vol.shape
    inb = (vx >= 0) & (vx <= W - 1) & (vy >= 0) & (vy <= H - 1) & (vz >= 0) & (vz <= nz - 1)
    hu = _sample(vol, vz, vy, vx, inb)
    lo = wl - ww / 2.0; rg = ww if ww != 0 else 1.0
    v = np.clip((hu - lo) / rg, 0.0, 1.0)
    Yap = Y + r0; rho = np.hypot(X, Yap); phi = np.arctan2(X, Yap)   # 仮想頂点からの極座標で扇マスク
    infan = (rho >= r0) & (rho <= R) & (np.abs(phi) <= FAN)
    out = np.where(infan, v, v * 0.30)
    edge = (rho > max(3.0, r0 + 0.5)) & (
        ((rho <= R + 0.6) & (np.abs(np.abs(phi) - FAN) <= 1.5 * PXMM / (rho + 1e-3)))
        | ((np.abs(rho - R) <= 1.1 * PXMM) & (np.abs(phi) <= FAN))
        | ((r0 > 0) & (np.abs(rho - r0) <= 1.1 * PXMM) & (np.abs(phi) <= FAN)))   # 近接弧(凸型のみ)
    img = (out * 255.0 + 0.5).astype(np.uint8)
    img[edge] = 240
    img[~inb] = 0                                          # 体外=黒
    if flip:
        img = img[:, ::-1]
    return img, Wi * PXMM, Hi * PXMM


# ===== 偏向で曲がる先端（3D linkage用・定曲率円弧）=====
def bend_tip(path_pts, zP, theta_deg, b1_deg, b2_deg, sx, sy, dz,
             tip_high_z=True, Lb=30.0):
    """先端30mm近位を支点に、A/P+L/R合成方向へ全角βの円弧で曲げる。
    返り値 dict: F(支点), orange(F→先端のポリライン Nx3), Tp(曲がった先端), t1(先端接線)。"""
    fr = _path_frame(path_pts, zP, sx, sy, dz)       # 円環依存を断つ（ice_geometryを呼ばない）
    if fr is None:
        return None
    apOn, S, pz, py, px, zmin, zmax, zP = fr
    apOn = apOn.copy()
    dd = 1.0 if tip_high_z else -1.0
    td3 = nrm(dd * S)
    b1r, b2r = np.radians(b1_deg), np.radians(b2_deg)
    # 遠位ポリライン(先端→近位, 累積長 Lb まで)
    distal = [apOn.copy()]; acc = 0.0; pr = apOn.copy(); zz = zP - dd; zf = zP
    while (zz >= zmin if dd > 0 else zz <= zmax):
        p = np.array([_interp(pz, px, zz) * sx, _interp(pz, py, zz) * sy, zz * dz])
        acc += np.linalg.norm(p - pr); pr = p; distal.append(p); zf = zz
        if acc >= Lb:
            break
        zz -= dd
    md = len(distal); F = distal[-1].copy()
    # 近位シャフト(灰): F より近位の経路点 → 末尾でFへ接続
    shaft = []
    z2 = zf - dd
    while (z2 >= zmin if dd > 0 else z2 <= zmax):
        shaft.append(np.array([_interp(pz, px, z2) * sx, _interp(pz, py, z2) * sy, z2 * dz]))
        z2 -= 2 * dd
    shaft = shaft[::-1]; shaft.append(F)
    t0 = nrm((distal[-2] if md >= 2 else distal[0]) - F)
    if np.linalg.norm(t0) < 1e-6:
        t0 = td3
    Vp0 = np.array([np.cos(np.radians(theta_deg)), np.sin(np.radians(theta_deg)), 0.0])
    nAP = Vp0 - (Vp0 @ t0) * t0
    nAP = nrm(nAP) if np.linalg.norm(nAP) > 1e-6 else np.array([1.0, 0, 0])
    nLR = nrm(np.cross(t0, nAP))
    n = b1r * nAP + b2r * nLR
    beta = np.hypot(b1r, b2r)
    if beta > 1e-4 and np.linalg.norm(n) > 1e-9:
        n = nrm(n); tot = acc if acc > 1e-6 else Lb; rho = tot / beta
        NB = (md - 1) if md > 2 else 10
        orange = [F + rho * np.sin(beta * i / NB) * t0 + rho * (1 - np.cos(beta * i / NB)) * n
                  for i in range(NB + 1)]
        Tp = orange[-1].copy(); t1 = nrm(orange[-1] - orange[-2])
    else:
        orange = [distal[i] for i in range(md - 1, -1, -1)]; Tp = apOn.copy(); t1 = td3
    return dict(F=F, orange=np.array(orange), Tp=Tp, t1=t1, apOn=apOn, shaft=np.array(shaft), S=S)


# ===== 針（Entry→Target を結ぶ円弧、curve角で弓なり, 0=直線）=====
def needle_path(entry, target, curve_deg, n=44):
    P = np.asarray(entry, float); T = np.asarray(target, float)
    C = T - P; d = float(np.linalg.norm(C))
    if d < 1.0:
        return None
    beta = np.radians(curve_deg)
    if abs(beta) < 0.02:
        return np.array([P + C * (i / (n - 1)) for i in range(n)])
    u = C / d
    bb = np.cross(u, [0, 0, 1.0])
    if np.linalg.norm(bb) < 1e-6:
        bb = np.cross(u, [0, 1.0, 0])
    bb = nrm(bb)
    Cx, Cy = d / 2.0, -d / (2.0 * np.tan(beta))
    Rr = np.hypot(Cx, Cy)
    aP = np.arctan2(0 - Cy, 0 - Cx); aT = np.arctan2(0 - Cy, d - Cx)
    dang = aT - aP
    while dang > np.pi:
        dang -= 2 * np.pi
    while dang < -np.pi:
        dang += 2 * np.pi
    out = []
    for i in range(n):
        ang = aP + dang * i / (n - 1)
        pu = Cx + Rr * np.cos(ang); pb = Cy + Rr * np.sin(ang)
        out.append(P + pu * u + pb * bb)
    return np.array(out)


def needle_path3(axis, entry, target, straight=False, n=44):
    """3点方式の針。軸(axis→entry)の延長を初期接線とし、entry から target へ。
      straight(RUPS) = 軸延長へ直進レイ／曲線(Colapinto) = 軸延長で出て target を通る平面円弧。
      axis 未設定（axis≈entry）なら従来同様 entry→target を初期接線にする。"""
    A = np.asarray(axis, float); P = np.asarray(entry, float); T = np.asarray(target, float)
    t0 = P - A
    t0 = nrm(t0) if np.linalg.norm(t0) > 1e-6 else nrm(T - P)
    L = float(np.linalg.norm(T - P))
    if L < 1.0:
        return None
    if straight:
        return np.array([P, P + L * t0])                          # RUPS: 軸延長へ直進
    d = T - P
    ey = d - (d @ t0) * t0                                         # 接線に直交する成分
    if np.linalg.norm(ey) < 1e-6:
        return np.array([P, T])                                    # 標的が軸延長上＝直線
    ey = nrm(ey)
    tx = float(d @ t0); ty = float(d @ ey)
    r = (tx * tx + ty * ty) / (2.0 * ty)                          # 中心(0,r)・x軸(=t0)に接する円
    rad = abs(r)
    a0 = np.arctan2(-r, 0.0); aT = np.arctan2(ty - r, tx)
    dang = aT - a0
    while dang > np.pi:
        dang -= 2 * np.pi
    while dang < -np.pi:
        dang += 2 * np.pi
    out = [P + (rad * np.cos(a0 + dang * i / (n - 1))) * t0
           + (r + rad * np.sin(a0 + dang * i / (n - 1))) * ey for i in range(n)]
    return np.array(out)


# ===== RUPS / Colapinto デバイス機構（Step2の針プランニング）=====
# 実寸は未受領のため推定値（実データ受領後に差替）:
RUPS_ALPHA = 40.0     # RUPSカニューラ先端の固定曲げ角(度) 推定(文献36–45°)
RUPS_LC = 43.5        # RUPSカニューラ曲げ部の長さ(mm) 推定(PMC5998197)
COLA_R = 55.0         # Colapinto針の固有曲げ半径(mm) 推定(要実データ)


def _poly_point_dist(poly, T):
    """折れ線polyと点Tの最短距離(mm)。Target到達ズレの算出用。"""
    P = np.asarray(poly, float); T = np.asarray(T, float); best = 1e9
    for i in range(len(P) - 1):
        a = P[i]; ab = P[i + 1] - a; L2 = float(ab @ ab)
        t = 0.0 if L2 < 1e-9 else float(np.clip((T - a) @ ab / L2, 0.0, 1.0))
        best = min(best, float(np.linalg.norm(T - (a + t * ab))))
    return best


def _bend_normal(t0, target, D):
    """t0 に直交し target 方向へ向く単位ベクトル（曲げ面内方向）。縮退時のフォールバック付き。"""
    aim = np.asarray(target, float) - np.asarray(D, float)
    nb = aim - (aim @ t0) * t0
    if np.linalg.norm(nb) < 1e-6:
        nb = np.cross(t0, [0.0, 0, 1.0])
        if np.linalg.norm(nb) < 1e-6:
            nb = np.cross(t0, [1.0, 0, 0])
    return nrm(nb)


def straight_path(D, target):
    """最小・確実な穿刺軌道＝Entry→Target を結ぶ直線。
      D=Entry(刺入点), target=Target(門脈). 曲げ・前進量・カニューラなし。
      返り値 dict: cannula(None), needle(2x3=直線), full, length(mm), miss(=0), tip(=target)."""
    D = np.asarray(D, float); T = np.asarray(target, float)
    needle = np.array([D, T])
    return dict(cannula=None, needle=needle, full=needle,
                length=float(np.linalg.norm(T - D)), miss=0.0, tip=T)


def needle_assembly(entry, target, up=(0.0, 0.0, 1.0),
                    cannula_len=90.0, fillet=9.0, n_fillet=14, pred_len=22.0,
                    needle_w=2.2, tip_bevel=7.0):
    """TIPS金属針システムの3D表現（外筒＋弯曲接続部＋針シャフト＋金属ベベル先端＋進行方向）。
      entry=刺入点(肝静脈壁), target=標的(門脈), up=頭側方向(既定=world +z)。
      ・外筒(cannula): entryから頸静脈側=頭側へ cannula_len mm 立てた直棒（TIPS金属針の外筒）。
      ・接続部＋シャフト(body): 外筒方向→針方向 を二次ベジェで丸め(かくかく解消)、
        そのまま先端手前(bevel開始)まで続く太い針の芯線。
      ・先端(tip): bevel開始→target の金属ベベル(尖った切っ先)。三角形の点列（描画は金属色）。
      ・blade: 接続弧＋針中心線の全長ポリライン（連続性の検証用）。
      ・pred: 針先(target)から進行方向へ pred_len mm 伸ばす直線（3Dの流れるダッシュ用）。
      target=None のときは外筒のみ（他=None）。
      返り値 dict(cannula, body, tip, blade, tip_point, pred, ndir, width)。"""
    E = np.asarray(entry, float); up = nrm(np.asarray(up, float))
    C_top = E + cannula_len * up
    none = dict(cannula=np.array([C_top, E]), body=None, tip=None,
                blade=None, tip_point=E, pred=None, ndir=None, width=needle_w)
    if target is None:
        return none
    T = np.asarray(target, float)
    ndir = T - E; L = float(np.linalg.norm(ndir))
    if L < 1e-6:
        return none
    ndir = ndir / L
    r = min(fillet, cannula_len * 0.5, L * 0.5)          # 丸めは短い辺に合わせる
    J0 = E + r * up                                       # 外筒側の丸め開始（entryより頭側）
    J1 = E + r * ndir                                     # 針側の丸め終了（entryより標的側）
    ts = np.linspace(0.0, 1.0, n_fillet)                 # 二次ベジェ（制御点=entry）で J0→J1 を滑らかに
    arc = np.array([(1 - t) ** 2 * J0 + 2 * (1 - t) * t * E + t * t * J1 for t in ts])
    bl = min(tip_bevel, L * 0.5)
    bstart = T - bl * ndir                               # 金属ベベル先端の付け根
    body = np.vstack([arc, bstart])                      # 接続弧＋シャフト（ベベル手前まで）＝太く描く芯線
    blade = np.vstack([arc, T])                          # 全長（連続性検証用）
    perp = np.cross(ndir, up)                            # 先端三角形を張る安定な横方向
    if np.linalg.norm(perp) < 1e-6:
        perp = np.cross(ndir, [1.0, 0.0, 0.0])
    perp = nrm(perp); hw = needle_w / 2.0
    tip = np.array([bstart + hw * perp, T, bstart - hw * perp])   # 尖った金属ベベル
    pred = np.array([T, T + pred_len * ndir])            # 進行方向（前方）
    return dict(cannula=np.array([C_top, J0]), body=body, tip=tip,
                blade=blade, tip_point=T, pred=pred, ndir=ndir, width=needle_w)


def rups_path(D, t_path, target, alpha_deg=RUPS_ALPHA, Lc=RUPS_LC, throw=90.0, n=14):
    """RUPS-100: 固定の曲がった金属カニューラ → そこから針が**直進**。
      D=偏向点(Entry=肝静脈壁), t_path=カニューラの来る向き(IVC接線/Axis→Entry),
      target=狙い(目印). カニューラは t0 から target 方位へ**固定角α**だけ曲がり(長さLc),
      針はカニューラ先端から exit 方向へ**直進**(throw mm)。Target には自動では当てない。
      返り値 dict: cannula(Nx3), needle(2x3), full(連結), miss(target到達ズレmm), tip(先端)."""
    D = np.asarray(D, float); t0 = nrm(t_path); a = np.radians(alpha_deg)
    nb = _bend_normal(t0, target, D)
    exit_dir = nrm(np.cos(a) * t0 + np.sin(a) * nb)               # カニューラ出口方向(固定角)
    R = Lc / a if a > 1e-4 else 1e6
    S0 = D - (R * np.sin(a) * t0 + R * (1 - np.cos(a)) * nb)      # 曲げ部の近位端
    cannula = np.array([S0 + R * np.sin(a * i / (n - 1)) * t0 + R * (1 - np.cos(a * i / (n - 1))) * nb
                        for i in range(n)])                       # 近位端→D の弧
    needle = np.array([D, D + throw * exit_dir])                  # D から直進
    full = np.vstack([cannula, needle[1:]])
    return dict(cannula=cannula, needle=needle, full=full,
                miss=_poly_point_dist(needle, target), tip=needle[-1])


def colapinto_path(D, t_path, target, R_c=COLA_R, s_adv=90.0, n=30):
    """Colapinto: 針**自体が固有曲率**で、D から target 方位へ**弧を描いて進む**。
      D=Entry, t_path=初期接線(IVC), target=狙い(目印). 曲率半径 R_c はデバイス固有(固定),
      target 方位へ s_adv(弧長) 前進。Target には自動では当てない(ズレを表示)。
      返り値 dict: cannula(None), needle(Nx3=弧), full, miss, tip."""
    D = np.asarray(D, float); t0 = nrm(t_path); nb = _bend_normal(t0, target, D)
    s = np.linspace(0.0, max(s_adv, 1.0), n)
    arc = np.array([D + R_c * np.sin(si / R_c) * t0 + R_c * (1 - np.cos(si / R_c)) * nb for si in s])
    return dict(cannula=None, needle=arc, full=arc,
                miss=_poly_point_dist(arc, target), tip=arc[-1])


# ===== 前方予測（Plot/予習モード・機構忠実）=====
# RUPS-100: 発射台を曲げ針は直進 → 進行方向に直進レイ。
# Colapinto: 針自体が曲がる → 進行方向に対し頭側(+z)へ固定曲率の弧。
def _bend_dir(u, up=(0.0, 0.0, 1.0)):
    """進行方向uに直交する「頭側(+z)成分」＝Colapintoの曲がる向き。"""
    u = nrm(u); up = np.asarray(up, float)
    n = up - (up @ u) * u
    if np.linalg.norm(n) < 1e-6:
        n = np.cross(u, [1.0, 0, 0])
        if np.linalg.norm(n) < 1e-6:
            n = np.cross(u, [0, 1.0, 0])
    return nrm(n)


def predict_straight(p_prev, p_tip, length=120.0):
    """RUPS: 2点(p_prev→p_tip)の進行方向にまっすぐ伸ばすレイ。"""
    p_prev = np.asarray(p_prev, float); p_tip = np.asarray(p_tip, float)
    u = nrm(p_tip - p_prev)
    return np.array([p_tip, p_tip + length * u])


def predict_curve(p_prev, p_tip, radius=55.0, span_deg=75.0, n=24, up=(0.0, 0.0, 1.0), torque_deg=0.0):
    """Colapinto: p_tipから接線u、頭側nbへ半径Rの定曲率弧。
      torque_deg: 手元でカニューラを軸(u)まわりに捻った角度。+=右回転(操作者が針の根元から先端方向を見て時計回り)。
      nbをuまわりにこの角度だけ回す＝『右に回すとどちらへ曲がるか』の目安を作る。"""
    p_prev = np.asarray(p_prev, float); p_tip = np.asarray(p_tip, float)
    u = nrm(p_tip - p_prev); nb = _bend_dir(u, up)
    if abs(torque_deg) > 1e-9:
        nb = nrm(rot3(nb, u, np.radians(torque_deg)))
    R = float(radius)
    out = [p_tip + R * np.sin(np.radians(span_deg) * i / n) * u
           + R * (1 - np.cos(np.radians(span_deg) * i / n)) * nb for i in range(n + 1)]
    return np.array(out)


def predict_readout(p_prev, p_tip, target, up=(0.0, 0.0, 1.0)):
    """直進レイから標的への垂直距離・前後成分・標的が頭側/足側か（Colapintoの寄離判定用）。"""
    p_prev = np.asarray(p_prev, float); p_tip = np.asarray(p_tip, float); T = np.asarray(target, float)
    u = nrm(p_tip - p_prev); w = T - p_tip
    along = float(w @ u); perp_vec = w - along * u; perp = float(np.linalg.norm(perp_vec))
    nb = _bend_dir(u, up); side = float(perp_vec @ nb)     # >0: 標的は頭側(=Colapintoが寄る側)
    return dict(perp=perp, along=along, side=side)


def fit_circle_radius(points):
    """≥3点に最適平面内で円フィット→実測曲率半径(mm)。RUPSは大、Colapintoは小。"""
    P = np.asarray(points, float)
    if len(P) < 3:
        return None
    c = P.mean(0); Q = P - c
    try:
        _, _, Vt = np.linalg.svd(Q)
        x = Q @ Vt[0]; y = Q @ Vt[1]
        A = np.c_[2 * x, 2 * y, np.ones(len(x))]; b = x * x + y * y
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        R = np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0))
        return float(R)
    except Exception:
        return None


# ===== 3断面への投影（向きマーカー）=====
def proj_mm(P, sx, sy, dz, plane, nz):
    xi, yi, zi = P[0] / sx, P[1] / sy, P[2] / dz
    if plane == 0:
        return xi, yi                       # Axial (x,y)
    if plane == 1:
        return xi, (nz - 1) - zi            # Coronal (x, N-1-z)
    return yi, (nz - 1) - zi                # Sagittal (y, N-1-z)


def fan_fill_for_plane(geom, sx, sy, dz, plane, nz, M=20):
    """扇(深達R)を断面へ投影したポリゴン点列。ICE=apex+弧(パイ)／凸型=近接弧r0+遠弧R(環状セクター)。"""
    if geom is None:
        return None
    Tp, Vp, Sp, R, FAN = geom["Tp"], geom["Vp"], geom["Sp"], geom["R"], geom["fan_half"]
    r0 = float(geom.get("r0", 0.0))
    if r0 <= 1e-6:
        pts = [proj_mm(Tp, sx, sy, dz, plane, nz)]
        for i in range(M + 1):
            phi = -FAN + 2 * FAN * i / M
            d = np.cos(phi) * Vp + np.sin(phi) * Sp
            pts.append(proj_mm(Tp + R * d, sx, sy, dz, plane, nz))
        return pts
    A = Tp - r0 * Vp                                       # 仮想頂点
    near, far = [], []
    for i in range(M + 1):
        phi = -FAN + 2 * FAN * i / M
        d = np.cos(phi) * Vp + np.sin(phi) * Sp
        near.append(proj_mm(A + r0 * d, sx, sy, dz, plane, nz))
        far.append(proj_mm(A + R * d, sx, sy, dz, plane, nz))
    return near + far[::-1]                                # 近接弧→遠弧(逆順) の閉ポリゴン


def fan_beam_for_plane(geom, sx, sy, dz, plane, nz):
    if geom is None:
        return None
    Tp, Vp, R = geom["Tp"], geom["Vp"], geom["R"]
    r0 = float(geom.get("r0", 0.0))
    far = Tp + (R - r0) * Vp                               # 中心ビーム＝皮膚から深さ(R-r0)
    return [proj_mm(Tp, sx, sy, dz, plane, nz),
            proj_mm(far, sx, sy, dz, plane, nz)]


# ===== 針先→標的の位置関係（記述のみ・術中ナビではない）=====
def aim_readout(tip, target, orient=None):
    """針先 tip から 標的 target への「位置関係」を測って返す（純粋関数）。
      点は world mm の (x,y,z)=(col*sx, row*sy, slice*dz)。orient=meta['orient']
      （index→LPS：列[rowd,cold,zdir]）があれば解剖方位へ分解。
      LPS: +x=左, +y=背側(後方), +z=頭側(上方)。
      返り値 dict: dist(mm), has_orient, comps=[(ラベル, mm)...], lps=[x,y,z] or None。
      ※これは『先生が入力した点の幾何計測』であり、経路の提案・最適化・到達保証ではない。"""
    tip = np.asarray(tip, float); target = np.asarray(target, float)
    w = target - tip
    dist = float(np.linalg.norm(w))
    if orient is None:
        return dict(dist=dist, has_orient=False, comps=[], lps=None)
    M = np.asarray(orient, float)
    lps = M @ w
    x, y, z = float(lps[0]), float(lps[1]), float(lps[2])
    comps = [("左" if x >= 0 else "右", abs(x)),
             ("背側" if y >= 0 else "腹側", abs(y)),
             ("頭側" if z >= 0 else "尾側", abs(z))]
    return dict(dist=dist, has_orient=True, comps=comps, lps=[x, y, z])


# ===== ICE同一断面チェック（針路が1つのICE扇平面に乗るか）=====
def ice_coplanarity(geom, entry, target, step_deg=0.5):
    """ICE扇平面（頂点Tp・軸Sp=t1・現ビームVp）に対する Entry/Target の面外ズレ(mm)と、
      針路(Entry→Target)が最も同一断面に乗る θ(度) を返す。記述・教育用。
      返り値 dict(off_entry, off_target, best_theta, best_off)。"""
    Tp = np.asarray(geom["Tp"], float); a = nrm(np.asarray(geom["Sp"], float))
    Vp = nrm(np.asarray(geom["Vp"], float))
    E = np.asarray(entry, float) - Tp; T = np.asarray(target, float) - Tp
    n_cur = np.cross(Vp, a); nn = np.linalg.norm(n_cur)
    n_cur = n_cur / nn if nn > 1e-6 else np.array([0.0, 0.0, 1.0])
    off_e = float(E @ n_cur); off_t = float(T @ n_cur)                # 面外ズレ(現θ)
    best_th, best_off = 0.0, 1e18
    m = int(round(360.0 / step_deg))
    for i in range(m):                                               # θを一周して針路が最も乗る面を探す
        th = np.radians(i * step_deg)
        V0 = np.array([np.cos(th), np.sin(th), 0.0]); Vpt = V0 - (V0 @ a) * a
        if np.linalg.norm(Vpt) < 1e-6:
            continue
        Vpt = nrm(Vpt); nv = np.cross(Vpt, a); mm = np.linalg.norm(nv)
        if mm < 1e-6:
            continue
        nv = nv / mm; off = max(abs(E @ nv), abs(T @ nv))
        if off < best_off:
            best_off, best_th = off, i * step_deg
    return dict(off_entry=off_e, off_target=off_t, best_theta=best_th, best_off=best_off)


# ===== 経腹コンベックス・プローブの扇幾何（ice_image が消費する dict を返す）=====
def surface_geometry(contact, inward_normal, theta_deg, tilt_deg, rock_deg, sx, sy, dz,
                     plane_axis=(0.0, 0.0, 1.0), r0=CONVEX_R0, depth=CONVEX_DEPTH, fan_half=CONVEX_FAN):
    """皮膚の接触点から体内へ向く凸型プローブの扇。
      theta=ビーム軸まわりの回転 / tilt=面内あおり / rock=面外あおり。
      plane_axis=置いたCT断面の法線（Axial=z / Coronal=y / Sagittal=x）。扇はその断面内に広がる。
      返り値 dict(Tp=接触点, Vp=ビーム方向, Sp=側方, fan_half, R=r0+depth, r0, mode)。"""
    C = np.asarray(contact, float)
    n0 = np.asarray(inward_normal, float)
    if np.linalg.norm(n0) < 1e-6:
        n0 = np.array([0.0, 1.0, 0.0])
    n0 = nrm(n0)
    pa = np.asarray(plane_axis, float)                     # 撮像面の法線＝この面内に扇を広げる
    pa = nrm(pa) if np.linalg.norm(pa) > 1e-6 else np.array([0.0, 0.0, 1.0])
    u0 = np.cross(n0, pa)                                  # 側方軸（＝撮像面内でビームに直交）
    if np.linalg.norm(u0) < 1e-6:                          # ビームが面法線と平行→代替
        u0 = np.cross(n0, [1.0, 0.0, 0.0])
        if np.linalg.norm(u0) < 1e-6:
            u0 = np.cross(n0, [0.0, 1.0, 0.0])
    u0 = nrm(u0)
    u = rot3(u0, n0, np.radians(theta_deg))               # ビーム軸まわりに回す（プローブ回転）
    w = nrm(np.cross(n0, u))                               # 面外軸
    n1 = rot3(n0, w, np.radians(tilt_deg))                # 面内あおり（ビームを w まわりに）
    Vp = nrm(rot3(n1, u, np.radians(rock_deg)))           # 面外あおり（u まわりに）
    Sp = u - float(u @ Vp) * Vp                           # 側方（Vp に直交化）
    if np.linalg.norm(Sp) < 1e-6:
        Sp = w - float(w @ Vp) * Vp
    Sp = nrm(Sp)
    return dict(Tp=C, Vp=Vp, Sp=Sp, fan_half=fan_half, R=r0 + depth, r0=r0, mode="surface")


def needle_glyph(p1, tip, width=2.2, taper=7.0):
    """実際の針の外形（world 3D点）。軸(p1→tip)の細いシャフト＋トロカール状の対称テーパー先端。
      半透明で重ねて描く『線ではなく物体』の表現。UIで掴む点は p1/tip のまま。
      返り値 dict(outline=閉ポリゴン点列(shaft+tip), tip=先端点, shaft_end=テーパー開始点)。"""
    P = np.asarray(p1, float); T = np.asarray(tip, float)
    ax = T - P; L = float(np.linalg.norm(ax))
    if L < 1e-6:
        return dict(outline=np.array([P, P, P]), tip=T, shaft_end=P)
    u = ax / L
    perp = np.cross(u, [0.0, 0.0, 1.0])
    if np.linalg.norm(perp) < 1e-6:
        perp = np.cross(u, [1.0, 0.0, 0.0])
    perp = nrm(perp)
    t = min(taper, L); shaft_end = T - t * u; hw = width / 2.0
    outline = np.array([P + hw * perp, shaft_end + hw * perp, T,
                        shaft_end - hw * perp, P - hw * perp])
    return dict(outline=outline, tip=T, shaft_end=shaft_end)


def probe_glyph(geom, housing_mm=24.0, n=16):
    """コンベックス・プローブの外形（world 3D点・撮像面内）。
      face=皮膚に当たる凸面の弧／outline=筐体(体外側)込みの閉ポリゴン点列。UIで掴む・描くのに使う。"""
    Tp = np.asarray(geom["Tp"], float); Vp = np.asarray(geom["Vp"], float); Sp = np.asarray(geom["Sp"], float)
    r0 = float(geom.get("r0", 0.0)); fan = float(geom["fan_half"])
    rr = r0 if r0 > 1e-3 else 8.0                          # ICE(r0=0)でも小さな凸面に
    A = Tp - rr * Vp                                       # 仮想頂点（皮膚の外側）
    face = [A + rr * (np.cos(ph) * Vp + np.sin(ph) * Sp) for ph in np.linspace(-fan, fan, n)]
    L, R = face[0], face[-1]
    Rh = R - housing_mm * Vp; Lh = L - housing_mm * Vp     # 筐体（体外側＝-Vp）
    outline = face + [Rh, Lh]                              # 凸面(L→R)→右筐体上→左筐体上（描画時に閉じる）
    return dict(face=np.array(face), outline=np.array(outline), housing=np.array([L, R, Rh, Lh]))


def snap_to_skin(slice2d, px, py, sx, sy, air=-300.0, max_steps=400):
    """axialスライス(H,W=HU)上で、クリック(px=col, py=row)を皮膚境界へ吸着し、
      接触点(col,row)と『体内向き法線(単位, mm空間 x,y)』を返す。便宜機能（外れたら据え置き）。
      体内クリック=外向きに進み皮膚(最後の体内画素)へ／体外クリック=内向きに進み最初の体内画素へ。"""
    H, W = slice2d.shape
    body = slice2d > air
    if int(body.sum()) < 50:
        return (float(px), float(py)), (0.0, 1.0)
    ys, xs = np.nonzero(body)
    cx, cy = float(xs.mean()), float(ys.mean())           # 体の重心
    inx, iny = (cx - px) * sx, (cy - py) * sy             # 体内向き(mm)＝クリック→重心
    L = np.hypot(inx, iny)
    inx, iny = (inx / L, iny / L) if L > 1e-6 else (0.0, 1.0)
    tx, ty = (cx - px), (cy - py)                         # 内向き単位(画素)
    tn = np.hypot(tx, ty)
    tx, ty = (tx / tn, ty / tn) if tn > 1e-6 else (0.0, 1.0)

    def isbody(xp, yp):
        ix, iy = int(round(xp)), int(round(yp))
        return 0 <= ix < W and 0 <= iy < H and bool(body[iy, ix])

    contact = (float(px), float(py)); cxp, cyp = float(px), float(py)
    if isbody(px, py):                                    # 体内 → 外向きに皮膚(最後の体内画素)へ
        for _ in range(max_steps):
            if isbody(cxp, cyp):
                contact = (cxp, cyp); cxp -= tx; cyp -= ty
            else:
                break
    else:                                                 # 体外 → 内向きに最初の体内画素(=皮膚)へ
        for _ in range(max_steps):
            if isbody(cxp, cyp):
                contact = (cxp, cyp); break
            cxp += tx; cyp += ty
    return contact, (inx, iny)
