#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  runtime=""
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      runtime="$candidate"
      break
    fi
  done
  if [[ -z "$runtime" ]]; then
    echo '请先安装 Python 3.11 或以上版本，详见 README.md。'
    read 'reply?按回车退出…'
    exit 1
  fi
  "$runtime" -m venv .venv
  .venv/bin/python -m pip install -r requirements.lock
fi
.venv/bin/python scripts/check_env.py
echo '\n课伴正在启动，请打开 http://127.0.0.1:8000\n保留此终端窗口，按 Ctrl+C 停止服务。'
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
