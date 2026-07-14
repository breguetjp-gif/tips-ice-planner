# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — TIPS ICE Planner（Mac .app / Windows .exe 共通）。
  Mac:  pyinstaller --noconfirm tips_ice.spec  → dist/TIPS ICE Planner.app
  Win:  同コマンド                              → dist/TIPS ICE Planner/TIPS ICE Planner.exe
"""
import os
import re
import sys
from PyInstaller.utils.hooks import collect_all

# バージョンは main.py の VERSION だけを正とする。ここに数字を直接書くと必ず main.py と食い違い、
# 「更新したのに .app が古いバージョンを名乗る」事故になる（v0.5.2 のビルドが 0.4.58 と名乗った）。
_APP_VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                         open(os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'main.py')).read(),
                         re.M).group(1)

# The manual PDFs are built from docs/manual_src*.html plus screenshots taken on a public CT
# dataset; they are absent from a fresh clone. Bundle whatever is present.
_candidates = [('icon_1024.png', '.'), ('docs/manual_ja.pdf', 'docs'),
               ('docs/manual_en.pdf', 'docs')]
datas = [(src, dst) for src, dst in _candidates if os.path.exists(src)]
binaries = []
hidden = ['catalog', 'database_view', 'bg', 'dicom_io', 'updater', 'i18n', 'settings_store', 'handle_control', 'tips_core', 'tips_core.geometry', 'tips_core.liver']
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
                     'CFBundleShortVersionString': _APP_VERSION,
                     'LSMinimumSystemVersion': '11.0',
                     # 外部アプリから検査を渡すためのURLスキーム（tipsiceplanner://open?dir=...）
                     'CFBundleURLTypes': [{
                         'CFBundleURLName': 'com.bonchan.tips-ice-planner.open',
                         'CFBundleURLSchemes': ['tipsiceplanner'],
                     }],
                 })
