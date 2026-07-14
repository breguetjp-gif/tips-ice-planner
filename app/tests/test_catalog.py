"""catalog + database_view + open 経路のスモークテスト（合成DICOM・一時フォルダ隔離）。"""
import os
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import PySide6
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(
    os.path.dirname(PySide6.__file__), "Qt", "plugins", "platforms")

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, CTImageStorage


def _slice(path, study_uid, series_uid, z, series_desc, sn, name="Test^Patient", pid="ID001"):
    fm = Dataset()
    fm.MediaStorageSOPClassUID = CTImageStorage
    fm.MediaStorageSOPInstanceUID = generate_uid()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(path, {}, file_meta=fm, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage; ds.SOPInstanceUID = fm.MediaStorageSOPInstanceUID
    ds.PatientName = name; ds.PatientID = pid; ds.PatientBirthDate = "19840424"
    ds.StudyInstanceUID = study_uid; ds.SeriesInstanceUID = series_uid
    ds.StudyDate = "20260221"; ds.StudyTime = "201800"
    ds.StudyDescription = "Abdomen"; ds.SeriesDescription = series_desc
    ds.Modality = "CT"; ds.InstitutionName = "Test Hosp"; ds.SeriesNumber = sn
    ds.Rows = 64; ds.Columns = 64; ds.PixelSpacing = [0.7, 0.7]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]; ds.ImagePositionPatient = [0, 0, float(z)]
    ds.SliceThickness = 1.0; ds.RescaleSlope = 1; ds.RescaleIntercept = -1024
    ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15; ds.PixelRepresentation = 0
    arr = (np.random.rand(64, 64) * 200 + 900).astype(np.uint16)
    ds.PixelData = arr.tobytes()
    try:
        ds.save_as(path, enforce_file_format=True)         # pydicom 3.x
    except TypeError:
        ds.is_little_endian = True; ds.is_implicit_VR = False
        ds.save_as(path, write_like_original=False)


def _make_db(folder):
    s1 = generate_uid(); s2 = generate_uid()                # 2 studies
    a, b, c = generate_uid(), generate_uid(), generate_uid()
    for z in range(5):
        _slice(os.path.join(folder, f"a{z}.dcm"), s1, a, z, "Abd 2.5", 1)
    for z in range(3):
        _slice(os.path.join(folder, f"b{z}.dcm"), s1, b, z, "PH Abd 1.25", 2)
    for z in range(4):
        _slice(os.path.join(folder, f"c{z}.dcm"), s2, c, z, "Chest", 1, name="Two^Patient", pid="ID002")
    return s1, s2


