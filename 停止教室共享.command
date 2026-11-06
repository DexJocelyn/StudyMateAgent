#!/bin/zsh
set -e
cd "$(dirname "$0")"
.venv/bin/python scripts/lan_host.py stop
read 'reply?按回车关闭…'
