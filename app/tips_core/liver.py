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


def _coarsen_any(m, c):
    """各軸 c ボクセルのブロックを OR で粗格子化（前景を薄く消さないため any 集約）。"""
    z, y, x = m.shape
    mp = np.pad(m, ((0, (-z) % c), (0, (-y) % c), (0, (-x) % c)))
    Z, Y, X = mp.shape
    return mp.reshape(Z // c, c, Y // c, c, X // c, c).any((1, 3, 5))


def largest_component(mask, max_iter=200, coarse=4):
    """3Dマスクの、重心に最も近い種から連結する成分だけ残す（肝臓に紛れる遠い誤検出ブロブを除去）。
    高速化のため粗格子(各軸coarse)で6近傍 geodesic 再構成し、細格子へ戻して AND する。
    近接ブロブ(coarse以内)は肝臓の一部として温存、離れたブロブのみ落とす。"""
    if int(mask.sum()) < 2:
        return mask
    small = _coarsen_any(mask, coarse)                 # 粗格子（数百分の1・数十反復で収束）
    si = np.argwhere(small)
    if len(si) < 2:
        return mask
    cen = si.mean(0)
    seed = si[((si - cen) ** 2).sum(1).argmin()]       # 重心に最も近い粗セルを種に
    marker = np.zeros_like(small); marker[tuple(seed)] = True
    cur = 1
    for _ in range(max_iter):
        d = marker.copy()
        d[1:] |= marker[:-1]; d[:-1] |= marker[1:]
        d[:, 1:] |= marker[:, :-1]; d[:, :-1] |= marker[:, 1:]
        d[:, :, 1:] |= marker[:, :, :-1]; d[:, :, :-1] |= marker[:, :, 1:]
        d &= small
        s = int(d.sum())
        if s == cur:
            break                                      # 収束＝種の連結成分が確定
        cur = s; marker = d
    keep = np.repeat(np.repeat(np.repeat(marker, coarse, 0), coarse, 1), coarse, 2)
    keep = keep[:mask.shape[0], :mask.shape[1], :mask.shape[2]]
    return mask & keep


def ivc_centerline(mask, dz=1.0, min_vox=6, keep_mm=8.0):
    """AI(TotalSegmentator)のIVCマスク(bool [nz,H,W])から、ICEの軸に使うパス点を作る。
    各スライスの重心を z 昇順に並べ→最大連結成分だけ残し→z方向に軽く平滑化→~keep_mm ごとに間引く。
    返り値: [[z, y, x], ...]（**index座標**＝self.path と同じ形式）。作れなければ []。
    ※あくまで下書き。斜走・合流部・肝硬変ではブレるため、医師がドラッグで微調整/消去する前提。"""
    m = np.asarray(mask).astype(bool)
    if m.ndim != 3 or int(m.sum()) < 30:
        return []
    try:
        m = largest_component(m)                       # 合流部の別枝や離れた誤検出を落とす
    except Exception:
        pass
    pts = []
    for z in range(m.shape[0]):
        ys, xs = np.where(m[z])
        if len(xs) >= min_vox:
            pts.append([float(z), float(ys.mean()), float(xs.mean())])
    if len(pts) < 2:
        return []
    pts = np.asarray(pts, float)
    if len(pts) >= 5:                                  # 重心のガタつきを3点移動平均で均す（端点は保持）
        for c in (1, 2):
            s = pts[:, c].copy()
            s[1:-1] = (pts[:-2, c] + pts[1:-1, c] + pts[2:, c]) / 3.0
            pts[:, c] = s
    keep = [0]                                         # ~keep_mm ごとに間引く（端点は必ず残す）
    for i in range(1, len(pts)):
        if (pts[i, 0] - pts[keep[-1], 0]) * dz >= keep_mm:
            keep.append(i)
    if keep[-1] != len(pts) - 1:
        keep.append(len(pts) - 1)
    return pts[keep].tolist()


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
    """マスク表面ボクセル → mm 点群と外向き法線（平滑占有率の勾配）。

    収縮の前に外周を1ボクセル分「空」で囲う。囲わないと、配列の端に接したボクセルは
    「端の外も中身」と見なされて収縮に残り、**表面と判定されない**。上腹部だけを撮った CT では
    最初と最後のスライスが体の切断面になるので、これを表面に入れないと 3D が
    上下の抜けた筒（中が丸見え）になる。実際そう見えていた。"""
    occ = m.astype(np.float32)
    for _ in range(3):             # 6近傍平均で平滑化（法線を滑らかに）。
        a = occ.copy()             # np.roll は毎回フル複製を作る。スライス加算なら一時配列は1本で済む
        a[1:] += occ[:-1]; a[:-1] += occ[1:]
        a[:, 1:] += occ[:, :-1]; a[:, :-1] += occ[:, 1:]
        a[:, :, 1:] += occ[:, :, :-1]; a[:, :, :-1] += occ[:, :, 1:]
        occ = a / 7.0
    surf = m & ~_ero6(np.pad(m, 1))[1:-1, 1:-1, 1:-1]
    zi, yi, xi = np.where(surf)
    if len(zi) == 0:
        return None, None
    # 勾配は「index あたり」で出るので mm に直す。ボクセルは異方性（面内 0.7mm / スライス間 2.5mm）で、
    # 直さないと法線が z 方向に潰れる。3D の陰影だけでなく、体表点を掴んだときのプローブの向き
    # （体内向き法線）もこの値を使っているので、単位を揃えないと向きが狂う。
    # 軸ごとに作って即座に表面ボクセルだけ抜く（3軸まとめて持つとボリューム3本分のメモリを食う）。
    gmm = np.stack([np.gradient(occ, axis=2)[zi, yi, xi] / dsx,
                    np.gradient(occ, axis=1)[zi, yi, xi] / dsy,
                    np.gradient(occ, axis=0)[zi, yi, xi] / ddz], 1)
    mag = np.linalg.norm(gmm, axis=1, keepdims=True); mag[mag < 1e-9] = 1e-9
    nl = (-gmm / mag).astype(np.float32)                 # 勾配は内向き→反転で外向き（mm空間の単位ベクトル）
    pts = np.column_stack([xi * dsx, yi * dsy, zi * ddz]).astype(np.float32)
    # サブボクセル補正：点をボクセル中心のまま使うと表面が格子に量子化され、深度が階段になる。
    # そこから法線を作ると段差が等高線の縞（指紋のような輪）として見える。実際そう見えていた。
    # 平滑占有率 occ の 0.5 等値面まで法線方向に押し出して、点を連続な面の上に乗せる。
    t = (occ[zi, yi, xi] - 0.5) / mag[:, 0]              # 等値面までの符号付き距離(mm・正=内側)
    lim = 2.0 * float(max(dsx, dsy, ddz))
    pts = pts + nl * np.clip(t, -lim, lim)[:, None]      # 外向き(nl)へ t だけ動かす
    if len(pts) > cap:
        idx = np.random.RandomState(seed).choice(len(pts), cap, replace=False)
        pts, nl = pts[idx], nl[idx]
    return pts.astype(np.float32), nl


def smooth_points(m, dsx, dsy, ddz, cap=200000, seed=3):
    """血管など細い構造のマスク → **滑らかな** mm 点群（法線は捨てて座標だけ）。

    従来はボクセル中心をそのまま splat で描いていたので、輪郭が格子に量子化されて階段状に見えた
    （解析の細かさを 1.5mm にするとさらに目立つ）。肝臓の面で使っている `_surface_points` と
    同じ仕組み＝平滑化した占有率の 0.5 等値面まで点を押し出す処理をそのまま流用する。
    点は「格子の中心」から「実際の面の上」へ動くだけなので、見た目が滑らかになるのと同時に
    位置としてもむしろ正確になる（動く量は1ボクセル未満）。

    細い血管は収縮で消えるため surf=全ボクセルとなり、点数は従来とほぼ同じ。
    """
    pts, _nl = _surface_points(m, dsx, dsy, ddz, cap=cap, seed=seed)
    return pts


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
                center=surf.mean(0).astype(np.float32), liters=liters,
                spacing=float(max(dsx, dsy, ddz)))