def run():
    tmp = tempfile.mkdtemp(prefix="tips_test_")
    dicom_dir = os.path.join(tmp, "dicom"); os.makedirs(dicom_dir)
    data_dir = os.path.join(tmp, "appdata"); os.makedirs(data_dir)
    s1, s2 = _make_db(dicom_dir)

    import catalog as catmod
    catmod.app_data_dir = lambda: (os.makedirs(os.path.join(data_dir, "thumbs"), exist_ok=True) or data_dir)
    cat = catmod.Catalog()
    added = cat.add_folder(dicom_dir)
    assert added == 3, f"expected 3 series, got {added}"

    # --- 取込プレビュー(scan_series)＋選択取込(import_groups)：匿名化あり/なし ---
    scan_dir = os.path.join(tmp, "scan_src"); os.makedirs(scan_dir)
    su = generate_uid(); se1, se2 = generate_uid(), generate_uid()
    for z in range(3):
        _slice(os.path.join(scan_dir, f"p{z}.dcm"), su, se1, z, "Plain", 1, name="Scan^Case", pid="SCAN1")
    for z in range(2):
        _slice(os.path.join(scan_dir, f"q{z}.dcm"), su, se2, z, "Delayed", 2, name="Scan^Case", pid="SCAN1")
    groups = cat.scan_series(scan_dir)
    assert len(groups) == 2, "scan_series must find both series"
    assert len(cat.series) == 3, "scan_series must not register anything by itself"
    g_by_uid = {g["series_uid"]: g for g in groups}
    assert set(len(g["files"]) for g in groups) == {3, 2}

    # 非匿名化：元ファイルを参照したまま登録（add_folderと同じ挙動＝コピーしない）
    scan_data_dir = os.path.join(tmp, "appdata_scan"); os.makedirs(scan_data_dir)
    catmod.app_data_dir = lambda: (os.makedirs(os.path.join(scan_data_dir, "thumbs"), exist_ok=True) or scan_data_dir)
    cat_plain = catmod.Catalog()
    added_plain = cat_plain.import_groups([g_by_uid[se1]], anonymize=False)
    assert added_plain == 1
    assert cat_plain.series[0]["files"][0].startswith(scan_dir), "non-anonymized import must reference the original files"
    assert cat_plain.series[0]["patient_name"] == "Scan^Case"

    # 匿名化：アプリ専用領域へ別コピー（元ファイルは書き換えない）
    cat_anon = catmod.Catalog()                              # 同じdata_dirだが se1 は未登録(cat_plainとは別インスタンス)
    added_anon = cat_anon.import_groups([g_by_uid[se2]], anonymize=True)
    assert added_anon == 1
    rec = cat_anon.series[-1]
    assert rec["patient_name"] == "ANONYMOUS" and rec["patient_id"] == "ANON"
    assert not rec["files"][0].startswith(scan_dir), "anonymized import must NOT reference the original files"
    import pydicom as _pydicom
    ds_anon = _pydicom.dcmread(rec["files"][0])
    assert str(ds_anon.PatientName) == "ANONYMOUS" and str(ds_anon.PatientID) == "ANON"
    ds_src = _pydicom.dcmread(os.path.join(scan_dir, "q0.dcm"))
    assert str(ds_src.PatientName) == "Scan^Case", "original source file must remain unmodified after anonymized import"
    catmod.app_data_dir = lambda: (os.makedirs(os.path.join(data_dir, "thumbs"), exist_ok=True) or data_dir)  # 以降のテスト用に元へ戻す

    studies = cat.studies()
    assert len(studies) == 2, f"expected 2 studies, got {len(studies)}"
    st1 = next(s for s in studies if s["study_uid"] == s1)
    assert st1["n_series"] == 2 and st1["n_images"] == 8, (st1["n_series"], st1["n_images"])

    # サムネイル
    thumb = cat.thumbnail(st1["series"][0])
    assert thumb is not None and thumb.dtype == np.uint8 and thumb.ndim == 2

    # コメント（重要機能）
    cat.set_comment(s1, "TIPS teaching example")
    cat2 = catmod.Catalog()                                  # 再読込で永続化確認
    assert cat2.studies()
    assert any(s["comment"] == "TIPS teaching example" for s in cat2.studies())

    # シリーズ読込 → Volume
    import dicom_io
    vol = dicom_io.load_series_files(st1["series"][0]["files"])
    assert vol.shape == (5, 64, 64), vol.shape
    assert abs(vol.sx - 0.7) < 1e-3 and abs(vol.dz - 1.0) < 1e-3

    print("✅ data-layer tests passed (catalog / thumbnails / comments / series load)")

    # ヘッドレスUI（環境依存=ベストエフォート。実機はcocoaで動作）
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
    except Exception as ex:
        print(f"⚠️ GUI smoke skipped (headless Qt unavailable): {ex}"); return
    from database_view import DatabaseView, C_COMMENT, C_PATIENT, C_ID, C_INST
    dv = DatabaseView(cat)
    assert dv.tree.topLevelItemCount() == 2                  # 検査=1行（子なし）
    assert dv.tree.topLevelItem(0).text(C_INST) == "Test Hosp", "institution column must show DICOM InstitutionName"
    # 匿名化トグル: 氏名/IDが伏字になる → 解除で戻る（データは不変）
    _nm0 = dv.tree.topLevelItem(0).text(C_PATIENT)
    dv.anonBtn.setChecked(True); dv._toggle_anon()
    assert dv.tree.topLevelItem(0).text(C_PATIENT) == "●●●" and dv.tree.topLevelItem(0).text(C_ID) == "●●●", "anonymize masks name/ID"
    assert any(s["patient_name"] for s in cat.studies()), "underlying data unchanged"
    dv.anonBtn.setChecked(False); dv._toggle_anon()
    assert dv.tree.topLevelItem(0).text(C_PATIENT) == _nm0, "un-anonymize restores"
    assert dv.tree.topLevelItem(0).childCount() == 0, "series must not clutter the tree (shown as thumbnails)"
    dv.tree.setCurrentItem(dv.tree.topLevelItem(0))          # 選択→サムネ表示
    assert dv.thumbs.count() >= 1
    dv._fill_thumbs(st1); assert dv.thumbs.count() == 2
    # 枚数(n img)は説明(series_desc)がどれだけ長くても隠れないよう1行目の先頭に置く(先生報告の不具合対応)
    st_longdesc = dict(st1); st_longdesc["series"] = [dict(st1["series"][0])]
    st_longdesc["series"][0]["series_desc"] = "あ" * 200                       # 極端に長い説明
    dv._fill_thumbs(st_longdesc)
    lbl = dv.thumbs.item(0).text()
    assert lbl.split("\n", 1)[0].startswith(f"{st_longdesc['series'][0]['n']} img"), \
        f"image count must lead the label regardless of description length; got: {lbl!r}"
    dv.tree.topLevelItem(0).setText(C_COMMENT, "edited-in-ui")   # Comment編集→永続化
    assert any(s.get("comment") == "edited-in-ui" for s in cat.studies()), "comment edit must persist"
    # --- 取込フロー一式：フォルダ選択→スキャン→選択ダイアログ(既定=全選択)→登録 ---
    # bg.run_with_progress は QThread 経由(非同期)なのでテストでは同期実行に差し替える。
    from unittest.mock import patch as _patch_imp
    import database_view, bg
    from database_view import ImportPickerDialog

    def _sync_run(parent, label, fn, on_done, on_fail=None):
        res = fn(lambda i, n: None); on_done(res)
    imp_dir = os.path.join(tmp, "import_flow_src"); os.makedirs(imp_dir)
    iu, ise = generate_uid(), generate_uid()
    for z in range(2):
        _slice(os.path.join(imp_dir, f"f{z}.dcm"), iu, ise, z, "FlowTest", 9, name="Flow^Case", pid="FLW1")
    n_before = len(cat.series)
    with _patch_imp.object(bg, "run_with_progress", side_effect=_sync_run), \
         _patch_imp.object(database_view.QFileDialog, "getExistingDirectory", return_value=imp_dir), \
         _patch_imp.object(ImportPickerDialog, "exec", return_value=database_view.QDialog.Accepted):
        dv._import()
    assert len(cat.series) == n_before + 1, "import flow (folder pick → scan → dialog → register) must add the new series"
    assert any(s["series_uid"] == ise for s in cat.series)
    # 再度同じフォルダを取り込もうとしても、既定で「取込済み」は選ばれない→重複登録されない
    with _patch_imp.object(bg, "run_with_progress", side_effect=_sync_run), \
         _patch_imp.object(database_view.QFileDialog, "getExistingDirectory", return_value=imp_dir), \
         _patch_imp.object(ImportPickerDialog, "exec", return_value=database_view.QDialog.Accepted):
        dv._import()
    assert len(cat.series) == n_before + 1, "already-imported series must not be re-added by default"
    dv.resize(900, 520); dv.grab()                           # paint 経路
    import main
    win = main.MainWindow()
    import i18n
    i18n.set_lang("en"); win._apply_language()               # テストは保存済みUI言語に依らず英語で検証
    # 上部メニュー（Help → FAQ / About）
    help_actions = [a.text() for mu in win.menuBar().actions() for a in (mu.menu().actions() if mu.menu() else [])]
    assert any("FAQ" in t for t in help_actions), help_actions
    vol2 = dicom_io.load_series_files(st1["series"][0]["files"])   # 読込は同期で検証
    win._on_series_loaded(vol2)                                    # 開く→ビューア切替の経路
    assert win.stack.currentWidget() is win.viewer_page
    assert win.vol is not None and win.vol.shape == (5, 64, 64)
    # 針 / ICE / 3D / 各オーバーレイの描画経路まで通す
    win.path = [[1, 40, 40], [2, 32, 32], [3, 24, 28]]; win.zP = 2
    win.entry = np.array([20 * vol2.sx, 30 * vol2.sy, 1 * vol2.dz])    # 刺入点(肝静脈)
    win.target = np.array([40 * vol2.sx, 20 * vol2.sy, 3 * vol2.dz])   # 狙い(門脈, 別z)
    assert win._needle() is not None                                  # Entry→Target 直線穿刺
    win.iceRoll = 35.0                                                # 無段階ロール表示の経路
    win.step = 1; win._refresh()
    assert win._geom() is not None and win._needle() is not None and win.p3d.valid
    _nd = win._needle()                                               # 巻き戻し後＝Entry→Target 直線
    assert _nd["cannula"] is None and _nd["needle"].shape == (2, 3), "straight puncture = single Entry→Target segment"
    assert abs(_nd["length"] - float(np.linalg.norm(win.target - win.entry))) < 1e-6, "length = Entry→Target distance"
    assert _nd["miss"] == 0.0 and np.allclose(_nd["tip"], win.target), "straight path tip is exactly Target"
    # ICEクリック→Entry/Targetの逆投影（ロール込み）経路
    win.ptMode = 0; w = win._ice_to_world(win._ice_wi / 2, win._ice_hi / 3)
    assert w is None or w.shape == (3,)
    # 実際の針(1クリック式): Entry(固定)→クリックした点(針先)を自動生成。Entry/Targetとは独立
    win.aimBtn.setChecked(True); win._toggle_aim(); assert win.aimMode is True
    _e0, _t0 = win.entry.copy(), win.target.copy()
    win._ice_click(win._ice_wi * 0.5, win._ice_hi * 0.45)                 # 1クリック=針先
    assert np.allclose(win.entry, _e0) and np.allclose(win.target, _t0), "aim click must not move Entry/Target"
    assert win.aim_tip is not None, "single click sets aim_tip (needle runs from the fixed Entry)"
    win._move_point_ice("aim_tip", win._ice_wi * 0.52, win._ice_hi * 0.47)  # ドラッグで微調整
    win._refresh(); assert win.ice.img is not None                       # 描画経路(針の形＋2cm予測点線＋readout)
    # Coronal/Sagittalに、3D連動パネルと同じカテーテル本体(灰シャフト+偏向先端=オレンジ)を
    # ICEの軌跡(IVCパス)上の実際の位置へ投影して重ねる（先生要望・Axial/ICEには出さない）。
    # unittest.mock.patchで差し替えるとMock自身がQPainter引数への参照をcall_argsに保持し続け、
    # オフスクリーンQtが「paint device destroyed」警告を出すため、素の関数差し替え(参照を残さない)で検証する。
    _cath_calls = []
    _orig_cath = main.MainWindow._draw_catheter_body
    main.MainWindow._draw_catheter_body = lambda self, p, tw, v, pl, nz: _cath_calls.append(pl)
    try:
        for pane in (win.ax, win.cor, win.sag):
            pane.resize(300, 280); pane.grab()                           # CT断面への針形状+予測点線 投影の描画経路
        assert sorted(_cath_calls) == [1, 2], f"catheter body must draw exactly for coronal(1)+sagittal(2), got {_cath_calls}"
    finally:
        main.MainWindow._draw_catheter_body = _orig_cath
    pred0 = main.core.predict_curve(win.entry, win.aim_tip, radius=main.core.COLA_R,
                                    span_deg=np.degrees(20.0 / main.core.COLA_R), torque_deg=win.aim_torque)
    assert pred0.shape[1] == 3 and len(pred0) >= 2
    ar = main.core.aim_readout(win.aim_tip, win.target, vol2.meta.get("orient"))
    assert ar["dist"] >= 0
    # 右回転/左回転：予測点線の曲がる向きが変わる（手元のトルク操作の目安）
    win._nudge_torque(15); assert win.aim_torque == 15.0 and win.hubWidget.torque_deg == 15.0
    pred1 = main.core.predict_curve(win.entry, win.aim_tip, radius=main.core.COLA_R,
                                    span_deg=np.degrees(20.0 / main.core.COLA_R), torque_deg=win.aim_torque)
    assert not np.allclose(pred0[-1], pred1[-1]), "rotating must change the predicted curve direction"
    win._nudge_torque(-15); assert win.aim_torque == 0.0
    win._refresh(); win.ice.grab()                                        # 回旋後の再描画経路
    # Entry未設定でaimModeに入っても落ちない（案内メッセージのみ）
    _entry_saved = win.entry; win.entry = None
    win._ice_click(win._ice_wi * 0.5, win._ice_hi * 0.5)
    win.entry = _entry_saved
    win.aimBtn.setChecked(False); win._toggle_aim(); assert win.aimMode is False
    # 予習(Plot)モード：実針プロット＋前方予測の描画経路
    win.predict = True; win.pred_curved = True
    win.obs = [win.entry + np.array([0, -3, 1]), win.entry + np.array([0, -6, 3])]
    win._refresh()
    assert win._pred_world() is not None
    # 肝臓ゴースト：合成データを注入し 3D描画経路（haze/surface）＋実ハンドラを通す
    _surf = np.array([[20, 30, 1], [22, 30, 1], [20, 32, 2], [24, 30, 2], [20, 30, 3]], np.float32)
    _Ld = dict(surf=_surf, nrm=np.tile([0, 0, 1.0], (len(_surf), 1)).astype(np.float32),
               interior=_surf.copy(), center=_surf.mean(0).astype(np.float32), liters=0.5)
    win.liver = _Ld; win.p3d.liver = _Ld; win._build_3d(win._geom())
    for _m in ("surface", "haze"):                                  # 実ハンドラでモード切替（setText含む）
        if win.liver_mode != _m:
            win._toggle_liver_mode()
        win.p3d.resize(320, 300); win.p3d.grab()
    win.liverBtn.setChecked(False); win._toggle_liver(); assert win.p3d.show_liver is False
    win.liverBtn.setChecked(True); win._toggle_liver(); assert win.p3d.show_liver is True
    win._set_liver_opacity(70); assert abs(win.liver_opacity - 0.70) < 1e-6
    # 方位キューブ：index軸→LPS から解剖文字を割当（標準axial＝既定／反転行列でR/L・S/I入替）
    assert win.p3d._orient_letters() == {"+x": "L", "-x": "R", "+y": "P", "-y": "A", "+z": "S", "-z": "I"}
    win.p3d.orient = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]            # 左右反転＋頭尾反転
    _fl = win.p3d._orient_letters()
    assert _fl["+x"] == "R" and _fl["-x"] == "L" and _fl["+z"] == "I" and _fl["-z"] == "S", _fl
    win.p3d.orient = None
    for pane in (win.ax, win.cor, win.sag, win.ice, win.p3d):
        pane.resize(320, 300); pane.grab()                         # paint（オーバーレイ/3D/予測/肝ゴースト）
    # トラックパッド操作（レビュー反映後）: ピンチ/Cmdズーム・積算スライス・反転リセット・パンラッチ・固着解除
    from PySide6.QtCore import QPointF, QEvent
    from PySide6.QtGui import QFocusEvent
    pane = win.ax; pane.resize(300, 300)
    z0 = pane.zoom
    class _Pinch:                                                  # ピンチ・ジェスチャの代用
        def gestureType(self): return main.Qt.NativeGestureType.ZoomNativeGesture
        def value(self): return 0.5
        def position(self): return QPointF(150, 150)
    pane._native_gesture(_Pinch()); assert pane.zoom > z0, "pinch should zoom in"
    z1 = pane.zoom; pane._wheel_zoom(120, QPointF(150, 150)); assert pane.zoom != z1, "Cmd+scroll zoom"
    got = []; pane.wheelMoved.connect(lambda d: got.append(d))
    pane._scroll_accum = 0.0
    for _ in range(12):                                           # pixelDelta 120px → ~4段
        pane._wheel_slice(10.0, 0)
    assert sum(got) != 0, "trackpad pixel scroll should step slices"
    got.clear(); pane._scroll_accum = 0.0
    pane._wheel_slice(0, 120); assert got == [1], f"one mouse notch = 1 step, got {got}"
    got.clear(); pane._scroll_accum = 0.0                         # 細かいangleは積算（Winで飛ばない）
    pane._wheel_slice(0, 40); pane._wheel_slice(0, 40)
    assert got == [], "sub-notch angleDelta must accumulate, not jump"
    pane._wheel_slice(0, 40); assert got == [1], f"reaching 120 = 1 step, got {got}"
    pane._scroll_accum = 0.0; pane._wheel_slice(20.0, 0); pane._wheel_slice(-20.0, 0)   # 反転リセット
    assert abs(pane._scroll_accum + 20.0) < 1e-6, f"reversal resets residue, accum={pane._scroll_accum}"
    pane._space = True                                           # Space固着 → focusOut で解除（HIGH修正）
    pane.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert pane._space is False, "focusOut must clear stuck Space pan-mode"
    # アクティブ枠の連動（マウスが入った画面だけ太枠）＋ 操作凡例バー
    win._activate(win.cor)
    assert win.cor.active and not win.ax.active, "active frame must follow the hovered pane"
    main.GestureBar().grab()                                  # 凡例バー（拡大縮小/階調/移動 アイコン）描画
    # 外部アプリ→本アプリ: URLスキームのパース（純粋関数）
    from PySide6.QtCore import QUrl
    _u = QUrl(f"{main.URL_SCHEME}://open?dir=/tmp/some%20study%20folder")
    assert main.path_from_open_event(_u, "") == "/tmp/some study folder", "URL scheme dir must url-decode"
    assert main.path_from_open_event(QUrl("https://example.com/x"), "") is None, "foreign scheme must be ignored"
    assert main.path_from_open_event(None, "/tmp/plain/path") == "/tmp/plain/path", "plain file-open path passes through"
    assert main.path_from_open_event(None, "") is None
    # 開く要求のバッファ: ハンドラ設定前のイベントは保持し、設定時にまとめて流す
    _got = []
    _disp = main._OpenDispatcher()
    _disp.dispatch("/tmp/pending")                            # ハンドラ未設定 → バッファ
    assert _got == [], "must not fire before handler is set"
    _disp.set_handler(_got.append)                            # 設定時に溜まった分を流す
    assert _got == ["/tmp/pending"], "buffered open events must flush on handler set"
    _disp.dispatch("/tmp/live")                               # 設定後は即配送
    assert _got == ["/tmp/pending", "/tmp/live"], "post-handler events dispatch immediately"
    # 外部からの永久取り込み: 新規スタディを _import_external で取り込む（temp data_dir に隔離）
    _ho = os.path.join(tmp, "handoff2"); os.makedirs(_ho)
    _su = generate_uid(); _se = generate_uid()
    for z in range(5):                                        # 単一相(=1シリーズ)の新規スタディ
        _slice(os.path.join(_ho, f"h{z}.dcm"), _su, _se, z, "Portal Phase", 7, name="Handoff^Case", pid="HO1")
    _n0 = len(win.catalog.series)
    _added, _suid = win._import_external(_ho)                 # 同期: コピー→カタログ取込
    assert _added == 1 and _suid == _su, f"import should add 1 new series, got {_added}/{_suid}"
    assert len(win.catalog.series) == _n0 + 1, "catalog must grow (permanent import)"
    _rec = next(s for s in win.catalog.series if s["study_uid"] == _su)
    assert _rec["files"][0].startswith(win.catalog.dir) and (os.sep + "studies" + os.sep) in _rec["files"][0], \
        "DICOMs must be copied under the app's permanent store (studies/)"
    assert all(os.path.exists(f) for f in _rec["files"]), "copied files must persist"
    _added2, _ = win._import_external(_ho)                    # 再送は重複追加しない
    assert _added2 == 0, "re-sending the same study must not duplicate"
    # 相選択: select_study が単一シリーズを自動オープン(openSeries発火)
    _fired = []; win.db.openSeries.connect(lambda files: _fired.append(files))
    win.db.reload()
    assert win.db.select_study(_su, open_if_single=True) is True
    assert _fired and len(_fired[-1]) == 5, "single-phase study auto-opens via openSeries"
    win.open_external_path(os.path.join(tmp, "does-not-exist"))   # 不正パスでも落ちない
    # 経腹エコー(体表)モード: 切替→ラベル読み替え→皮膚にプローブ設置→凸扇geom＋描画(3Dはカテーテル無し)
    win.stack.setCurrentWidget(win.viewer_page)
    win._toggle_viewmode(); assert win.viewMode == "surface" and win.surfCtl.isVisible() and not win.handleCtl.isVisible()
    _place = ((0, lambda: win._axial_click(vol2.shape[2] // 2, 3)),      # Axial
              (1, lambda: win._ortho_click(1, vol2.shape[2] // 2, 3)),   # Coronal
              (2, lambda: win._ortho_click(2, vol2.shape[1] // 2, 3)))   # Sagittal
    for _pl, _click in _place:
        _click(); assert win.contact is not None and win.surfPlane == _pl, f"probe placed on plane {_pl}"
        _sg = win._geom(); assert _sg is not None and _sg.get("mode") == "surface", f"surface geom (plane {_pl})"
        win._refresh(); assert win.ice.img is not None, f"transabdominal echo must render (plane {_pl})"
    win._move_point("contact", vol2.shape[2] // 2, 5, 0)          # ドラッグ=皮膚上を移動(Axial)
    assert win.surfPlane == 0 and win.contact is not None, "drag moves the probe along the surface"
    # 自動オリエント: Entry/Target設定済みでプローブを置くと、3点が乗る断面へθが自動回転（先生要望2026-07-14）
    win.entry = np.array([vol2.shape[2] * 0.4 * vol2.sx, vol2.shape[1] * 0.5 * vol2.sy, 20 * vol2.dz])
    win.target = np.array([vol2.shape[2] * 0.6 * vol2.sx, vol2.shape[1] * 0.5 * vol2.sy, 25 * vol2.dz])
    win.theta = 0.0; win._axial_click(vol2.shape[2] // 2, 3)      # 設置→_auto_orient_surface発火
    _g = win._geom(); _w = np.cross(_g["Vp"], _g["Sp"]); _w = _w / (np.linalg.norm(_w) + 1e-9); _C = np.asarray(_g["Tp"])
    assert abs((win.entry - _C) @ _w) < 6.0 and abs((win.target - _C) @ _w) < 6.0, "3点が同一断面に近づくようθ自動回転"
    assert win.b1 == 0.0 and win.b2 == 0.0, "初期表示は傾き/あおり0"
    # 3D体表: bodyを注入→3Dピックでプローブ設置(surfPlane=-1)＋その面での凸扇geom
    _surf = np.array([[30, 10, 10], [31, 10, 10], [30, 12, 11]], np.float32)
    win.body = dict(surf=_surf, nrm=np.tile([0, -1, 0.0], (3, 1)).astype(np.float32),
                    interior=_surf, center=_surf.mean(0).astype(np.float32), extent=40.0)
    win.p3d.body = win.body
    win._pick_surface_3d(1)
    assert win.surfPlane == -1 and win.contact is not None, "3D surface pick places the probe"
    assert win._geom() is not None and win._geom().get("mode") == "surface", "free-placed surface geom"
    win.p3d.show_body = True; win.p3d.resize(300, 280); win.p3d.grab()   # 体表シェル描画経路
    for pane in (win.ax, win.cor, win.sag, win.p3d):
        pane.resize(300, 280); pane.grab()                       # 断面ゴースト＋接触点＋3D(扇のみ)描画経路
    win._toggle_viewmode(); assert win.viewMode == "ice" and win.handleCtl.isVisible() and not win.surfCtl.isVisible()
    # --- v0.4.22: 3Dズーム整合 / パンoffset / レイアウト固定 / 言語切替 ---
    from PySide6.QtCore import QPointF
    from tips_core import liver as liver_core
    _pts = np.array([[10.0, 5.0, -3.0]])
    _u0, _v0, _ = liver_core._project(_pts, 30, 20, (0, 0, 0), 2.0, 200, 200)
    _u1, _v1, _ = liver_core._project(_pts, 30, 20, (0, 0, 0), 2.0, 200, 200, offset=(7.0, -5.0))
    assert _u1[0] - _u0[0] == 7 and _v1[0] - _v0[0] == -5, "liver projection must honor pan offset"
    _p3 = win.p3d
    _p3.zoom3d = 8.0; _p3.pan3d = QPointF(3, 4)
    _p3._zoom_at(2.0, QPointF(10, 10), _p3.rect())            # 上限クランプ時にパンだけ滑らない
    assert _p3.zoom3d == 8.0 and abs(_p3.pan3d.x() - 3) < 1e-6 and abs(_p3.pan3d.y() - 4) < 1e-6
    _p3.zoom3d = 1.0; _p3.pan3d = QPointF(0, 0)
    # 針操作行は Step1(ICE)では丸ごと畳む（下の空白を無くし画像を大きく）／Step2で出す
    win._set_step(0); assert win.needleRowW.isHidden(), "needle row collapses in step 1 (ICE setup)"
    win._set_step(1); assert not win.needleRowW.isHidden(), "needle row shows in step 2 (needle)"
    win._set_step(0)
    # Handleパネルは画像直下の複合帯に置かれ、下部の別行には居ない
    assert win.handleCtl.parent() is not None
    # --- 持ち手(body)は上下=θ回転・左右=push/pull に統合（先生指摘：どこを押すか分かりにくい対策）---
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent
    hc = win.handleCtl; hc.resize(900, 140)
    p0 = hc._px(98, 30)                                       # 本体(θ/push-pull)領域の中心付近
    p_up = QPointF(p0.x(), p0.y() - 40); p_side = QPointF(p0.x() + 40, p0.y())

    def _ev(kind, pos):
        return QMouseEvent(kind, pos, pos, _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier)
    hc.set_state(0, 0, 90, 0, False)
    hc.mousePressEvent(_ev(QMouseEvent.Type.MouseButtonPress, p0))
    assert hc._drag == "body"
    hc.mouseMoveEvent(_ev(QMouseEvent.Type.MouseMove, p_up))   # 上へドラッグ＝θ回転
    theta_after_up = hc.theta
    hc.mouseReleaseEvent(_ev(QMouseEvent.Type.MouseButtonRelease, p_up))
    assert abs(theta_after_up - 90.0) > 1.0, "dragging the body vertically must rotate theta"
    # 左右ドラッグ＝push/pull（probe_enabled時のみ）。thetaは変化しない
    hc.set_state(0, 0, 90, 40, True)
    hc.mousePressEvent(_ev(QMouseEvent.Type.MouseButtonPress, p0))
    hc.mouseMoveEvent(_ev(QMouseEvent.Type.MouseMove, p_side))
    theta_after_side = hc.theta; probe_after_side = hc.probe
    hc.mouseReleaseEvent(_ev(QMouseEvent.Type.MouseButtonRelease, p_side))
    assert theta_after_side == 90.0, "horizontal drag on the body must not rotate theta"
    assert abs(probe_after_side - 40.0) > 1.0, "horizontal drag on the body must move push/pull when enabled"
    # probe_enabled=False のときは左右ドラッグしてもpush/pullは変化しない（IVCパス未確定時のガード）
    hc.set_state(0, 0, 90, 40, False)
    hc.mousePressEvent(_ev(QMouseEvent.Type.MouseButtonPress, p0))
    hc.mouseMoveEvent(_ev(QMouseEvent.Type.MouseMove, p_side))
    probe_after_disabled = hc.probe
    hc.mouseReleaseEvent(_ev(QMouseEvent.Type.MouseButtonRelease, p_side))
    assert probe_after_disabled == 40.0, "push/pull must stay gated by probe_enabled"
    i18n.set_lang("ja"); win._apply_language()                # 言語切替（英→日→英）
    assert win.surfBtn.text() == "経腹（体表）", win.surfBtn.text()
    assert win.db.openBtn.text() == "開く" and i18n.lang() == "ja"
    i18n.set_lang("en"); win._apply_language()
    assert win.surfBtn.text().startswith("Transabdominal") and i18n.lang() == "en"
    # --- v0.4.32: 患者リスト画面(DatabaseView)にも言語切替ボタン(先生要望) ---
    # v0.4.35以降：本体の言語ボタンはメニュー(Settings)に集約したため、DB画面のボタン単体で確認する
    assert win.db.langBtn.text() == "日本語", "DB view lang button shows the next-language label"
    win.db.langBtn.click()                                    # DB画面のボタンから切替 → アプリ全体に反映
    assert i18n.lang() == "ja"
    assert win.db.langBtn.text() == "English", "shows the next-language label after toggling from the DB view"
    win.db.langBtn.click()                                    # もう一度押して元の言語に戻る
    assert i18n.lang() == "en"
    assert win.db.langBtn.text() == "日本語"
    win._clear_needle()
    assert win.aim_tip is None and win.entry is None, "clear needle also clears the actual-needle line"
    # --- Undo（直前の1手だけ戻す）: Entry/Target設定・Clear needle・Clear plots を1つずつ取り消せる ---
    win._undo_snapshot = None; win._set_undo_enabled(False)   # 直前のテスト分の未消費スナップショットを掃除(実復元はしない)
    assert not win.undoBtn.isEnabled()
    _undo_off_style = win.undoBtn.styleSheet()
    win.step = 1; win.ptMode = 0
    win._axial_click(80, 80)                                          # Entry設定（1手）
    assert win.entry is not None and win.undoBtn.isEnabled() and win.undoAction.isEnabled()
    assert win.undoBtn.styleSheet() != _undo_off_style, \
        "undo button must look visually different when enabled (setEnabled alone is invisible in this app's QSS)"
    win._undo()
    assert win.entry is None and not win.undoBtn.isEnabled(), "undo reverts the Entry click"
    win._axial_click(80, 80); win._axial_click(90, 90)                # Entry→Target設定
    assert win.entry is not None and win.target is not None
    win.obs = [np.array([1.0, 2.0, 3.0])]
    win._clear_needle()
    assert win.entry is None and win.target is None
    win._undo()
    assert win.entry is not None and win.target is not None, "undo reverts Clear needle"
    win._clear_plots()
    assert win.obs == []
    win._undo()
    assert len(win.obs) == 1 and np.allclose(win.obs[0], [1.0, 2.0, 3.0]), "undo restores cleared plot points too"
    win._clear_needle(); win._clear_plots()                           # このテストブロックの後始末
    # --- v0.4.29: 作業状態の保存/復元（患者ごとにスロット1/2/3） ---
    from database_view import ROLE_STUDYUID
    win.stack.setCurrentWidget(win.viewer_page)
    win.current_study_uid = s1; win._current_files = st1["series"][0]["files"]
    win.path = [[1, 40, 40], [2, 32, 32]]; win.theta = 77.0; win.b1 = 5.0
    win.entry = np.array([10.0, 20.0, 30.0]); win.target = np.array([15.0, 25.0, 35.0])
    win._save_slot(1, notify=False)                          # notify=False: テストでモーダルポップアップを出さない
    assert win.catalog.has_session(s1, 1) and not win.catalog.has_session(s1, 2), "only slot 1 saved"
    assert win.saveBtns[0].styleSheet() != win.saveBtns[1].styleSheet(), "saved slot must look different from empty"
    saved = win.catalog.get_session(s1, 1)
    assert saved["theta"] == 77.0 and saved["entry"] == [10.0, 20.0, 30.0] and len(saved["path"]) == 2
    win._clear_all()
    assert win.entry is None and len(win.path) == 0, "clear all resets state before restore"
    win._restore_state(saved)
    assert win.theta == 77.0 and len(win.path) == 2
    assert win.entry is not None and np.allclose(win.entry, [10.0, 20.0, 30.0]), "restore brings entry back"
    def _restore_buttons(dv, uid):
        """患者リストの行に埋め込まれたRestore(1/2/3)ボタンをdictで取得するテスト用ヘルパー。"""
        w = dv._restoreWidgets[uid]
        return {i + 1: w.layout().itemAt(i).widget() for i in range(3)}

    win.stack.setCurrentWidget(win.db); win.db.reload()
    btns1 = _restore_buttons(win.db, s1)
    assert btns1[1].isEnabled() and not btns1[2].isEnabled(), \
        "Restore button (embedded in this patient's row) reflects which slots are saved"
    assert btns1[1].styleSheet() != btns1[2].styleSheet(), \
        "saved vs empty restore slots must look visually different (setEnabled alone is invisible in this app's QSS)"
    # 行ボタンは「今選択中の行」に依存せず、そのボタン自身の患者を直接対象にする（先生指摘の根本修正）
    win.db.tree.clearSelection()
    got = []
    win.db.restoreSession.connect(lambda uid, n: got.append((uid, n)))
    btns1[1].click()
    assert got == [(s1, 1)], "row-embedded restore button must target its OWN patient regardless of tree selection"
    # --- v0.4.31: 保存スロットの削除（Save側／Restore側の両方から） ---
    from unittest.mock import patch
    win.stack.setCurrentWidget(win.viewer_page)
    win.current_study_uid = s1; win._current_files = st1["series"][0]["files"]
    assert win.catalog.has_session(s1, 1)
    with patch.object(main.QMessageBox, "question", return_value=main.QMessageBox.Yes):
        win._delete_slot(1)
    assert not win.catalog.has_session(s1, 1), "main._delete_slot removes the saved session"
    assert win.saveBtns[0].styleSheet() == win.saveBtns[1].styleSheet(), \
        "slot 1 button now looks like the empty slot 2"
    win._save_slot(3, notify=False)
    win.stack.setCurrentWidget(win.db); win.db.reload()      # 保存後は復元ボタンの表示更新のためreload必須
    btns1b = _restore_buttons(win.db, s1)
    assert btns1b[3].isEnabled()
    import database_view
    with patch.object(database_view.QMessageBox, "question", return_value=database_view.QMessageBox.Yes):
        win.db._on_restore_delete(s1, 3)
    assert not win.catalog.has_session(s1, 3), "database_view._on_restore_delete removes the saved session"
    btns1c = _restore_buttons(win.db, s1)
    assert not btns1c[3].isEnabled(), "restore button disabled after delete"
    import settings_store
    # --- 挿入方向(既定)は施設固定の設定としてSettingsメニューに集約・永続化される ---
    win._set_insertion_default(False)
    assert settings_store.store().value("insertion_default") == "jugular" and win.tipHighZ is False
    assert win.actInsFem.isChecked() is False and win.actInsJug.isChecked() is True
    win._set_insertion_default(True)
    assert settings_store.store().value("insertion_default") == "femoral" and win.tipHighZ is True
    # --- 設定の保存先(QStandardPaths.AppDataLocation)はQCoreApplication.applicationNameに依存し、
    # 未設定だと実行環境によって保存先が変わり得る（更新のたびに設定が消える不具合の原因になった）。
    # main()がこれを明示固定していることをソースから確認する（黙って削除されると実害が出るため回帰防止）。
    import inspect
    assert 'setApplicationName("TIPS ICE Planner")' in inspect.getsource(main.main), \
        "main() must pin applicationName so QStandardPaths.AppDataLocation (settings/catalog storage) " \
        "stays stable across environments and app updates"
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication as _QApp
    _QApp.instance().setApplicationName("TIPS ICE Planner")
    assert QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) \
        .endswith("TIPS ICE Planner"), "pinned applicationName must resolve to the real app's data folder"
    # --- 患者リスト/DICOMを開くは File メニューへ集約（下部の行からは撤去） ---
    file_menu = win.menuBar().actions()[0].menu()
    assert any(a.text() == "Patient list" for a in file_menu.actions())
    assert not hasattr(win, "langBtn"), "language button moved to Settings menu only"
    # --- v0.4.42: 閉じる時に状態保存を確認するダイアログ（Yes/No/Cancel） ---
    win.stack.setCurrentWidget(win.viewer_page)
    assert win.vol is not None and win.current_study_uid, "this point in the test must have a patient open"
    from PySide6.QtGui import QCloseEvent
    from unittest.mock import patch as _patch
    with _patch.object(main.QMessageBox, "question", return_value=main.QMessageBox.Cancel) as m_q, \
         _patch.object(main.QMessageBox, "information") as m_i:
        ev = QCloseEvent(); win.closeEvent(ev)
        assert not ev.isAccepted(), "Cancel must abort closing"
        assert m_i.call_count == 0, "Cancel must not save"
    with _patch.object(main.QMessageBox, "question", return_value=main.QMessageBox.Yes) as m_q, \
         _patch.object(main.QMessageBox, "information") as m_i:
        # 空きスロットへ自動保存され、「スロットN」の確認ポップアップが出る
        for n in (1, 2, 3):
            win.catalog.clear_session(win.current_study_uid, n)
        ev = QCloseEvent(); win.closeEvent(ev)
        assert ev.isAccepted(), "Yes must allow closing (after saving)"
        assert win.catalog.has_session(win.current_study_uid, 1), "Yes must save to the first empty slot"
        assert m_i.call_count == 1, "a save-slot popup must be shown"
    with _patch.object(main.QMessageBox, "question", return_value=main.QMessageBox.No) as m_q:
        ev = QCloseEvent(); win.closeEvent(ev)                 # No=保存せず閉じる（背景スレッド待ちも検証）
        assert ev.isAccepted()
    print("✅ GUI smoke tests passed (database / handoff / needle / ICE / 3D / transabdominal / trackpad / liver-ghost)")


if __name__ == "__main__":
    run()
def test_quit_while_worker_running_does_not_abort(qtbot=None):
    """Cmd+Q（QApplication.quit()）は closeEvent を呼ばない。走行中の QThread が破棄されると Qt は
    qFatal→abort() するので、aboutToQuit から _stop_workers() を必ず通す必要がある。
    実際に SIGABRT を再現したうえで入れた回帰テスト（別プロセスで終了コードを見る）。"""
    import subprocess, sys, os, textwrap
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prog = textwrap.dedent(f"""
        import os, sys, time
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, {app_dir!r})
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        app = QApplication.instance() or QApplication([])
        import main as M, bg
        win = M.MainWindow(); win.show()
        def slow(progress):
            t0 = time.time()
            while time.time() - t0 < 2.0:
                time.sleep(0.02)
        bg.run_with_progress(win, "busy", slow, lambda r: None)
        QTimer.singleShot(150, app.quit)
        app.exec()
    """)
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, timeout=60)
    assert r.returncode == 0, (
        f"quit-while-busy aborted (rc={r.returncode}; -6/134 = SIGABRT). "
        f"stderr tail: {r.stderr.decode()[-300:]}")


def test_new_patient_always_starts_at_step0():
    """先生報告の回帰テスト：前の患者でStep2(針)・Entry/Targetまで進めた状態のまま
    別の(未保存の)患者を開くと、stepが1のまま持ち越され「2. 穿刺針」モードで開いてしまい、
    IVCパスが無いためEntry/Target操作をしても画面に何も反映されず「ボタンが効かない」ように
    見えていた。_set_volume は患者ごとに毎回呼ばれるので、ここでstep/ptModeもリセットする。"""
    import dicom_io
    import main as M
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = tempfile.mkdtemp(prefix="tips_test_step_")
    dir_a = os.path.join(tmp, "a"); os.makedirs(dir_a)
    dir_b = os.path.join(tmp, "b"); os.makedirs(dir_b)
    su_a, se_a = generate_uid(), generate_uid()
    su_b, se_b = generate_uid(), generate_uid()
    for z in range(4):
        _slice(os.path.join(dir_a, f"a{z}.dcm"), su_a, se_a, z, "PatientA", 1, name="A^Pat", pid="PA")
    for z in range(4):
        _slice(os.path.join(dir_b, f"b{z}.dcm"), su_b, se_b, z, "PatientB", 1, name="B^Pat", pid="PB")

    win = M.MainWindow()
    vol_a = dicom_io.load_series(dir_a)
    win._set_volume(vol_a)
    win._set_step(1)                                     # 患者Aで「2. 穿刺針」まで進める
    win.entry = np.array([1.0, 1.0, 1.0]); win.target = np.array([2.0, 2.0, 2.0])
    assert win.step == 1

    vol_b = dicom_io.load_series(dir_b)                   # 患者B（未保存の新規患者）を開く
    win._set_volume(vol_b)
    assert win.step == 0, "新規患者はstep=0(1. ICEセットアップ)から始まるべき"
    assert win.ptMode == 0
    assert win.entry is None and win.target is None
    assert not win.needleRowW.isVisible(), "Step1では針操作行は畳まれているべき"


def test_surface_mode_switches_to_dedicated_probe_widget():
    """先生指定の回帰テスト：経腹（体表）モードに切り替えると、ICEモードのHandleControlではなく
    専用のSurfaceProbeControlへ操作ウィジェットが丸ごと切り替わること。ドラッグでTilt/Rock/Rotate
    (b1/b2/theta)が実際に動くこと（プローブ本体=Tilt/Rock、ダイヤル=Rotate）も検証する。"""
    import main as M
    from handle_control import SurfaceProbeControl, AXY
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    win = M.MainWindow(); win.show()
    win.stack.setCurrentWidget(win.viewer_page)          # 操作パネルはviewer_page側にある
    assert win.handleCtl.isVisible() and not win.surfCtl.isVisible(), "既定はICEモード=HandleControl表示"

    win._set_viewmode("surface")
    assert win.surfCtl.isVisible() and not win.handleCtl.isVisible(), "経腹モードではSurfaceProbeControlに切り替わる"

    win._set_viewmode("ice")
    assert win.handleCtl.isVisible() and not win.surfCtl.isVisible(), "ICEへ戻すとHandleControlに戻る"

    # --- SurfaceProbeControl単体でのドラッグ操作を検証 ---
    ctl = SurfaceProbeControl()
    ctl.resize(160, 108)
    got = {"b1": None, "b2": None, "theta": None}
    ctl.b1Changed.connect(lambda v: got.__setitem__("b1", v))
    ctl.b2Changed.connect(lambda v: got.__setitem__("b2", v))
    ctl.thetaChanged.connect(lambda v: got.__setitem__("theta", v))

    p0 = ctl._px(ctl._BODY_CX, AXY + 6.0)    # プローブ本体の中心付近
    ctl.mousePressEvent(type("E", (), {"position": lambda self=None: p0})())
    assert ctl._drag == "tilt_rock"
    p1 = QPointF(p0.x() + 8, p0.y() - 10)     # 右上へドラッグ = Rockプラス・Tiltプラス
    ctl.mouseMoveEvent(type("E", (), {"position": lambda self=None: p1})())
    assert got["b1"] is not None and got["b1"] > 0, "上ドラッグでTilt(b1)が増えるべき"
    assert got["b2"] is not None and got["b2"] > 0, "右ドラッグでRock(b2)が増えるべき"
    ctl.mouseReleaseEvent(type("E", (), {})())

    d0 = ctl._px(ctl._DIAL_CX, AXY)           # ひねりダイヤルの中心
    ctl.mousePressEvent(type("E", (), {"position": lambda self=None: d0})())
    assert ctl._drag == "theta"
    d1 = QPointF(d0.x() + 12, d0.y())
    ctl.mouseMoveEvent(type("E", (), {"position": lambda self=None: d1})())
    assert got["theta"] is not None, "ダイヤルの左右ドラッグでRotate(theta)が変化するべき"


def test_no_stray_top_level_windows():
    """親を持たない遺物ウィジェットが、デスクトップに小さな窓として出てしまわないこと。

    Qt では parent の無いウィジェットはトップレベルウィンドウになる。UIを Handle 方式に作り直した
    ときの残骸（sProbe / lblProbe / probeFoot / probeHead ほか計12個）はどのレイアウトにも入って
    おらず、_update_mode_ui() の setVisible(True) がそのまま "foot" "head" という小窓をデスクトップ
    に出していた（先生報告・実機で4個確認）。非表示の親に付けて封じたことの回帰テスト。
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("TIPS ICE Planner")
    import main as M
    win = M.MainWindow()
    win.show()
    for mode in ("ice", "surface", "ice"):          # 窓が湧くのはモード切替の setVisible
        win.viewMode = mode
        win._update_mode_ui()
        app.processEvents()

    def describe(w):                               # f-string の中に lambda は書けない
        text = w.text() if hasattr(w, "text") else ""
        return "{}(text={!r})".format(w.__class__.__name__, text)

    # 同じ pytest セッションの他テストが作った MainWindow も topLevelWidgets に残るので、
    # 「MainWindow でないトップレベル窓」だけを迷子とみなす。
    stray = [w for w in app.topLevelWidgets()
             if not isinstance(w, M.MainWindow) and not w.isHidden()]
    assert not stray, "MainWindow 以外のトップレベル窓が出ている: " + ", ".join(
        describe(w) for w in stray)

    orphans = [n for n in ("sTheta", "sProbe", "sB1", "sB2", "b1Val", "b2Val", "lblTheta",
                           "lblProbe", "probeFoot", "probeHead", "lblAP", "lblLR")
               if getattr(win, n).parent() is None]
    assert not orphans, "親を持たないウィジェットが残っている（いつでも窓になり得る）: {}".format(orphans)


def test_ivc_path_hidden_in_transabdominal_mode():
    """経腹モードでは IVC を通るカテーテル経路を 2D 断面に描かないこと。

    経腹ではプローブは体表にあり、IVC の中を通る経路は関係が無い。3Dペインは以前から
    shaft=None で隠していたのに、2D（Axial/Coronal/Sagittal）だけ描き続けていて画面が
    食い違っていた（先生指摘）。経路の有無で描画が変わらないことを確かめる。
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("TIPS ICE Planner")
    import main as M
    import dicom_io
    import glob

    files = sorted(glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "sample_data", "HCC048_portal_venous", "*.dcm")))
    if not files:
        return                                   # 同梱CTが無いクローンではスキップ

    win = M.MainWindow(); win.resize(1200, 800)
    try:
        win.current_study_uid = "t"; win._current_files = list(files)
        win._on_series_loaded(dicom_io.load_series_files(files))
        for _ in range(3):
            app.processEvents()
        win.show_liver = False                   # 肝抽出ワーカーを走らせない
        path = [[9.0, 237.0, 244.0], [40.0, 255.0, 245.0], [75.0, 268.0, 247.0]]

        def render(mode, p):
            win.viewMode = mode; win.path = [list(q) for q in p]
            win._update_mode_ui(); win._refresh()
            for _ in range(3):
                app.processEvents()
            win.cor.grab().save("/tmp/_ivc_test.png")
            from PIL import Image
            return np.array(Image.open("/tmp/_ivc_test.png").convert("RGB"), dtype=int)

        surf_with, surf_without = render("surface", path), render("surface", [])
        assert surf_with.shape == surf_without.shape
        assert int((np.abs(surf_with - surf_without) > 8).sum()) == 0, \
            "経腹モードなのに IVC 経路が描かれている"

        ice_with, ice_without = render("ice", path), render("ice", [])
        assert ice_with.shape == ice_without.shape
        assert int((np.abs(ice_with - ice_without) > 8).sum()) > 0, \
            "血管内モードで IVC 経路が描かれていない（消しすぎ）"
    finally:
        win._stop_workers()
