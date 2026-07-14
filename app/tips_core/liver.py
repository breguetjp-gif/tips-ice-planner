"""TIPS Planner — 肝臓ラフ抽出 & 3Dゴースト描画（OS非依存・numpyのみ）。

造影CTボリュームから肝臓の「大まかな立体」を粗抽出し、3Dパネルに
淡いゴースト（サーフェス陰影 / 半透明もや）として重ねるための計算核。

  - 抽出 estimate() : HU適応窓 + IVCパス起点の領域成長（純numpy・scipy不要）
  - 描画 render_ghost() : 点群を Pane3D._proj と同一の az/el で投影し
                          RGBA バッファ化（Qt非依存・テスト可能）

座標系は geometry.py と一致: 画像index (x=col, y=row, z=slice) → mm は (x*sx, y*sy, z*dz)。
path 点は [z, y, x]（geometry._path_frame と同じ並び）。

注意: これは研究・教育用の **粗い位置目安**。境界は造影相・肝硬変・腹水でしばしばはみ出す。
医療機器ではなく、区域名・体積・推奨穿刺路は提示しない（faint=薄く・裏に・ON/OFF前提）。
"""
from __future__ import annotations
import numpy as np

TERRA = (240, 143, 105)          # ゴースト色（テラコッタ）

# ---- 形態素（6近傍=3D / 4近傍=面内のみ）。すべて bool 配列の shift OR/AND ----
def _dil6(o):
    a = o.copy()
    a[1:] |= o[:-1]; a[:-1] |= o[1:]
    a[:, 1:] |= o[:, :-1]; a[:, :-1] |= o[:, 1:]
    a[:, :, 1:] |= o[:, :, :-1]; a[:, :, :-1] |= o[:, :, 1:]
    return a


def _ero6(o):
    a = o.copy()
    a[1:] &= o[:-1]; a[:-1] &= o[1:]
    a[:, 1:] &= o[:, :-1]; a[:, :-1] &= o[:, 1:]
    a[:, :, 1:] &= o[:, :, :-1]; a[:, :, :-1] &= o[:, :, 1:]
    return a


def _dil4(o):                      # 面内（y,x）だけ
    a = o.copy()
    a[:, 1:] |= o[:, :-1]; a[:, :-1] |= o[:, 1:]
    a[:, :, 1:] |= o[:, :, :-1]; a[:, :, :-1] |= o[:, :, 1:]
    return a


def _ero4(o):
    a = o.copy()
    a[:, 1:] &= o[:, :-1]; a[:, :-1] &= o[:, 1:]
    a[:, :, 1:] &= o[:, :, :-1]; a[:, :, :-1] &= o[:, :, 1:]
    return a


def _grow(seed, mask, max_iter=250):
    """形態的再構成: seed を mask 内で連結成分まで膨張（領域成長）。"""
    g = seed & mask
    for _ in range(max_iter):
        ng = _dil6(g) & mask
        if ng.sum() == g.sum():
            break
        g = ng
    return g


def _fill_inplane_holes(m, max_iter=200):
    """面内（軸位）で囲まれた穴を埋める＝肝内の縦走血管断面を充填。"""
    comp = ~m
    r = np.zeros_like(comp)
    r[:, 0] |= comp[:, 0]; r[:, -1] |= comp[:, -1]
    r[:, :, 0] |= comp[:, :, 0]; r[:, :, -1] |= comp[:, :, -1]
    for _ in range(max_iter):
        nr = _dil4(r) & comp
        if nr.sum() == r.sum():
            break
        r = nr
    return m | (comp & ~r)