def body_surface(hu, sx, sy, dz, air=-300.0, ds=None, cap=160000):
    """CT全体の外郭(皮膚)を粗く抽出。経腹エコーの3D表示＆プローブ設置面に使う。
      返り値 dict(surf,nrm(外向き),interior,center,extent,spacing) or None。診断用途ではない粗いシェル。

    面内は 1/2 間引きが基本（従来は一律 1/4）。表面の見た目は点群の密度で決まるので、ここをケチると
    後段でいくら滑らかにしても輪郭が痩せる。ただしスライス数の多い CT では作業配列が肥大するので、
    800万ボクセルに収まるところまで自動で間引きを強める。読み込み時の背景処理なので体感には出ない。
    spacing = 隣り合う表面点の最大間隔(mm)。描画側がスプラット半径をこれから決める。"""
    if ds is None:
        f = 2
        while hu.size / float(f * f) > 8e6 and f < 5:
            f += 1
        ds = (1, f, f)
    fz, fy, fx = ds
    d = np.ascontiguousarray(hu[::fz, ::fy, ::fx]).astype(np.float32)
    m = d > air
    if int(m.sum()) < 100:
        return None
    m = _fill_inplane_holes(m)                     # 肺/腸管などの内部空気を埋め、外郭(皮膚)だけ残す
    # CT の寝台とマットは体幹の背側に「薄い弧」として写り、-300HU より濃いのでマスクに入ってしまう。
    # 3D では体から浮いた板になって見え、経腹プローブを置く面と紛らわしい。面内オープニング
    # （収縮→膨張）で薄いものだけを消す：弧は数ボクセル厚なので消え、体幹は厚いので残る。
    op = m
    for _ in range(3):
        op = _ero4(op)
    for _ in range(3):
        op = _dil4(op)
    if int(op.sum()) > int(m.sum()) * 0.5:         # 体幹が残っていることを確認してから採用
        m = op & m
    dsx, dsy, ddz = sx * fx, sy * fy, dz * fz
    surf, nl = _surface_points(m, dsx, dsy, ddz, cap=cap, seed=3)
    if surf is None:
        return None
    ext = float(np.linalg.norm(surf.max(0) - surf.min(0)))
    return dict(surf=surf, nrm=nl, interior=surf,
                center=surf.mean(0).astype(np.float32), extent=ext,
                spacing=float(max(dsx, dsy, ddz)))


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


