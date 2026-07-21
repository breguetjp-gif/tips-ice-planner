# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — TIPS ICE Planner（Mac .app / Windows .exe 共通）。
  Mac:  pyinstaller --noconfirm tips_ice.spec  → dist/TIPS ICE Planner.app
  Win:  同コマンド                              → dist/TIPS ICE Planner/TIPS ICE Planner.exe
"""
import sys, os, glob, re
# アプリのバージョンは main.py の VERSION が唯一の正（Info.plist へもここから注入・2026-07-18）
APP_VERSION = re.search(r'^VERSION = "([^"]+)"',
                        open(os.path.join(SPECPATH, "main.py"), encoding="utf-8").read(), re.M).group(1)
from PyInstaller.utils.hooks import collect_all

datas = [('icon_1024.png', '.'), ('docs/manual_ja.pdf', 'docs'), ('docs/manual_en.pdf', 'docs'),
         ('ts_helper.py', '.')]      # TotalSegmentator橋渡し（研究用venvのpythonが実行する外部スクリプト）
# 配布アプリに常に入れる公開サンプルCT（TCIA HCC-TACE-Seg / HCC048・CC BY 4.0）。
# app/sample_data か、無ければリポジトリ直下 sample_data（公開版レイアウト）から拾う。
for _root in ('sample_data', os.path.join('..', 'sample_data')):
    _sd = os.path.join(_root, 'HCC048_portal_venous')
    if os.path.isdir(_sd):
        datas += [(f, 'sample_data/HCC048_portal_venous') for f in glob.glob(os.path.join(_sd, '*.dcm'))]
        _attr = os.path.join(_root, 'ATTRIBUTION.md')
        if os.path.exists(_attr):
            datas += [(_attr, 'sample_data')]
        _npz = os.path.join(_root, 'HCC048_portal_venous.ai.npz')   # 事前計算のAIマスク（Patient_01のAI 3Dを同梱）
        if os.path.exists(_npz):
            datas += [(_npz, 'sample_data')]
        break
binaries = []
hidden = ['catalog', 'database_view', 'bg', 'dicom_io', 'updater', 'i18n', 'settings_store', 'handle_control', 'ts_seg', 'tips_core', 'tips_core.geometry', 'tips_core.liver']
# DICOM デコーダ群（ネイティブlib込みで同梱）
for pkg in ['pydicom', 'gdcm', 'pylibjpeg', 'pylibjpeg_libjpeg', 'pylibjpeg_openjpeg']:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception as e:                       # pragma: no cover
        print('collect_all skipped:', pkg, e)

a = Analysis(
    ['main.py'], pathex=['.'], binaries=binaries, datas=datas, hiddenimports=hidden,
    excludes=['matplotlib', 'tkinter', 'scipy', 'PySide6.QtWebEngineCore', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtPdf'],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = 'icon.icns' if sys.platform == 'darwin' else 'icon.ico'
exe = EXE(pyz, a.scripts, exclude_binaries=True, name='TIPS ICE Planner',
          console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TIPS ICE Planner')

if sys.platform == 'darwin':
    app = BUNDLE(coll, name='TIPS ICE Planner.app', icon='icon.icns',
                 bundle_identifier='com.bonchan.tips-ice-planner',
                 info_plist={
                     'NSHighResolutionCapable': True,
                     'CFBundleShortVersionString': APP_VERSION,
                     'LSMinimumSystemVersion': '11.0',
                     # Mieleプラグイン→本アプリの橋渡し用URLスキーム（tipsiceplanner://open?dir=...）
                     'CFBundleURLTypes': [{
                         'CFBundleURLName': 'com.bonchan.tips-ice-planner.open',
                         'CFBundleURLSchemes': ['tipsiceplanner'],
                     }],
                 })
