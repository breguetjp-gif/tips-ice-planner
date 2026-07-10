#!/bin/bash
# TIPS Planner プラグイン ビルド＆インストール（ローカル完結・ネット不要）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="/Applications/miele-lxiv.app/Contents/MacOS/miele-lxiv"
PLUG_DIR="$HOME/Library/Containers/com.bettarini.miele-lxiv/Data/Library/Application Support/miele-lxiv/Plugins"
# Build in /tmp: some synced folders (Dropbox/iCloud/Obsidian vaults) fight over xattrs and break codesign.
BUNDLE="/tmp/tips-planner-build/TIPSPlanner.mieleplugin"
SIGN_ID="${SIGN_ID:--}"   # 既定はad-hoc。失敗時は SIGN_ID="Apple Development: ..." で再実行

echo "[1] バンドル骨組み作成"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
cp "$HERE/Info.plist" "$BUNDLE/Contents/Info.plist"

echo "[2] コンパイル（universal: arm64 + x86_64, -bundle_loader で本体exeへ結線）"
xcrun clang -bundle -fobjc-arc \
    -arch arm64 -arch x86_64 \
    -mmacosx-version-min=10.15 \
    -bundle_loader "$HOST" \
    -framework Cocoa \
    -o "$BUNDLE/Contents/MacOS/TIPSPlanner" \
    "$HERE/TIPSPlannerFilter.m"

echo "[3] 拡張属性を除去して署名（${SIGN_ID}）"
xattr -cr "$BUNDLE"
codesign --force --deep --sign "$SIGN_ID" "$BUNDLE"

echo "[4] 検証"
echo "  -- otool -L --"; otool -L "$BUNDLE/Contents/MacOS/TIPSPlanner" | grep -iE "miele|bundle_loader|architecture" || true
file "$BUNDLE/Contents/MacOS/TIPSPlanner"
echo "  -- codesign --"; codesign -dvv "$BUNDLE" 2>&1 | grep -iE "Identifier|Authority|Signature|flags" || true
echo "  -- PluginFilter は未定義(=ロード時に本体が結線) --"; nm -u "$BUNDLE/Contents/MacOS/TIPSPlanner" | grep -i PluginFilter || true

echo "[5] インストール → $PLUG_DIR"
mkdir -p "$PLUG_DIR"
rm -rf "$PLUG_DIR/TIPSPlanner.mieleplugin"
cp -R "$BUNDLE" "$PLUG_DIR/"
echo "✅ 完了。Miele-LXIV を起動し、画像を1つ開いて Plugins メニューに「TIPS Planner」が出るか確認してください。"
