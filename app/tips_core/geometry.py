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

# ── ビーム定数・経路パラメータ化は術式プリセット（app/preset.py）から取る ──────────
# preset が無い素の import 時は ICE/TIPS の既定値（従来の計算核と同値）。
try:
    import preset as _preset       # 各アプリの app/preset.py（bare import・app/ が sys.path 前提）
except Exception:                  # 単体利用（プラグイン移植・ノートブック等）
    _preset = None
R_DEPTH = float(getattr(_preset, "R_DEPTH_MM", 85.0))                  # 描出深達 mm（ICE=85 / EUS linear=100）
FAN_HALF = np.radians(float(getattr(_preset, "FAN_HALF_DEG", 45.0)))   # 扇の半角（ICE=45°=90°扇 / EUS=75°=150°扇）
PATH_PARAM = str(getattr(_preset, "PATH_PARAM", "z"))                  # 経路座標: "z"=体軸単調(IVC芯線) / "s"=弧長(管腔)
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
def _path_frame_s(path_pts, sP, sx, sy, dz):
    """path上の弧長 sP(mm, 口側=0) での頂点Fbと軸接線S（口→遠位向き）を返す。
    ★クリック順を口→遠位として扱い、zソートしない＝食道→胃→十二指腸のような
      z非単調パスに対応（旧IVC版はzで補間していたため非単調で破綻していた）。
    返り値: Fb, S, s(累積弧長配列), Wp(world点列 Nx3, クリック順), 0.0, L, sP。無効ならNone。"""
    pts = np.asarray(path_pts, float)
    if pts.shape[0] < 2:
        return None
    Wp = np.column_stack([pts[:, 2] * sx, pts[:, 1] * sy, pts[:, 0] * dz])   # world[x,y,z]（クリック順のまま）
    seg = np.linalg.norm(np.diff(Wp, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(s[-1])
    if L < 1e-6:
        return None
    sP = float(np.clip(sP, 0.0, L))
    def _at(sq):
        sq = min(L, max(0.0, sq))
        return np.array([np.interp(sq, s, Wp[:, 0]), np.interp(sq, s, Wp[:, 1]), np.interp(sq, s, Wp[:, 2])])
    Fb = _at(sP)
    S = nrm(_at(min(L, sP + 2.0)) - _at(max(0.0, sP - 2.0)))                # 口→遠位（弧長増加）向き
    return Fb, S, s, Wp, 0.0, L, sP


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
def bend_tip_s(path_pts, sP, theta_deg, b1_deg, b2_deg, sx, sy, dz,
             tip_high_z=True, Lb=30.0, shaft_len=90.0):
    """先端30mm近位を支点に、A/P+L/R合成方向へ全角βの円弧で曲げる（EUS: 弧長パラメータ版）。
    sP=弧長(mm,口側=0)。tip_high_z=True で先端は遠位(挿入先)側、False で口側。
    返り値 dict: F(支点), orange(F→先端 Nx3), Tp(曲がった先端), t1(先端接線), apOn, shaft, S。"""
    fr = _path_frame_s(path_pts, sP, sx, sy, dz)
    if fr is None:
        return None
    apOn, S, s, Wp, smin, L, sP = fr
    apOn = apOn.copy()
    def _at(sq):
        sq = min(L, max(0.0, sq))
        return np.array([np.interp(sq, s, Wp[:, 0]), np.interp(sq, s, Wp[:, 1]), np.interp(sq, s, Wp[:, 2])])
    dd = 1.0 if tip_high_z else -1.0                     # tip 方向（+1=遠位/弧長増加, -1=口側）
    td3 = nrm(dd * S)
    b1r, b2r = np.radians(b1_deg), np.radians(b2_deg)
    # 遠位ポリライン：apex(sP)から tip 方向へ 累積長 Lb まで（1mm刻み）
    distal = [apOn.copy()]; acc = 0.0; pr = apOn.copy(); sq = sP + dd * 1.0
    while (sq <= L if dd > 0 else sq >= 0.0):
        p = _at(sq); acc += float(np.linalg.norm(p - pr)); pr = p; distal.append(p); sf = sq
        if acc >= Lb:
            break
        sq += dd * 1.0
    else:
        sf = sP
    md = len(distal); F = distal[-1].copy()
    # 近位シャフト（口側＝反tip方向）
    shaft = []; s2 = sf - dd * 2.0
    while (0.0 <= s2 <= L):
        shaft.append(_at(s2)); s2 -= dd * 2.0
    shaft = shaft[::-1]; shaft.append(F)
    # 近位を接線方向へ延長（実機のスコープは撮影範囲外まで続く＝口側へ長い）
    if len(shaft) >= 2:
        seg2 = np.linalg.norm(np.diff(np.asarray(shaft, float), axis=0), axis=1)
        have = float(seg2.sum()); u = nrm(np.asarray(shaft[0], float) - np.asarray(shaft[1], float))
    else:
        have = 0.0; u = nrm(F - distal[-2]) if md >= 2 else -td3
    if have < shaft_len and np.linalg.norm(u) > 1e-6:
        shaft = [np.asarray(shaft[0], float) + u * (shaft_len - have)] + list(shaft)
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


def eus_scope_glyph(Tp, t1, Vp, sect, fan_half, tip_len=38.0, tip_dia=14.6, r0=10.0, bump=2.6, arc_n=14):
    """EUSコンベックス内視鏡の先端グリフ（撮像面(sect,Vp)内のworld 3D点列）。
    実機 GF-UCT260 級（先端部外径 14.6mm・前方斜視の凸アレイ）に寄せた造形：
      ・先端硬性部は *まっすぐ*（硬いので自身は曲がらない）。軸 t1 に沿う丸角カプセル。
        （曲がるのは近位の軟性シャフト側＝path。先生「あり得ない角度で曲がる」の是正）
      ・その +Vp 面の遠位寄りに探触子（コンベックスアレイ）が凸に *出っ張り*、そこから扇が出る
        （ICE と同じく隆起で「ビームの出所」を明示・先生指示 2026-07-16）。
    返り値 dict:
      tip_outline = 先端硬性部の閉ポリゴン（丸角カプセル）
      transducer  = 探触子の凸面ポリゴン（+Vp側の隆起＝ビーム出射面）
      apex        = 扇の仮想頂点（凸面の奥 r0）
      sect        = 扇の広がり方向（=入力 sect）
    """
    t1 = nrm(np.asarray(t1, float))
    Vp = np.asarray(Vp, float); Vp = nrm(Vp - (Vp @ t1) * t1)      # 側射（面内・軸直交）に正規化
    sect = np.asarray(sect, float); sect = nrm(sect - (sect @ Vp) * Vp)  # 扇の広がり（≈t1）
    hw = tip_dia / 2.0
    B1 = np.asarray(Tp, float)                                     # 遠位端
    B0 = B1 - tip_len * t1                                         # 硬性部の付け根（近位）
    # --- 先端硬性部カプセル（-Vp背側=平ら / 遠位端=半円で丸め / +Vp腹側=平ら）---
    caps = [B0 - hw * Vp, B1 - hw * Vp]
    for k in range(arc_n + 1):                                     # 遠位端の半円（-Vp→遠位→+Vp）
        a = -np.pi / 2 + np.pi * k / arc_n
        caps.append(B1 + hw * (np.sin(a) * Vp + np.cos(a) * t1))
    caps.append(B0 + hw * Vp)
    tip_outline = np.array(caps)
    # --- 探触子（コンベックスアレイ）：+Vp面の遠位寄りに凸弧で出っ張らせる ---
    surf_c = B1 - 0.32 * tip_len * t1 + (hw + bump) * Vp           # 面より bump だけ突き出た探触子中心
    A = surf_c - r0 * Vp                                           # 扇の仮想頂点（凸面の奥 r0）
    phis = np.linspace(-fan_half, fan_half, 2 * arc_n + 1)
    arc = np.array([A + r0 * (np.cos(ph) * Vp + np.sin(ph) * sect) for ph in phis])   # 凸面（ビーム出射面）
    root0 = arc[0] - bump * 1.6 * Vp                               # 弧の両端を面側へ落として閉じる（隆起の根元）
    root1 = arc[-1] - bump * 1.6 * Vp
    transducer = np.vstack([arc, root1, root0])
    return dict(tip_outline=tip_outline, transducer=transducer, apex=A, sect=sect)


def echo_filter(img, depth_atten=1.4, speckle=0.32, seed=12345):
    """CTのreslice像(uint8)を『エコー風』に加工（厳密な音響シミュではなく見た目の近似）。
      ①境界を明るく（エコー＝界面反射）②深さ減衰（下＝深部ほど暗い）③スペックル（乗算ノイズ）④log圧縮。
    先生要望：CTをエコーのようにフィルタ（On/Off）。scipy が無い環境では境界強調を省いた簡易版で返す。"""
    f = np.asarray(img, np.float32) / 255.0
    try:
        from scipy import ndimage
        gy = ndimage.sobel(f, axis=0); gx = ndimage.sobel(f, axis=1)
        grad = np.hypot(gx, gy); grad = grad / (grad.max() + 1e-6)
    except Exception:
        grad = np.zeros_like(f)
    base = 0.35 * f + 0.9 * grad                              # 軟部の弱い散乱＋界面の強い反射
    H = f.shape[0]; depth = np.arange(H, dtype=np.float32)[:, None] / max(1, H)
    base = base * np.exp(-depth_atten * depth)               # 深さ減衰（下＝深部ほど暗い）
    rng = np.random.default_rng(seed)                         # 固定seed＝毎フレーム同じ（ちらつかない）
    base = base * np.clip(1.0 + speckle * rng.standard_normal(f.shape).astype(np.float32), 0.2, 2.0)
    out = np.log1p(6.0 * np.clip(base, 0.0, None)); out = out / (out.max() + 1e-6)
    return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def eus_needle_ray(Tp, t1, Vp, sect, elevator_deg, length=95.0, tip_len=38.0, tip_dia=14.6):
    """穿刺針の3D経路（実機EUS準拠）。針は *穿刺チャンネル遠位端＝探触子の近位（起上台の位置）* から出て、
    走査面(Vp,sect)内を、スコープ軸(sect)から起上台の lead-out 角だけビーム側(+Vp)へ持ち上げた方向に
    まっすぐ進む（co-planar 拘束＝面外自由度なし）。elevator_deg=起上台の出射角（面内・機種依存で概ね30–50°）。
    length=針の伸展長(mm, 最大~9cm)。返り値 dict(exit=出口, needle=[出口,先端], dir=単位方向, lead_deg)。"""
    t1 = nrm(np.asarray(t1, float))
    Vp = np.asarray(Vp, float); Vp = nrm(Vp - (Vp @ t1) * t1)          # 側射（面内・軸直交）
    sect = np.asarray(sect, float); sect = nrm(sect - (sect @ Vp) * Vp)  # 扇の広がり（≈軸・遠位向き）
    hw = tip_dia / 2.0
    B1 = np.asarray(Tp, float)
    exit_pt = B1 - 0.52 * tip_len * sect + 0.9 * hw * Vp               # +Vp面・探触子より少し近位＝起上台の出口
    lead = np.radians(float(elevator_deg))
    d = nrm(np.cos(lead) * sect + np.sin(lead) * Vp)                    # 軸(遠位)からビーム側へ lead 角持ち上げ（面内）
    tip = exit_pt + length * d
    return dict(exit=exit_pt, needle=np.array([exit_pt, tip]), dir=d, lead_deg=float(elevator_deg))


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


# ===== 経路パラメータ化の変種（術式プリセット PATH_PARAM で選択）=====
# "z": IVC のような体軸単調の芯線。位置 p はZスライス値（TIPS/ICE）。
# "s": 食道→胃→十二指腸のような z 非単調の管腔。位置 p は弧長mm・口側0（EUS）。
# 出力契約（bend_tip の dict キー）は両変種で同一。_path_frame のタプル構成は変種毎に異なる。

def _path_frame_z(path_pts, zP, sx, sy, dz):
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



def bend_tip_z(path_pts, zP, theta_deg, b1_deg, b2_deg, sx, sy, dz,
             tip_high_z=True, Lb=30.0, shaft_len=90.0):
    """先端30mm近位を支点に、A/P+L/R合成方向へ全角βの円弧で曲げる。
    shaft_len: 近位シャフトの最短全長(mm)。TIPS の外筒(cannula_len=90)に合わせてある。
    返り値 dict: F(支点), orange(F→先端のポリライン Nx3), Tp(曲がった先端), t1(先端接線)。"""
    fr = _path_frame_z(path_pts, zP, sx, sy, dz)       # 円環依存を断つ（ice_geometryを呼ばない）
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
    # 近位側をまっすぐ延長する。実機の ICE カテーテルは大腿静脈から入っており、シャフトは
    # 撮影範囲の外まで続いている。クリックした IVC パスの端で切ると数十mmの切り株になり、
    # 同じ画面に描かれる TIPS の外筒（Entry から頸静脈側へ 90mm）より短く見えてしまう。
    # 近位端の接線方向へ、全長が shaft_len に届くまで一直線に伸ばす。
    if len(shaft) >= 2:
        seg = np.linalg.norm(np.diff(np.asarray(shaft, float), axis=0), axis=1)
        have = float(seg.sum())
        u = nrm(np.asarray(shaft[0], float) - np.asarray(shaft[1], float))   # 近位向き
    else:
        have = 0.0
        u = nrm(F - distal[-2]) if md >= 2 else -td3                          # 点が足りない時は先端の逆向き
    if have < shaft_len and np.linalg.norm(u) > 1e-6:
        shaft = [np.asarray(shaft[0], float) + u * (shaft_len - have)] + list(shaft)
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



def _path_frame(path_pts, p, sx, sy, dz):
    """パス上の位置 p での頂点と接線。p の解釈は PATH_PARAM（術式プリセット）で切替。"""
    fn = _path_frame_s if PATH_PARAM == "s" else _path_frame_z
    return fn(path_pts, p, sx, sy, dz)


def bend_tip(path_pts, p, theta_deg, b1_deg, b2_deg, sx, sy, dz,
             tip_high_z=True, Lb=30.0, shaft_len=90.0):
    """偏向で曲がる先端（PATH_PARAM で _z / _s 変種へディスパッチ。出力の dict は同一契約）。"""
    fn = bend_tip_s if PATH_PARAM == "s" else bend_tip_z
    return fn(path_pts, p, theta_deg, b1_deg, b2_deg, sx, sy, dz,
              tip_high_z=tip_high_z, Lb=Lb, shaft_len=shaft_len)


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


def sector_shortfall(geom, P):
    """点 P が「実際に画面へ描かれる扇の領域」からどれだけ外れているか(mm)。0=扇の中。

    3点固定の旧実装は「無限に伸ばした画像平面に乗るか」しか見ていなかったため、
    数学上は残差ほぼ0でも、点が扇の深達(R)の先や扇角の外にあると絵に出てこない
    （2026-07-18 先生報告「二つはロックして見えるが Entry が考慮されていない」の真因）。
    ICE は頂点=Tp・r∈[0,R]、経腹コンベックスは仮想頂点=Tp−r0·Vp・r∈[r0,R] の環状扇。
    面内座標 (u=ビーム方向, w=側方) で扇領域へクランプした最近点までの距離を返す。"""
    Tp = np.asarray(geom["Tp"], float)
    Vp = nrm(np.asarray(geom["Vp"], float)); Sp = nrm(np.asarray(geom["Sp"], float))
    r0 = float(geom.get("r0", 0.0)); R = float(geom["R"]); fan = float(geom["fan_half"])
    apex = Tp - r0 * Vp                                    # ICE(r0=0)なら apex=Tp
    d = np.asarray(P, float) - apex
    u = float(d @ Vp); w = float(d @ Sp)                   # 面内成分だけで判定（面外は off_* が担当）
    r = float(np.hypot(u, w))
    phi = float(np.arctan2(w, u)) if r > 1e-9 else 0.0
    pc = min(max(phi, -fan), fan)                          # 扇角へクランプ
    rc = min(max(r, r0), R)                                # 深さ方向へクランプ
    return float(np.hypot(rc * np.cos(pc) - u, rc * np.sin(pc) - w))


def solve_theta_3points(path_pts, zP, b1_deg, b2_deg, sx, sy, dz, entry, target,
                        tip_high_z=True, coarse=4.0, span=6.0, fine=0.25):
    """Entry と Target が ICE 画像面にいちばんよく乗る θ(度) を、**真の幾何で**解く。

    ice_coplanarity() は現在の頂点 Tp と接線 Sp を固定したままビーム Vp だけを回す近似。
    偏向をかけていると θ は「どちら向きに曲げるか」そのものを変えるので、Tp も先端接線 t1 も動く。
    近似のままだと「言われたとおり θ を回したのに合わない」ことが起きるので、ここは候補 θ ごとに
    ice_geometry を作り直して評価する。粗く一周して最良点を掴み、その周りだけ細かく詰める。

    評価は「面外距離」＋「扇の見える範囲からのはみ出し(sector_shortfall)」（2026-07-18）。
    面に乗せるだけだと、点が扇の外（深達の先・扇角の外・背側）でも"解けた"ことになり、
    画面では Entry が無視されたように見えるため。

    自由度4（押し引き・θ・A/P偏向・L/R偏向）に対し拘束は2本（Entry と Target が面上）なので、
    θ だけでは一般に残差が残る。残差は呼び出し側が mm で表示する（＝ごまかさない）。

    返り値 dict(theta, off_entry, off_target, resid, vis_entry, vis_target)。無効なら None。
    """
    E = np.asarray(entry, float); T = np.asarray(target, float)

    def _off(th):
        g = ice_geometry(path_pts, zP, th, b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
        if g is None:
            return None
        Tp = np.asarray(g["Tp"], float)
        n = np.cross(nrm(g["Vp"]), nrm(g["Sp"]))          # 画像面の法線
        m = np.linalg.norm(n)
        if m < 1e-9:
            return None
        n = n / m
        return (float((E - Tp) @ n), float((T - Tp) @ n),
                sector_shortfall(g, E), sector_shortfall(g, T))

    def _score(o):
        return max(abs(o[0]), abs(o[1])) + o[2] + o[3]

    best = None
    th = 0.0
    while th < 360.0:                                      # 粗く一周
        o = _off(th)
        if o is not None:
            r = _score(o)
            if best is None or r < best[0]:
                best = (r, th, o)
        th += coarse
    if best is None:
        return None
    lo, hi = best[1] - span, best[1] + span                # 最良点の周りだけ細かく
    th = lo
    while th <= hi:
        o = _off(th)
        if o is not None:
            r = _score(o)
            if r < best[0]:
                best = (r, th, o)
        th += fine
    o = best[2]
    return dict(theta=best[1] % 360.0, off_entry=o[0], off_target=o[1],
                resid=max(abs(o[0]), abs(o[1])), vis_entry=o[2], vis_target=o[3])


def aim_beam_at_target(path_pts, zP, b1_deg, b2_deg, sx, sy, dz, target,
                       tip_high_z=True, coarse=3.0, span=5.0, fine=0.2):
    """扇の *中心軸(ビーム Vp)* を Target に向ける θ(度) を解く（＝探触子からTargetへエコーを合わせる）。
    solve_theta_3points は「Targetが画像"面"に乗る」θ（面内のどこでも可＝扇の端でもよい）だが、
    こちらは「ビームの中心が Target を指す」＝ Vp と (Target−探触子) 方向の一致度(内積)を最大化する。
    co-planar 拘束で Vp は軸に直交な面内でしか回らないので、Targetの軸方向ずれは残る（それは
    プローブ位置＝center_arclength で詰める）。返り値 dict(theta, align) or None。"""
    T = np.asarray(target, float)

    def _align(th):
        g = ice_geometry(path_pts, zP, th, b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
        if g is None:
            return None
        Tp = np.asarray(g["Tp"], float); Vp = nrm(np.asarray(g["Vp"], float))
        d = T - Tp; n = np.linalg.norm(d)
        if n < 1e-6:
            return None
        return float((d / n) @ Vp)                       # 1=ビーム正面にTarget / -1=真後ろ

    best = None; th = 0.0
    while th < 360.0:                                     # 粗く一周して最良を掴む
        a = _align(th)
        if a is not None and (best is None or a > best[0]):
            best = (a, th)
        th += coarse
    if best is None:
        return None
    lo = best[1] - span; th = lo                         # 最良点の周りを細かく
    while th <= best[1] + span:
        a = _align(th)
        if a is not None and a > best[0]:
            best = (a, th)
        th += fine
    # 解いた θ での軸方向ズレ（Targetが扇の中心からどれだけ横にずれているか＝θでは消せない分）
    g = ice_geometry(path_pts, zP, best[1], b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
    lateral = depth = 0.0
    if g is not None:
        Tp = np.asarray(g["Tp"], float); Vp = nrm(np.asarray(g["Vp"], float)); Sp = nrm(np.asarray(g["Sp"], float))
        d = T - Tp; lateral = abs(float(d @ Sp)); depth = float(d @ Vp)
    return dict(theta=best[1] % 360.0, align=best[0], lateral=lateral, depth=depth)


def aim_needle_at_target(path_pts, zP, b1_deg, b2_deg, elevator_deg, sx, sy, dz, target,
                         needle_len=95.0, tip_high_z=True, coarse=3.0, span=5.0, fine=0.2):
    """穿刺針の走行線が Target を通る θ を解く（＝針を Target に当て続ける／針追尾モード）。
    各θで先端グリフ→針レイ(eus_needle_ray)を作り、Target から針レイへの距離を最小化する。
    返り値 dict(theta, miss=Targetと針線の最短距離mm)。無効なら None。"""
    T = np.asarray(target, float)

    def _miss(th):
        g = ice_geometry(path_pts, zP, th, b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
        if g is None:
            return None
        Tp = np.asarray(g["Tp"], float); Vp = nrm(g["Vp"]); Sp = nrm(g["Sp"])   # Sp=先端接線≈軸/扇の広がり
        nr = eus_needle_ray(Tp, Sp, Vp, Sp, elevator_deg, length=needle_len)
        e = np.asarray(nr["exit"], float); d = nrm(np.asarray(nr["dir"], float))
        w = T - e; t = max(0.0, float(w @ d))                                   # 針は前方(t>=0)のみ
        return float(np.linalg.norm(w - t * d))

    best = None; th = 0.0
    while th < 360.0:
        m = _miss(th)
        if m is not None and (best is None or m < best[0]):
            best = (m, th)
        th += coarse
    if best is None:
        return None
    lo = best[1] - span; th = lo
    while th <= best[1] + span:
        m = _miss(th)
        if m is not None and m < best[0]:
            best = (m, th)
        th += fine
    return dict(theta=best[1] % 360.0, miss=best[0])


def center_arclength(path_pts, target, sx, sy, dz):
    """腫瘤(target world[x,y,z])が扇の *側方中心*（スコープ軸に直交＝EUS画像の真ん中）に来る
    プローブ弧長位置 sP(mm, 口側=0) を返す。深達 R_DEPTH 以内で、側方ズレ |(T-P)·軸接線| が
    最小の弧長点を選ぶ（＝腫瘤を扇の正面に据える）。パス不足なら None。"""
    fr = _path_frame_s(path_pts, 0.0, sx, sy, dz)
    if fr is None:
        return None
    _, _, s, Wp, _, L, _ = fr
    T = np.asarray(target, float)

    def _at(sq):
        sq = min(L, max(0.0, sq))
        return np.array([np.interp(sq, s, Wp[:, 0]), np.interp(sq, s, Wp[:, 1]), np.interp(sq, s, Wp[:, 2])])

    def _tan(sq):
        return nrm(_at(min(L, sq + 2.0)) - _at(max(0.0, sq - 2.0)))

    best = None
    for sq in np.linspace(0.0, L, max(2, int(L / 2.0) + 1)):   # 2mm刻みでスキャン
        P = _at(sq); Sp = _tan(sq); d = T - P
        dist = float(np.linalg.norm(d))
        if dist < 3.0 or dist > R_DEPTH:                       # 近すぎ/深達外は不可
            continue
        lat = abs(float(d @ Sp))                               # 側方(軸方向成分)=扇中心からのズレ
        if best is None or lat < best[0]:
            best = (lat, float(sq))
    return None if best is None else best[1]


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


def best_surface_theta(contact, inward_normal, entry, target, plane_axis=(0.0, 0.0, 1.0), step_deg=0.5):
    """経腹プローブの扇平面（接触点を頂点・法線 n0 を含む）が Entry/Target を最もよく含む θ(度)。
      tilt=rock=0 のとき扇平面は {n0, u(θ)} で張られ、θ は u を n0 まわりに回す。
      3点（接触点＝頂点は常に面内）＋Entry＋Target が同一断面に乗る初期表示に使う（先生要望2026-07-14）。
      plane_axis は surface_geometry と同じもの（＝u0 の基準）を渡すこと。返り値: θ(度)。"""
    C = np.asarray(contact, float)
    n0 = nrm(np.asarray(inward_normal, float))
    if np.linalg.norm(n0) < 1e-6:
        n0 = np.array([0.0, 1.0, 0.0])
    E = np.asarray(entry, float) - C
    T = np.asarray(target, float) - C
    pa = nrm(np.asarray(plane_axis, float))
    u0 = np.cross(n0, pa)
    if np.linalg.norm(u0) < 1e-6:
        u0 = np.cross(n0, [1.0, 0.0, 0.0])
        if np.linalg.norm(u0) < 1e-6:
            u0 = np.cross(n0, [0.0, 1.0, 0.0])
    u0 = nrm(u0)
    best_th, best_off = 0.0, 1e18
    m = int(round(360.0 / step_deg))
    for i in range(m):
        u = rot3(u0, n0, np.radians(i * step_deg))
        w = np.cross(n0, u); nw = np.linalg.norm(w)
        if nw < 1e-6:
            continue
        w = w / nw                                          # 扇平面の法線
        off = max(abs(float(E @ w)), abs(float(T @ w)))     # Entry/Target の面外ズレ(mm)
        if off < best_off:
            best_off, best_th = off, i * step_deg
    return best_th


def _path_pos_range(path_pts, sx, sy, dz):
    """プローブ位置パラメータの有効範囲 (lo, hi)。PATH_PARAM="z" は Zスライス値、"s" は弧長mm。"""
    fr = _path_frame(path_pts, 0.0, sx, sy, dz)
    if fr is None:
        return None
    return (float(fr[4]), float(fr[5])) if PATH_PARAM == "s" else (float(fr[5]), float(fr[6]))


def solve_theta_pos_3points(path_pts, pos0, b1_deg, b2_deg, sx, sy, dz, entry, target,
                            tip_high_z=True):
    """3点固定の「乗せ直し」完全版：θ(回転) と プローブ位置(押し引き) を同時に解き、
    Entry / Target を扇平面へ乗せ切る（未知数2=拘束2なので一般に残差ほぼ0）。

    旧 solve_theta_3points は θ 単独＝原理的に残差が残る設計（押し引きは手で詰める）だったが、
    2026-07-18 先生要望「3点固定なのに面に乗らない」を受けて位置も解く本関数を追加。
    全域探索のため連続追従（ドラッグ中の毎フレーム）には使わず、ON 時と Entry/Target 変更時に呼ぶ。
    同率最適が並ぶ場合は現在位置 pos0 に最も近い解（プローブを無闇に飛ばさない）。
    評価は面外距離＋扇はみ出し(sector_shortfall)＝「面に乗る」だけでなく「絵の中に入る」（2026-07-18）。
    返り値 dict(theta, pos, off_entry, off_target, resid, vis_entry, vis_target)。無効なら None。"""
    rng = _path_pos_range(path_pts, sx, sy, dz)
    if rng is None:
        return None
    lo, hi = rng
    if hi - lo < 1e-6:
        s = solve_theta_3points(path_pts, pos0, b1_deg, b2_deg, sx, sy, dz, entry, target,
                                tip_high_z=tip_high_z)
        if s is None:
            return None
        s = dict(s); s["pos"] = float(pos0)
        return s
    E = np.asarray(entry, float); T = np.asarray(target, float)

    def _score(th, pos):
        g = ice_geometry(path_pts, pos, th, b1_deg, b2_deg, sx, sy, dz, tip_high_z=tip_high_z)
        if g is None:
            return None
        Tp = np.asarray(g["Tp"], float)
        n = np.cross(nrm(g["Vp"]), nrm(g["Sp"]))
        m = np.linalg.norm(n)
        if m < 1e-9:
            return None
        n = n / m
        oe = float((E - Tp) @ n); ot = float((T - Tp) @ n)
        vE = sector_shortfall(g, E); vT = sector_shortfall(g, T)
        return max(abs(oe), abs(ot)) + vE + vT, (oe, ot, vE, vT)

    best = None                    # (選好キー, resid生, θ, pos, (oe,ot))  キー=(residを0.01mm丸め, |pos-pos0|)
    npos = 31
    for i in range(npos):          # 粗い格子（位置 × θ 4°）
        pos = lo + (hi - lo) * i / (npos - 1)
        th = 0.0
        while th < 360.0:
            sc = _score(th, pos)
            if sc is not None:
                key = (round(sc[0], 2), abs(pos - pos0))
                if best is None or key < best[0]:
                    best = (key, sc[0], th, pos, sc[1])
            th += 4.0
    if best is None:
        return None
    dpos = (hi - lo) / (npos - 1)
    for _ in range(3):             # θ→位置 の交互詰め（最良点の近傍のみ・谷が曲がっていても追える回数）
        th0c, pos_c = best[2], best[3]
        th = th0c - 5.0
        while th <= th0c + 5.0 + 1e-9:
            sc = _score(th % 360.0, pos_c)
            if sc is not None:
                key = (round(sc[0], 2), abs(pos_c - pos0))
                if key < best[0]:
                    best = (key, sc[0], th % 360.0, pos_c, sc[1])
            th += 0.25
        th_c = best[2]
        step = max((hi - lo) / 600.0, 5e-4)
        pos = max(lo, best[3] - 2.0 * dpos)
        while pos <= min(hi, best[3] + 2.0 * dpos) + 1e-9:
            sc = _score(th_c, pos)
            if sc is not None:
                key = (round(sc[0], 2), abs(pos - pos0))
                if key < best[0]:
                    best = (key, sc[0], th_c, float(pos), sc[1])
            pos += step
    oe, ot, vE, vT = best[4]
    return dict(theta=float(best[2] % 360.0), pos=float(best[3]),
                off_entry=float(oe), off_target=float(ot),
                resid=float(max(abs(oe), abs(ot))), vis_entry=float(vE), vis_target=float(vT))


def solve_surface_3points2(contact, inward_normal, tilt0, rock_deg, entry, target,
                           plane_axis, sx, sy, dz, tilt_lo=-80.0, tilt_hi=80.0):
    """経腹3点固定の「乗せ直し」完全版：回転θ と あおり(tilt) を同時に解き Entry/Target を扇平面へ。
    首振り(rock)は術者の手に残す。同率なら現在の tilt0 に近い解（プローブ姿勢を無闇に変えない）。
    連続追従には従来の solve_surface_3points（θ単独）を使い、本関数は ON 時と点変更時のみ。
    返り値 dict(theta, tilt, off_entry, off_target, resid)。"""
    C = np.asarray(contact, float)
    E = np.asarray(entry, float); T = np.asarray(target, float)

    def _score(th, ti):
        g = surface_geometry(contact, inward_normal, th, ti, rock_deg, sx, sy, dz, plane_axis=plane_axis)
        if g is None:
            return None
        n = np.cross(g["Vp"], g["Sp"]); m = np.linalg.norm(n)
        if m < 1e-6:
            return None
        n = n / m
        oe = float((E - C) @ n); ot = float((T - C) @ n)
        vE = sector_shortfall(g, E); vT = sector_shortfall(g, T)   # 扇の絵に入ることも要求（2026-07-18）
        return max(abs(oe), abs(ot)) + vE + vT, (oe, ot, vE, vT)

    best = None
    ti = tilt_lo
    while ti <= tilt_hi + 1e-9:
        th = 0.0
        while th < 360.0:
            sc = _score(th, ti)
            if sc is not None:
                key = (round(sc[0], 2), abs(ti - tilt0))
                if best is None or key < best[0]:
                    best = (key, sc[0], th, ti, sc[1])
            th += 6.0
        ti += 6.0
    if best is None:
        return dict(theta=0.0, tilt=float(tilt0), off_entry=0.0, off_target=0.0, resid=0.0,
                    vis_entry=0.0, vis_target=0.0)
    th0c, ti0c = best[2], best[3]
    dth = -6.0
    while dth <= 6.0 + 1e-9:
        dti = -6.0
        while dti <= 6.0 + 1e-9:
            t2 = min(tilt_hi, max(tilt_lo, ti0c + dti))
            sc = _score((th0c + dth) % 360.0, t2)
            if sc is not None:
                key = (round(sc[0], 2), abs(t2 - tilt0))
                if key < best[0]:
                    best = (key, sc[0], (th0c + dth) % 360.0, t2, sc[1])
            dti += 1.0
        dth += 0.5
    oe, ot, vE, vT = best[4]
    return dict(theta=float(best[2]), tilt=float(best[3]),
                off_entry=float(oe), off_target=float(ot),
                resid=float(max(abs(oe), abs(ot))), vis_entry=float(vE), vis_target=float(vT))


def solve_surface_3points(contact, inward_normal, tilt_deg, rock_deg, entry, target,
                          plane_axis, sx, sy, dz, step_deg=1.0):
    """経腹プローブの3点固定＝接触点(扇の頂点)＋Entry＋Target が最もよく乗るように回転θを解く。
      現在の傾き(tilt)/あおり(rock)はそのまま使う（＝ICEの偏向と同じく先生の手に残す）。
      各θで surface_geometry を作り直し、扇平面(法線=Vp×Sp)からの Entry/Target の面外距離を最小化。
      返り値 dict(theta, off_entry, off_target, resid) — ICEの solve_theta_3points と同じキー。"""
    C = np.asarray(contact, float)
    E = np.asarray(entry, float); T = np.asarray(target, float)
    best_th, best_score, best_o = 0.0, 1e18, None
    m = max(1, int(round(360.0 / step_deg)))
    for i in range(m):
        th = i * step_deg
        g = surface_geometry(contact, inward_normal, th, tilt_deg, rock_deg, sx, sy, dz, plane_axis=plane_axis)
        n = np.cross(g["Vp"], g["Sp"]); nn = np.linalg.norm(n)  # 扇平面の法線
        if nn < 1e-6:
            continue
        n = n / nn
        oe = float((E - C) @ n); ot = float((T - C) @ n)
        vE = sector_shortfall(g, E); vT = sector_shortfall(g, T)   # 扇の絵に入ることも要求（2026-07-18）
        score = max(abs(oe), abs(ot)) + vE + vT
        if score < best_score:
            best_score, best_th, best_o = score, th, (oe, ot, vE, vT)
    if best_o is None:
        return dict(theta=0.0, off_entry=0.0, off_target=0.0, resid=0.0, vis_entry=0.0, vis_target=0.0)
    oe, ot, vE, vT = best_o
    return dict(theta=best_th, off_entry=oe, off_target=ot, resid=max(abs(oe), abs(ot)),
                vis_entry=vE, vis_target=vT)


def catmull_rom(pts, n=12):
    """点列を通る滑らかな曲線（Catmull-Rom スプライン）。各区間を n 分割した密な点列を返す。
    手描き肝静脈を『終了』したとき、カクカクの折れ線＋節点を、なだらかな血管曲線にし直すのに使う。
    点が2つ以下ならそのまま返す。"""
    P = np.asarray(pts, float)
    if len(P) < 3:
        return P
    ext = np.vstack([P[0], P, P[-1]])                      # 端点を複製して端まで通す
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for t in np.linspace(0.0, 1.0, n, endpoint=False):
            t2 = t * t; t3 = t2 * t
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t
                              + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-1])
    return np.asarray(out, float)


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


def probe_glyph(geom, housing_mm=40.0, n=18):
    """コンベックス・プローブの外形（world 3D点・撮像面内）。実機の探触子＆下部モックに寄せた造形：
      face   = 皮膚に当たる凸面の弧（アレイ表面）
      array  = 青いアレイ帯（薄い三日月・塗り）
      outline= 白い筐体の閉ポリゴン（肩で広がり首で細く丸い頂部・体外側 -Vp へ）
      button = 前面の操作ボタン点
      housing= 互換用の簡易4点。UIで掴む・描くのに使う。"""
    Tp = np.asarray(geom["Tp"], float); Vp = np.asarray(geom["Vp"], float); Sp = np.asarray(geom["Sp"], float)
    r0 = float(geom.get("r0", 0.0)); fan = float(geom["fan_half"])
    rr = r0 if r0 > 1e-3 else 8.0                          # ICE(r0=0)でも小さな凸面に
    A = Tp - rr * Vp                                       # 仮想頂点（皮膚の外側）
    phis = np.linspace(-fan, fan, n)
    face = np.array([A + rr * (np.cos(ph) * Vp + np.sin(ph) * Sp) for ph in phis])       # アレイ凸面(皮膚)
    inner = np.array([A + (rr - 3.0) * (np.cos(ph) * Vp + np.sin(ph) * Sp) for ph in phis])
    array = np.vstack([face, inner[::-1]])                 # 青いアレイ帯（薄い三日月）
    half = rr * np.sin(fan); dch = rr * np.cos(fan)        # 面の側方半幅・弦の深さ

    def P(d, lat):                                         # (Vp方向の深さ d, 側方 lat) → world 3D
        return A + d * Vp + lat * Sp
    hm = housing_mm
    outline = np.array([                                   # 体外側 -Vp（=深さを弦から減らす方向）へ立ち上がる筐体
        face[0], P(dch - 5, -half * 1.06), P(dch - hm * 0.5, -half * 0.92),
        P(dch - hm * 0.82, -half * 0.5), P(dch - hm, -half * 0.18),
        P(dch - hm, half * 0.18), P(dch - hm * 0.82, half * 0.5),
        P(dch - hm * 0.5, half * 0.92), P(dch - 5, half * 1.06), face[-1],
    ])
    button = P(dch - hm * 0.45, 0.0)
    housing = np.array([face[0], face[-1], P(dch - hm, half * 0.18), P(dch - hm, -half * 0.18)])
    return dict(face=face, array=array, outline=outline, button=button, housing=housing)


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