def _surface_points(m, dsx, dsy, ddz, cap=20000, seed=0):
    """マスク表面ボクセル → mm 点群と外向き法線（平滑占有率の勾配）。"""
    occ = m.astype(np.float32)
    for _ in range(3):             # 6近傍平均で平滑化（法線を滑らかに）
        occ = (occ + np.roll(occ, 1, 0) + np.roll(occ, -1, 0)
               + np.roll(occ, 1, 1) + np.roll(occ, -1, 1)
               + np.roll(occ, 1, 2) + np.roll(occ, -1, 2)) / 7.0
    surf = m & ~_ero6(m)
    zi, yi, xi = np.where(surf)
    if len(zi) == 0:
        return None, None
    gz, gy, gx = np.gradient(occ)
    nl = np.stack([gx[zi, yi, xi], gy[zi, yi, xi], gz[zi, yi, xi]], 1)
    nrm = np.linalg.norm(nl, axis=1, keepdims=True); nrm[nrm < 1e-6] = 1.0
    nl = (-nl / nrm).astype(np.float32)                  # 勾配は内向き→反転で外向き
    pts = np.column_stack([xi * dsx, yi * dsy, zi * ddz]).astype(np.float32)
    if len(pts) > cap:
        idx = np.random.RandomState(seed).choice(len(pts), cap, replace=False)
        pts, nl = pts[idx], nl[idx]
    return pts, nl


def _interior_points(m, dsx, dsy, ddz, cap=28000, seed=1):
    """マスク内部ボクセル → mm 点群（もや＝半透明累積用）。"""
    zi, yi, xi = np.where(m)
    if len(zi) == 0:
        return None
    pts = np.column_stack([xi * dsx, yi * dsy, zi * ddz]).astype(np.float32)
    if len(pts) > cap:
        idx = np.random.RandomState(seed).choice(len(pts), cap, replace=False)
        pts = pts[idx]
    return pts


def _pack(m, dsx, dsy, ddz):
    surf, nrm = _surface_points(m, dsx, dsy, ddz)
    if surf is None:
        return None
    inter = _interior_points(m, dsx, dsy, ddz)
    liters = float(m.sum()) * dsx * dsy * ddz / 1e6
    return dict(surf=surf, nrm=nrm, interior=inter,
                center=surf.mean(0).astype(np.float32), liters=liters)


def body_surface(hu, sx, sy, dz, air=-300.0, ds=(1, 4, 4), cap=45000):
    """CT全体の外郭(皮膚)を粗く抽出。経腹エコーの3D表示＆プローブ設置面に使う。
      返り値 dict(surf,nrm(外向き),interior,center,extent) or None。診断用途ではない粗いシェル。"""
    fz, fy, fx = ds
    d = np.ascontiguousarray(hu[::fz, ::fy, ::fx]).astype(np.float32)
    m = d > air
    if int(m.sum()) < 100:
        return None
    m = _fill_inplane_holes(m)                     # 肺/腸管などの内部空気を埋め、外郭(皮膚)だけ残す
    dsx, dsy, ddz = sx * fx, sy * fy, dz * fz
    surf, nl = _surface_points(m, dsx, dsy, ddz, cap=cap, seed=3)
    if surf is None:
        return None
    ext = float(np.linalg.norm(surf.max(0) - surf.min(0)))
    return dict(surf=surf, nrm=nl, interior=surf,
                center=surf.mean(0).astype(np.float32), extent=ext)


