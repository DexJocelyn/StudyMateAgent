"""Mac classroom host: reuse the local app on a private interface, no login by default."""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.share_gateway import lan_address
from scripts.share_public import share_credentials, stop_child

FOLDER = ROOT / 'data' / 'lan'
STATUS = FOLDER / 'status.json'
STOP = FOLDER / 'stop.json'


def addresses():
    result = []
    for interface in ('en0', 'en1', 'en2', 'en3', 'en4', 'en5'):
        ip = subprocess.run(['/usr/sbin/ipconfig','getifaddr',interface],capture_output=True,text=True).stdout.strip()
        if lan_address(ip) and ip not in result:
            result.append(ip)
    return result


def read_status():
    try: return json.loads(STATUS.read_text())
    except (OSError, ValueError): return {}


def supervisor_active():
    FOLDER.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (FOLDER/'host.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        return False


def probe(url, credentials=None):
    request = urllib.request.Request(url + '/api/health')
    if credentials:
        token = base64.b64encode(':'.join(credentials).encode()).decode()
        request.add_header('Authorization','Basic ' + token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=2) as response:
            value=json.load(response)
        return value.get('ok') and value.get('mcp_tools',0)>0
    except (OSError, ValueError): return False


def write_status(value):
    temporary=STATUS.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    temporary.chmod(0o600); temporary.replace(STATUS)


def serve(args):
    FOLDER.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (FOLDER/'host.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        children=[]; outputs=[]
        state={'instance':uuid.uuid4().hex,'pid':os.getpid(),'state':'starting','url':f'http://{args.host}:{args.port}'}
        stopping=False
        def stop(*_):
            nonlocal stopping
            stopping=True
        for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,stop)
        def spawn(command,name,env):
            fd=os.open(FOLDER/name,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            output=os.fdopen(fd,'a');outputs.append(output)
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
            children.append(child);return child
        def ready(url,credentials=None,child=None):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if probe(url,credentials):return
                if stopping or (child and child.poll() is not None):break
                time.sleep(.25)
            raise RuntimeError('服务未就绪，请查看 data/lan 下的日志。')
        write_status(state)
        try:
            # Keep the host awake only while this supervisor is serving the classroom.
            spawn(['/usr/bin/caffeinate','-i','-w',str(os.getpid())],'awake.log',dict(os.environ))
            with socket.socket() as test: test.bind((args.host,args.port))
            origin=f'http://127.0.0.1:{args.origin_port}'
            if not probe(origin):
                env=dict(os.environ)
                env.update({'STUDYMATE_PUBLIC':'0','STUDYMATE_ACCESS_PASSWORD':''})
                child=spawn([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(args.origin_port)],'origin.log',env)
                ready(origin,child=child)
            credentials=share_credentials() if args.require_password else None
            env={k:v for k,v in os.environ.items() if k not in ('MODEL_API_KEY','TAVILY_API_KEY','STUDYMATE_ACCESS_PASSWORD','STUDYMATE_ACCESS_USER')}
            env.update({'STUDYMATE_PUBLIC':'1' if args.require_password else '0',
                        'STUDYMATE_ORIGIN_PORT':str(args.origin_port),'STUDYMATE_LAN_HOST':args.host,
                        'STUDYMATE_LAN_NO_AUTH':'0' if args.require_password else '1'})
            if credentials:env.update(STUDYMATE_ACCESS_USER=credentials[0],STUDYMATE_ACCESS_PASSWORD=credentials[1])
            child=spawn([sys.executable,'-m','uvicorn','backend.share_gateway:app','--host',args.host,
                         '--port',str(args.port),'--no-proxy-headers'],'gateway.log',env)
            ready(state['url'],credentials,child)
            state.update(state='running',auth_required=args.require_password)
            if credentials:state.update(username=credentials[0],credentials_file=str(ROOT/'.env.share'))
            write_status(state)
            while not stopping:
                try: requested=json.loads(STOP.read_text())
                except (OSError,ValueError):requested={}
                if requested.get('instance')==state['instance']:break
                if child.poll() is not None:raise RuntimeError('局域网入口已停止，请重新启动。')
                time.sleep(.5)
        except Exception as exc:
            state.update(state='error',message=str(exc));write_status(state)
        finally:
            for child in reversed(children):stop_child(child)
            for output in outputs:output.close()
            if state['state']!='error':state['state']='stopped';write_status(state)


def main():
    parser=argparse.ArgumentParser(description='课伴教室局域网共享，不使用公网隧道。')
    parser.add_argument('action',choices=['start','stop','status','serve'],default='start',nargs='?')
    parser.add_argument('--host');parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--origin-port',type=int,default=8000)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--require-password',action='store_true',help='可选：为教室入口开启小组密码')
    args=parser.parse_args()
    if args.action=='status':print(json.dumps(read_status(),ensure_ascii=False,indent=2));return
    if args.action=='stop':
        state=read_status()
        if not supervisor_active():
            if state:state['state']='stopped';write_status(state)
            print('局域网入口当前未运行。');return
        if state.get('state') not in ('running','starting'):print('局域网入口当前未运行。');return
        STOP.write_text(json.dumps({'instance':state['instance']}));STOP.chmod(0o600)
        for _ in range(40):
            if read_status().get('state') not in ('running','starting'):break
            time.sleep(.25)
        else:raise SystemExit('仍在等待正在执行的操作结束，请稍后检查状态。')
        print('局域网分享已停止；原本就在运行的本机课伴会保留。');return
    if not 1<=args.port<=65535 or not 1<=args.origin_port<=65535 or args.port==args.origin_port:
        raise SystemExit('请输入两个不同的有效端口。')
    FOLDER.mkdir(parents=True,exist_ok=True,mode=0o700)
    state=read_status()
    candidates=addresses()
    credentials=share_credentials() if args.require_password else None
    if args.action=='start' and state.get('state')=='running' and state.get('auth_required',True)==args.require_password and state.get('url','').split('//')[-1].split(':')[0] in candidates and probe(state['url'],credentials):
        print('局域网服务已经启动：'+state['url'])
        if not args.no_browser:webbrowser.open(state['url'])
        return
    if args.action=='start' and state.get('state') in ('running','starting') and supervisor_active():
        # An old listener can survive a Wi-Fi/IP change; ask only our supervisor to stop.
        STOP.write_text(json.dumps({'instance':state['instance']}));STOP.chmod(0o600)
        for _ in range(80):
            if read_status().get('state') not in ('running','starting'):break
            time.sleep(.25)
        else:raise SystemExit('旧入口正在结束，请稍后重试。')
    args.host=args.host or (candidates[0] if candidates else '')
    if not lan_address(args.host) or args.host not in candidates:
        raise SystemExit('没有检测到可用的内网地址。请连接教室Wi-Fi或同一个热点后重试。')
    if args.action=='serve':serve(args);return
    command=[sys.executable,str(Path(__file__).resolve()),'serve','--host',args.host,'--port',str(args.port),'--origin-port',str(args.origin_port)]
    if args.require_password:command.append('--require-password')
    fd=os.open(FOLDER/'supervisor.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as output:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(120):
        state=read_status()
        if state.get('state')=='running' and state.get('auth_required',True)==args.require_password and probe(state['url'],credentials):break
        if child.poll() is not None or (state.get('pid')==child.pid and state.get('state')=='error'):
            raise SystemExit(state.get('message','启动失败，请查看 data/lan/supervisor.log。'))
        time.sleep(.25)
    else:raise SystemExit('启动超时，请查看 data/lan 日志。')
    print('课伴教室共享已开启：'+state['url'])
    print('Windows组员填写此地址，打开后选择自己的姓名即可，无需账号密码。' if not args.require_password else '访问账号和密码在主机的 .env.share 文件。')
    print('主机需保持开机联网；换Wi-Fi后重新启动分享，并更新组员客户端地址。')
    if not args.no_browser:webbrowser.open(state['url'])


if __name__=='__main__':main()
