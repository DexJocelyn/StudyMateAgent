#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo '请先安装本机课伴运行环境，再使用教室共享。'
  read 'reply?按回车退出…'
  exit 1
fi
.venv/bin/python scripts/lan_host.py start
echo '共享已在后台运行，可以关闭此窗口。组员用Windows客户端连接上面显示的地址。'
read 'reply?按回车关闭…'
