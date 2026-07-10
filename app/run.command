#!/bin/bash
# TIPS Planner standalone — ダブルクリックで起動（macOS）。
# 重要: Qt(PySide6) は **スペースを含むパス**ではプラグインを読めず起動時にクラッシュする。
#       そのため venv（Qt一式の置き場）は必ず **スペース無しのホーム配下** に作る。
#       コード本体はこのリポジトリ（スペース有りでも可）から実行する。
APP_DIR="$(cd "$(dirname "$0")" && pwd)"        # = <repo>/app
VENV="$HOME/.tips_planner/venv"                  # スペース無し（ここが肝）

PY="$(command -v python3.13 || echo /opt/homebrew/bin/python3.13)"
if [ ! -x "$PY" ]; then
  echo "Python 3.13 が必要です。Homebrew で: brew install python@3.13"
  echo "（PySide6 は Python 3.14 では現状動きません。3.13 を使ってください）"
  read -r -p "Enter で終了"; exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "[初回セットアップ] 仮想環境を作成中（$VENV）…"
  mkdir -p "$HOME/.tips_planner"
  "$PY" -m venv "$VENV" || { echo "venv作成に失敗"; read -r; exit 1; }
  "$VENV/bin/python" -m pip install -q --upgrade pip
  echo "[初回セットアップ] 依存パッケージを導入中（数分かかります）…"
  "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt" || { echo "依存導入に失敗"; read -r; exit 1; }
fi

exec "$VENV/bin/python" "$APP_DIR/main.py"
