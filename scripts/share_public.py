"""免费临时公网分享：本机课伴 -> 有密码的回环代理 -> Cloudflare Quick Tunnel。"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import base64
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SHARE_ENV = ROOT / '.env.share'


def share_credentials():
    if SHARE_ENV.is_symlink():
        raise RuntimeError('访问配置不能是符号链接，请检查.env.share。')
    if not SHARE_ENV.exists():
        password = secrets.token_urlsafe(24)
        fd = os.open(SHARE_ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(f'STUDYMATE_PUBLIC=1\nSTUDYMATE_ACCESS_USER=group\nSTUDYMATE_ACCESS_PASSWORD={password}\n')
    os.chmod(SHARE_ENV, 0o600)
    config = dotenv_values(SHARE_ENV, interpolate=False)
    user, password = config.get('STUDYMATE_ACCESS_USER', 'group'), config.get('STUDYMATE_ACCESS_PASSWORD', '')
    if not password or len(password) < 16:
        raise RuntimeError('小组访问密码未配置或不足16位，请在本机.env.share中设置。')
    return user, password


def probe(port, user=None, password=None):
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health')
    if user is not None:
        token = base64.b64encode(f'{user}:{password}'.encode()).decode()
        request.add_header('Authorization', 'Basic ' + token)
    # 只检查回环服务，不经系统HTTP代理，避免向代理传递访问凭据。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        data = json.load(response)
    return data.get('ok') and data.get('mcp_tools', 0) > 0 and data.get('project_id')


def stop_child(process):
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT if os.name != 'nt' else signal.SIGTERM)
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()


def main():
    parser = argparse.ArgumentParser(description='开启有密码保护的免费临时公网分享。')
    parser.add_argument('--origin-port', type=int, default=8000)
    parser.add_argument('--gateway-port', type=int, default=8765)
    args = parser.parse_args()
    if any(not 1 <= port <= 65535 for port in (args.origin_port, args.gateway_port)) or args.origin_port == args.gateway_port:
        raise SystemExit('本机服务和分享入口需使用不同的有效端口。')
    with socket.socket() as check:
        try: check.bind(('127.0.0.1', args.gateway_port))
        except OSError: raise SystemExit('公网分享端口已在使用；若已开启分享，请查看data/sharing/status.json中的现有地址。')
    binary = ROOT / '.tools' / ('cloudflared.exe' if os.name == 'nt' else 'cloudflared')
    if not binary.exists():
        subprocess.run([sys.executable, str(ROOT / 'scripts/install_cloudflared.py')], check=True)
    folder = ROOT / 'data' / 'sharing'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    children, outputs = [], []
    status_path = folder / 'status.json'
    metadata = {'running': False, 'url': None, 'credentials_file': str(SHARE_ENV)}
    def status():
        status_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        os.chmod(status_path, 0o600)
    def spawn(command, name, env):
        log = folder / name
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        output = os.fdopen(fd, 'w', encoding='utf-8'); outputs.append(output)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        children.append(process); return process
    try:
        try: healthy = probe(args.origin_port)
        except urllib.error.URLError: healthy = False
        if not healthy:
            env = dict(os.environ); env['STUDYMATE_PUBLIC'] = '0'; env.pop('STUDYMATE_ACCESS_PASSWORD', None)
            process = spawn([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(args.origin_port)], 'origin.log', env)
            for _ in range(60):
                try:
                    if probe(args.origin_port): break
                except urllib.error.URLError: pass
                if process.poll() is not None: raise RuntimeError('本机课伴未能启动，请查看data/sharing/origin.log。')
                time.sleep(.25)
            else: raise RuntimeError('本机课伴启动超时。')
        user, password = share_credentials()
        env = dict(os.environ)
        env.update({'STUDYMATE_PUBLIC':'1', 'STUDYMATE_ACCESS_USER':user, 'STUDYMATE_ACCESS_PASSWORD':password,
                    'STUDYMATE_ORIGIN_PORT':str(args.origin_port)})
        env.pop('MODEL_API_KEY', None)
        env.pop('TAVILY_API_KEY', None)
        gateway = spawn([sys.executable, '-m', 'uvicorn', 'backend.share_gateway:app', '--host', '127.0.0.1',
                         '--port', str(args.gateway_port), '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'], 'gateway.log', env)
        for _ in range(60):
            try:
                if probe(args.gateway_port, user, password): break
            except urllib.error.URLError: pass
            if gateway.poll() is not None: raise RuntimeError('密码保护入口未能启动，请查看data/sharing/gateway.log。')
            time.sleep(.25)
        else: raise RuntimeError('密码保护入口启动超时。')
        # 不把模型密钥、小组密码或自动服务安装传给隧道进程。
        tunnel_env = {key:value for key,value in os.environ.items() if key not in ('MODEL_API_KEY','TAVILY_API_KEY','STUDYMATE_ACCESS_PASSWORD')}
        tunnel = spawn([str(binary), 'tunnel', '--url', f'http://127.0.0.1:{args.gateway_port}',
                        '--protocol', 'http2', '--no-autoupdate'], 'tunnel.log', tunnel_env)
        print('密码保护已就绪，正在申请免费临时公网链接…', flush=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            log = (folder / 'tunnel.log').read_text(encoding='utf-8', errors='replace')
            urls = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', log)
            if urls:
                metadata.update({'running':True, 'url':urls[-1], 'gateway_pid':gateway.pid, 'tunnel_pid':tunnel.pid})
                status()
                print(f'公网地址：{urls[-1]}\n小组访问用户名：{user}\n密码请在本机文件中复制：{SHARE_ENV}\n'
                      '保持电脑开机联网，保留此窗口；按Ctrl+C停止分享。本机原有课伴服务继续保留。', flush=True)
                break
            if tunnel.poll() is not None: raise RuntimeError('免费隧道未能建立，请查看data/sharing/tunnel.log。')
            time.sleep(.5)
        else: raise RuntimeError('申请链接超时，校园网可能无法连接该免费服务，请查看data/sharing/tunnel.log。')
        while True:
            if gateway.poll() is not None or tunnel.poll() is not None:
                raise RuntimeError('公网分享连接已结束，请重新启动分享。')
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n正在停止公网分享…', flush=True)
    finally:
        for process in reversed(children): stop_child(process)
        for output in outputs: output.close()
        metadata['running'] = False; status()


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
