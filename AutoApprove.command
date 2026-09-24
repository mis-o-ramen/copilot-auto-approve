#!/bin/sh
# macOS 用ランチャー: Finder からダブルクリックで GUI を起動する (起動後このウィンドウは閉じてよい)
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
nohup "$PY" auto_approve_gui.py >/dev/null 2>&1 &