def estimate(hu, path, sx, sy, dz, tip_high_z=True, ds=(2, 4, 4)):
    """造影CT(HU) + IVCパス起点 → 肝臓ラフ抽出。返り値 dict（surf/nrm/interior/center/liters）or None。

    path 無し/不足のときは estimate_pathfree() にフォールバック。
    """
    if path is None or len(path) < 2:
        return estimate_pathfree(hu, sx, sy, dz, ds=ds)
    fz, fy, fx = ds
    d = np.ascontiguousarray(hu[::fz, ::fy, ::fx]).astype(np.float32)
    dnz = d.shape[0]
    dsx, dsy, ddz = sx * fx, sy * fy, dz * fz
    P = np.asarray(path, float)
    P = P[np.argsort(P[:, 0])]
    ph = P[len(P) // 2:]                                 # 先端寄り半分（肝実質内が確実）
    rb = max(2, int(round(12 / dsy)))
    boxes = []
    for z, y, x in ph:
        zz, yy, xx = int(round(z / fz)), int(round(y / fy)), int(round(x / fx))
        boxes.append(d[max(zz - 1, 0):zz + 2, max(yy - rb, 0):yy + rb,
                       max(xx - rb, 0):xx + rb].ravel())
    a = np.concatenate(boxes) if boxes else d.ravel()
    par = a[(a > -50) & (a < 300)]
    mu = float(np.median(par)) if par.size else 100.0    # 肝実質HUの推定
    mask = (d >= mu - 35) & (d <= mu + 55)
    # 先端側 z を造影された心臓/IVC流入へ漏らさぬよう頭側を制限・足側は広めに許容
    ztip = int(round(P[-1, 0] / fz)); zbot = int(round(P[0, 0] / fz))
    zlo = max(0, zbot - int(round(45 / ddz))); zhi = min(dnz, ztip + int(round(25 / ddz)))
    mask[:zlo] = False; mask[zhi:] = False
    seed = np.zeros(d.shape, bool)
    rb2 = max(1, int(round(8 / dsy)))
    for z, y, x in ph:
        zz, yy, xx = int(round(z / fz)), int(round(y / fy)), int(round(x / fx))
        seed[max(zz - 1, 0):zz + 2, max(yy - rb2, 0):yy + rb2, max(xx - rb2, 0):xx + rb2] = True
    seed &= mask
    if not seed.any():
        return None
    m = _grow(seed, mask)
    m = _ero6(_dil6(_dil6(m)))                            # 3D クローズ
    m = _dil6(_ero6(m))                                   # 軽くオープン（薄い橋/筋の漏れ除去）
    m = _fill_inplane_holes(m)
    m = _ero4(_dil4(_dil4(m)))                            # 面内平滑
    return _pack(m, dsx, dsy, ddz)


def estimate_pathfree(hu, sx, sy, dz, ds=(2, 4, 4)):
    """パス未入力でも読み込み時に出せる暫定肝臓: 上腹部で最大の充実軟部塊を採る。"""
    fz, fy, fx = ds
    d = np.ascontiguousarray(hu[::fz, ::fy, ::fx]).astype(np.float32)
    dnz = d.shape[0]
    dsx, dsy, ddz = sx * fx, sy * fy, dz * fz
    band = (d >= 40) & (d <= 180)                        # 非造影〜造影肝を広めに
    # 強く収縮して体壁の薄いリングを落とす → 最大塊の核を seed に
    core = _ero6(_ero6(_ero6(band)))
    if not core.any():
        return None
    zi, yi, xi = np.where(core)
    # z 重心が上腹部に寄るよう、存在域の上側 70% に限定
    zc = np.median(zi)
    keep = zi >= (zi.min() + 0.0)
    seed = np.zeros(d.shape, bool); seed[zi[keep], yi[keep], xi[keep]] = True
    grown = _grow(seed, band)
    # 連結成分ごとに体積を測り最大を採用（領域成長は seed の全連結→既に最大寄りだが保険）
    m = grown
    m = _ero6(_dil6(_dil6(m))); m = _dil6(_ero6(m))
    m = _fill_inplane_holes(m); m = _ero4(_dil4(_dil4(m)))
    res = _pack(m, dsx, dsy, ddz)
    if res is not None:
        res["pathfree"] = True
    return res


# ===== 3D ゴースト描画（Qt非依存・RGBA を返す。Pane3D が QImage 化）=====
def _project(pts, az_deg, el_deg, center, scale, w, h, offset=(0.0, 0.0)):
    """Pane3D._proj と同一式で点群を画面座標へ。returns (u,v int, depth float)。
    offset: 画面ピクセルの平行移動（Pane3D のズーム時パン pan3d と整合させるため）。"""
    a, e = np.radians(az_deg), np.radians(el_deg)
    x = pts[:, 0] - center[0]; y = pts[:, 1] - center[1]; z = pts[:, 2] - center[2]
    X = np.cos(a) * x - np.sin(a) * y
    Y = np.sin(a) * x + np.cos(a) * y
    Y2 = np.cos(e) * Y - np.sin(e) * z
    depth = np.sin(e) * Y + np.cos(e) * z                # 視線方向: 大きいほど手前
    u = (w / 2.0 + X * scale + offset[0]).astype(np.int64)
    v = (h / 2.0 - Y2 * scale + offset[1]).astype(np.int64)
    return u, v, depth


def _box_blur(a, r=2):
    """分離型ボックスぼかし（numpyのみ）。a: float2D。"""
    if r < 1:
        return a
    k = 2 * r + 1
    c = np.cumsum(np.insert(a, 0, 0, axis=1), axis=1)
    a = (c[:, k:] - c[:, :-k]) / k
    a = np.pad(a, ((0, 0), (r, r)), mode="edge")
    c = np.cumsum(np.insert(a, 0, 0, axis=0), axis=0)
    a = (c[k:, :] - c[:-k, :]) / k
    a = np.pad(a, ((r, r), (0, 0)), mode="edge")
    return a


def render_ghost(liver, az_deg, el_deg, center, scale, w, h,
                 mode="haze", opacity=0.5, color=TERRA, offset=(0.0, 0.0), splat_rad=2):
    """肝臓ゴーストを (h,w,4) uint8 RGBA(非乗算) で返す。Pane3D が drawImage で裏に敷く。

      mode='surface' : Zバッファ + 法線シェーディングの塗り（立体の影）
      mode='haze'    : 内部点の加算累積 + ぼかし（もや・半透明）
    center/scale は Pane3D が device 幾何で使う apex / s をそのまま渡す（位置整合のため）。
    splat_rad: surfaceモードの点1つあたりの塗り半径(px)。点群が疎い対象(体表シェル等)は大きめにして隙間を防ぐ。
    """
    if liver is None or w < 4 or h < 4:
        return None
    out = np.zeros((h, w, 4), np.uint8)
    col = np.array(color, np.float32)
    if mode == "surface":
        pts, nrm = liver.get("surf"), liver.get("nrm")
        if pts is None:
            return None
        u, v, depth = _project(pts, az_deg, el_deg, center, scale, w, h, offset)
        light = np.array([0.4, -0.5, 0.75]); light /= np.linalg.norm(light)
        shade = np.clip(nrm @ light, 0, 1) * 0.75 + 0.25
        rad = max(1, int(splat_rad))
        oy, ox = np.mgrid[-rad:rad + 1, -rad:rad + 1]
        offs = [(int(dy), int(dx)) for dy, dx in zip(oy.ravel(), ox.ravel())]
        # 遠い順に書く（後勝ち＝手前が上書き）。スプラットは近傍へ複製。
        order = np.argsort(depth)
        zbuf = np.full((h, w), -1e18, np.float32)
        au = u[order]; av = v[order]; ad = depth[order]; ash = shade[order]
        a8 = int(np.clip(opacity, 0, 1) * 255)
        for dy, dx in offs:
            uu = au + dx; vv = av + dy
            ok = (uu >= 0) & (uu < w) & (vv >= 0) & (vv < h)
            ii = vv[ok] * w + uu[ok]
            dd = ad[ok]
            # フラット index に対し「より手前(depth大)なら更新」をベクトル化
            flat_z = zbuf.ravel()
            better = dd > flat_z[ii]
            ii2 = ii[better]
            flat_z[ii2] = dd[better]
            sh = ash[ok][better]
            rgb = (col[None, :] * sh[:, None]).astype(np.uint8)
            fo = out.reshape(-1, 4)
            fo[ii2, 0] = rgb[:, 0]; fo[ii2, 1] = rgb[:, 1]; fo[ii2, 2] = rgb[:, 2]
            fo[ii2, 3] = a8
        return out
    # ---- haze ----
    pts = liver.get("interior")
    if pts is None:
        pts = liver.get("surf")
    if pts is None:
        return None
    u, v, _ = _project(pts, az_deg, el_deg, center, scale, w, h, offset)
    ok = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    acc = np.zeros((h, w), np.float32)
    np.add.at(acc, (v[ok], u[ok]), 1.0)
    acc = _box_blur(acc, r=5)
    if acc.max() > 1e-6:
        acc = acc / np.percentile(acc[acc > 0], 88)      # 上位値で正規化（飽和を防ぐ）
    alpha = np.clip(acc, 0, 1) * float(np.clip(opacity, 0, 1))
    out[..., 0] = color[0]; out[..., 1] = color[1]; out[..., 2] = color[2]
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return out
