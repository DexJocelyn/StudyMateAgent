"""Windows启动助手：为新电脑创建自己的虚拟环境，不复制Mac虚拟环境。"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def environment_python(root=ROOT):
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare():
    if sys.version_info < (3, 11):
        raise RuntimeError('请先安装Python3.11或以上版本，建议使用Python3.12。')
    python = environment_python()
    if not python.exists():
        print('首次启动：正在创建此电脑的Python环境…', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    fingerprint = hashlib.sha256((ROOT / 'requirements.lock').read_bytes()).hexdigest()
    marker = ROOT / '.venv' / 'studymate-dependencies.sha256'
    if not marker.exists() or marker.read_text().strip() != fingerprint:
        print('正在安装项目依赖，首次可能需要几分钟…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.lock')], check=True)
        marker.write_text(fingerprint, encoding='utf-8')
    subprocess.run([str(python), str(ROOT / 'scripts/check_env.py')], check=True)
    return python


def main():
    parser = argparse.ArgumentParser(description='在本机创建环境并启动课伴。')
    parser.add_argument('--share', action='store_true', help='开启有访问密码的免费临时公网链接')
    args = parser.parse_args()
    os.chdir(ROOT)
    python = prepare()
    if args.share:
        command = [str(python), str(ROOT / 'scripts/share_public.py')]
    else:
        print('课伴启动后请打开 http://127.0.0.1:8000/ ，保留此窗口；按Ctrl+C停止。', flush=True)
        command = [str(python), '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000']
    try:
        subprocess.run(command, check=True)
    except KeyboardInterrupt:
        print('\n课伴启动窗口已停止。', flush=True)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
