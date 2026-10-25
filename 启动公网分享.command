#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo '请先按README安装Python并启动一次课伴，再开启公网分享。'
  read 'reply?按回车退出…'
  exit 1
fi
exec .venv/bin/python scripts/share_public.py
