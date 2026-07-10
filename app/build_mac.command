#!/bin/bash
# TIPS ICE Planner — Mac配布ビルド（PyInstaller → .app）。要 ~/.tips_planner/venv
cd "$(dirname "$0")" || exit 1
VENV="$HOME/.tips_planner/venv"
"$VENV/bin/pip" install -q pyinstaller >/dev/null 2>&1
rm -rf build "dist/TIPS ICE Planner.app"
"$VENV/bin/pyinstaller" --noconfirm tips_ice.spec || { echo "build失敗"; read -r; exit 1; }
# Optional code signing. Export your own identity before running, e.g.
#   export TIPS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
# If unset (or not found in the keychain), the app is ad-hoc signed, which is fine for local use.
DEVID="${TIPS_SIGN_IDENTITY:-}"
if [ -n "$DEVID" ] && security find-identity -v -p codesigning | grep -qF "$DEVID"; then
  echo "[sign] Developer ID (notarize separately with notarytool)"
  codesign --force --deep --options runtime --sign "$DEVID" "dist/TIPS ICE Planner.app" 2>/dev/null || codesign --force --deep -s - "dist/TIPS ICE Planner.app"
else
  echo "[sign] ad-hoc"
  codesign --force --deep -s - "dist/TIPS ICE Planner.app"
fi
echo "✅ dist/TIPS ICE Planner.app"