# ---- スクリーン空間サーフェス（Zバッファ → 穴埋め → 平滑 → 深度勾配から法線 → 陰影）----
#
# 以前は「点をそのまま四角く撒いて、点ごとに持たせた法線で色を塗る」点群スプラットだった。
# 粒々に見えていた理由は3つで、どれも塗り方の問題ではなく **法線と輪郭の作り方** の問題:
#   1. 点ごとの法線は 2値マスクの粗い格子から出しており、隣の点と向きが飛ぶ → 面がザラつく
#   2. 点が疎いと隙間が空き、四角いスプラットで埋めるので四角い粒が見える
#   3. 深度を平滑化していないので、輪郭が階段状になる
# そこで「点は深度バッファを作るためだけに使い、色は画面（=深度画像）から作る」方式に変えた。
# ゲームの deferred shading と同じ考え方で、法線は平滑化した深度の勾配から求める。純 numpy のみ。

def _dil2(m, r=1):                 # 2D 4近傍膨張
    a = m.copy()
    for _ in range(r):
        b = a.copy()
        b[1:] |= a[:-1]; b[:-1] |= a[1:]
        b[:, 1:] |= a[:, :-1]; b[:, :-1] |= a[:, 1:]
        a = b
    return a


def _close2(m, r=1):               # 膨張→収縮＝内側の穴だけ塞ぎ、外形はほぼ元のまま
    return ~_dil2(~_dil2(m, r), r)


