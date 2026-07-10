#!/bin/bash
# "Send to ICE Planner" Database プラグイン ビルド＆インストール（ローカル完結・ネット不要）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="/Applications/miele-lxiv.app/Contents/MacOS/miele-lxiv"
PLUG_DIR="$HOME/Library/Containers/com.bettarini.miele-lxiv/Data/Library/Application Support/miele-lxiv/Plugins"
# Build in /tmp: some synced folders (Dropbox/iCloud/Obsidian vaults) fight over xattrs and break codesign.
BUNDLE="/tmp/tips-send-build/TIPSSend.mieleplugin"
SIGN_ID="${SIGN_ID:--}"   # 既定はad-hoc。配布時は SIGN_ID="Developer ID Application: ..." で再実行→公証

echo "[1] バンドル骨組み作成"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
cp "$HERE/Info.plist" "$BUNDLE/Contents/Info.plist"

echo "[2] コンパイル（universal: arm64 + x86_64, -bundle_loader で本体exeへ結線）"
xcrun clang -bundle -fobjc-arc -Wall -Wextra \
    -arch arm64 -arch x86_64 \
    -mmacosx-version-min=10.15 \
    -bundle_loader "$HOST" \
    -framework Cocoa \
    -o "$BUNDLE/Contents/MacOS/TIPSSend" \
    "$HERE/TIPSSendFilter.m"

echo "[3] 拡張属性を除去して署名（${SIGN_ID}）"
xattr -cr "$BUNDLE"
codesign --force --deep --sign "$SIGN_ID" "$BUNDLE"

echo "[4] 検証"
echo "  -- file --"; file "$BUNDLE/Contents/MacOS/TIPSSend"
echo "  -- codesign --"; codesign -dvv "$BUNDLE" 2>&1 | grep -iE "Identifier|Authority|Signature|flags" || true
echo "  -- PluginFilter は未定義(=ロード時に本体が結線) --"; nm -u "$BUNDLE/Contents/MacOS/TIPSSend" | grep -i PluginFilter || true

echo "[5] インストール → $PLUG_DIR"
mkdir -p "$PLUG_DIR"
rm -rf "$PLUG_DIR/TIPSSend.mieleplugin"
cp -R "$BUNDLE" "$PLUG_DIR/"
echo "✅ 完了。Miele-LXIV を再起動 → 患者リストでスタディを1つ選択 →"
echo "   メニュー Plugins ▸ Database ▸ \"Send to ICE Planner\" で TIPS ICE Planner が開くか確認してください。"