def _blur_masked(a, m, r):
    """有効画素だけで平均するボックス平滑（normalized convolution）。
    無効画素を 0 として混ぜると縁が黒く沈むので、重みで割り戻す。"""
    if r < 1:
        return a
    mf = m.astype(np.float32)
    num = _box_blur(np.where(m, a, 0.0).astype(np.float32), r)
    den = _box_blur(mf, r)
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), a).astype(np.float32)


def _smooth_depth(dep, solid, r=3, edge_mm=8.0):
    """深度の平滑化。ただし『崖』はまたがない（1回だけのバイラテラル）。
    撮影範囲の端＝体の切断面では深度が数十mm一気に落ちる。素直に平均すると崖が斜面に化け、
    そこから作った法線が視線と直交して縁が黒くギザギザになる。平均から大きく外れる画素は
    平均の材料から外し、生値のまま残す＝崖は崖のまま、なめらかな所だけなめらかにする。"""
    b = _blur_masked(dep, solid, r)
    near = solid & (np.abs(dep - b) < edge_mm)
    if not near.any():
        return b
    return np.where(near, _blur_masked(dep, near, r), dep).astype(np.float32)


def _pyr_fill(a, m, levels=7):
    """穴あき深度バッファをピラミッド(push-pull)で滑らかに補間して埋める。
    点群が疎いと Zバッファは虫食いになる。近傍コピーで埋めるとブロックが残るので、
    粗い階層まで畳んでから戻し、欠けた画素だけ上の階層の値で埋める。O(N)。"""
    ds = [np.where(m, a, 0.0).astype(np.float32)]
    ws = [m.astype(np.float32)]
    for _ in range(levels):
        A, W = ds[-1], ws[-1]
        H0, W0 = A.shape
        if H0 < 4 or W0 < 4:
            break
        h2, w2 = H0 // 2 * 2, W0 // 2 * 2
        aw = (A[:h2, :w2] * W[:h2, :w2]).reshape(h2 // 2, 2, w2 // 2, 2).sum((1, 3))
        ww = W[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).sum((1, 3))
        ds.append(np.where(ww > 0, aw / np.maximum(ww, 1e-6), 0.0).astype(np.float32))
        ws.append(np.minimum(ww, 1.0).astype(np.float32))
    for i in range(len(ds) - 1, 0, -1):
        H0, W0 = ds[i - 1].shape
        up = np.repeat(np.repeat(ds[i], 2, 0), 2, 1)
        uw = np.repeat(np.repeat(ws[i], 2, 0), 2, 1)
        pad = ((0, max(0, H0 - up.shape[0])), (0, max(0, W0 - up.shape[1])))
        up = np.pad(up, pad, mode="edge")[:H0, :W0]
        uw = np.pad(uw, pad, mode="edge")[:H0, :W0]
        hole = ws[i - 1] < 0.5
        ds[i - 1] = np.where(hole, up, ds[i - 1])
        ws[i - 1] = np.where(hole, uw, ws[i - 1])
    return ds[0]


def _render_surface(liver, az_deg, el_deg, center, scale, w, h, opacity, color, offset, splat_rad, ss=2):
    pts = liver.get("surf")
    if pts is None:
        return None
    ss = max(1, int(ss))
    W, H = w * ss, h * ss
    u, v, depth = _project(pts, az_deg, el_deg, center, scale * ss, W, H,
                           (offset[0] * ss, offset[1] * ss))

    # --- 1) Zバッファ（高解像側で作る＝輪郭のギザギザはここで決まる）
    #     点は「遠い順」に並べる。同じ画素に重なった点は最後（=最も手前）が勝つので、
    #     np.maximum との代入ひとつで正しい Z テストになる（重複indexでも後勝ちが最大値）。
    # 塗り半径は「隣り合う点の画面上の間隔」の半分あれば足りる。大きくすると Zバッファ書き込みが
    # 半径の2乗で重くなるので、残った隙間は後段（クロージング＋ピラミッド補間）に任せるほうが速くて滑らか。
    spacing = float(liver.get("spacing", 0.0))
    if spacing > 0:
        rad = int(np.clip(np.ceil(spacing * scale * ss * 0.45), 1, 10))
    else:
        rad = max(1, int(round(splat_rad * ss)))
    order = np.argsort(depth)
    au, av, ad = u[order], v[order], depth[order]
    zb = np.full(H * W, -1e18, np.float32)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            if dx * dx + dy * dy > rad * rad + rad:   # 丸いスプラット（四角い粒を出さない）
                continue
            uu = au + dx; vv = av + dy
            ok = (uu >= 0) & (uu < W) & (vv >= 0) & (vv < H)
            ii = vv[ok] * W + uu[ok]
            zb[ii] = np.maximum(zb[ii], ad[ok])
    zb = zb.reshape(H, W)
    cov = zb > -1e17
    if not cov.any():
        return None

    # --- 2) アルファは高解像の被覆から作り、平均で落として輪郭をアンチエイリアス
    alpha_hi = _close2(cov, rad + 1).astype(np.float32)       # 点の隙間を閉じて 1枚の面にする
    alpha = alpha_hi.reshape(h, ss, w, ss).mean((1, 3)) if ss > 1 else alpha_hi

    # --- 3) 陰影計算は等倍で（画素数が 1/ss² になり十分速い）。深度は最大値プーリング＝手前の面を採る
    if ss > 1:
        dep = zb.reshape(h, ss, w, ss).max((1, 3))
    else:
        dep = zb
    solid = _close2(dep > -1e17, max(2, rad // ss + 2))       # 内部の虫食いを塞ぐ
    if not solid.any():
        return None
    dep = _pyr_fill(dep, dep > -1e17)                         # 虫食いを滑らかに補間
    dep = _smooth_depth(dep, solid, r=3)                      # 階段を均す（法線の質はここで決まる。崖は保つ）

    # --- 4) 法線＝平滑化した深度の勾配（視空間 x右 / y上 / z手前）
    #     u=x*s, v=-y*s, depth=z(mm) より n ∝ (-∂D/∂u·s, +∂D/∂v·s, 1)
    s_eff = float(scale)
    gv, gu = np.gradient(dep)
    nx = -gu * s_eff; ny = gv * s_eff; nz = np.ones_like(dep)
    # 撮影範囲の端（切断面）では深度が崖のように落ちる。傾きをそのまま使うと法線が視線と直交して
    # 真っ黒な線になり、輪郭に沿って黒いギザギザが出る。傾きに上限を設けて「急だが照らされた面」にする。
    np.clip(nx, -4.0, 4.0, out=nx); np.clip(ny, -4.0, 4.0, out=ny)
    ln = np.sqrt(nx * nx + ny * ny + nz * nz); ln[ln < 1e-6] = 1.0
    nx /= ln; ny /= ln; nz /= ln

    # --- 5) 陰影：拡散(半ランバート)＋環境遮蔽＋リムライト＋弱い鏡面＋奥行きの暗さ
    lig = np.array([-0.38, 0.46, 0.80], np.float32); lig /= np.linalg.norm(lig)
    ndl = nx * lig[0] + ny * lig[1] + nz * lig[2]
    lam = np.clip(ndl, 0.0, 1.0)
    wrap = np.clip(ndl * 0.5 + 0.5, 0.0, 1.0) ** 1.4          # 半ランバート＝解剖図譜の柔らかい陰
    diff = 0.34 * lam + 0.52 * wrap

    coarse = _blur_masked(dep, solid, r=max(4, int(0.06 * min(w, h))))
    ridge = np.clip((dep - coarse) / 9.0, -1.0, 1.0)          # +1=尾根 / -1=谷（肋弓・臍・鼠径のくぼみ）
    ao = np.clip(0.80 + 0.30 * ridge, 0.50, 1.06)

    hv = lig + np.array([0.0, 0.0, 1.0], np.float32); hv /= np.linalg.norm(hv)
    spec = np.clip(nx * hv[0] + ny * hv[1] + nz * hv[2], 0.0, 1.0) ** 30.0 * 0.14
    rim = np.clip(1.0 - nz, 0.0, 1.0) ** 5.0 * 0.16           # 輪郭がふわっと立ち上がる＝立体に見える決め手
    #   ↑ 強くすると切断面（撮影範囲の端）の縁が白く光ってケーキのように見えるので控えめに

    dv = dep[solid]
    lo, hi = float(dv.min()), float(dv.max())
    cue = 0.80 + 0.20 * np.clip((dep - lo) / max(hi - lo, 1e-3), 0.0, 1.0)   # 奥は沈ませる

    col = np.array(color, np.float32)
    shade = (0.26 + diff) * ao * cue
    rgb = col[None, None, :] * shade[..., None]
    rgb += 255.0 * spec[..., None]
    rgb += rim[..., None] * np.array([255.0, 232.0, 214.0], np.float32)[None, None, :]
    rgb = np.clip(rgb, 0, 255)

    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = (np.clip(alpha, 0, 1) * float(np.clip(opacity, 0, 1)) * 255).astype(np.uint8)
    return out


def render_ghost(liver, az_deg, el_deg, center, scale, w, h,
                 mode="haze", opacity=0.5, color=TERRA, offset=(0.0, 0.0), splat_rad=2, ss=2):
    """肝臓ゴーストを (h,w,4) uint8 RGBA(非乗算) で返す。Pane3D が drawImage で裏に敷く。

      mode='surface' : スクリーン空間の面レンダリング（Zバッファ→穴埋め→法線→陰影）
      mode='haze'    : 内部点の加算累積 + ぼかし（もや・半透明）
    center/scale は Pane3D が device 幾何で使う apex / s をそのまま渡す（位置整合のため）。
    splat_rad: 点1つあたりの塗り半径(px)。liver dict に 'spacing'(mm) があればそちらから自動決定。
    ss: 超解像倍率。輪郭のアンチエイリアスに効く。回転ドラッグ中など速度優先なら 1。
    """
    if liver is None or w < 4 or h < 4:
        return None
    if mode == "surface":
        return _render_surface(liver, az_deg, el_deg, center, scale, w, h,
                               opacity, color, offset, splat_rad, ss)
    out = np.zeros((h, w, 4), np.uint8)
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


def render_points(pts, az_deg, el_deg, center, scale, w, h, color,
                  offset=(0.0, 0.0), rad=2, opacity=1.0):
    """血管など点群を depth-shaded の色付き splat で (h,w,4) RGBA にして返す（Zバッファ後勝ち・手前が明るい）。
    TotalSegmentator の IVC/門脈を Pane3D に重ねるのに使う。center/scale は他の描画と同じ値を渡す。"""
    if pts is None or len(pts) == 0 or w < 4 or h < 4:
        return None
    u, v, depth = _project(pts, az_deg, el_deg, center, scale, w, h, offset)
    dmin, dmax = float(depth.min()), float(depth.max())
    rng = (dmax - dmin) + 1e-6
    order = np.argsort(depth)                             # 遠い順→後勝ちで手前が上書き
    uu, vv, dd = u[order], v[order], depth[order]
    dn = (dd - dmin) / rng
    col = np.array(color, np.float32)
    out = np.zeros((h, w, 4), np.uint8); flat = out.reshape(-1, 4)
    a8 = int(np.clip(opacity, 0, 1) * 255)
    rad = max(0, int(rad))
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            if dx * dx + dy * dy > rad * rad + rad:       # 丸い粒
                continue
            U = uu + dx; V = vv + dy
            ok = (U >= 0) & (U < w) & (V >= 0) & (V < h)
            idx = V[ok] * w + U[ok]; sh = 0.45 + 0.55 * dn[ok]
            rgb = (col[None, :] * sh[:, None]).astype(np.uint8)
            flat[idx, 0] = rgb[:, 0]; flat[idx, 1] = rgb[:, 1]; flat[idx, 2] = rgb[:, 2]; flat[idx, 3] = a8
    return out
