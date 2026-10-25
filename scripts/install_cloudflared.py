"""只从Cloudflare官方发布下载固定版本，校验SHA256后放进本项目.tools。"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
VERSION = '2026.10.3'
RELEASES = {('Darwin', 'arm64'): 'cloudflared-darwin-arm64.tgz',
            ('Darwin', 'x86_64'): 'cloudflared-darwin-amd64.tgz',
            ('Linux', 'x86_64'): 'cloudflared-linux-amd64',
            ('Linux', 'aarch64'): 'cloudflared-linux-arm64',
            ('Windows', 'AMD64'): 'cloudflared-windows-amd64.exe'}


def main():
    choice = RELEASES.get((platform.system(), platform.machine()))
    if not choice:
        raise SystemExit('此设备需按Cloudflare官方说明手动安装cloudflared。')
    asset = choice
    url = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{asset}'
    # 发布正文可能保留旧摘要；取GitHub官方API中当前附件的digest，再校验实际下载。
    metadata_url = f'https://api.github.com/repos/cloudflare/cloudflared/releases/tags/{VERSION}'
    request = urllib.request.Request(metadata_url, headers={'Accept':'application/vnd.github+json', 'User-Agent':'StudyMate-course-project'})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    metadata = next((item for item in release.get('assets', []) if item['name'] == asset), None)
    if not metadata or metadata.get('browser_download_url') != url or not re.fullmatch(r'sha256:[a-f0-9]{64}', metadata.get('digest', '')):
        raise SystemExit('无法取得官方附件校验信息，没有下载或安装。')
    expected = metadata['digest'].split(':',1)[1]
    print(f'正在下载Cloudflare官方cloudflared {VERSION}，不需要购买或登录账号。', flush=True)
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read()
    if len(data) != metadata['size'] or hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit('官方发布文件SHA256校验未通过，没有安装或运行。')
    if asset.endswith('.tgz'):
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            member = next(m for m in archive.getmembers() if m.isfile() and Path(m.name).name == 'cloudflared')
            data = archive.extractfile(member).read()
    folder = ROOT / '.tools'
    folder.mkdir(exist_ok=True)
    destination = folder / ('cloudflared.exe' if platform.system() == 'Windows' else 'cloudflared')
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    os.chmod(temporary, 0o755)
    temporary.replace(destination)
    print(f'校验通过，已安装到 {destination}。')


if __name__ == '__main__':
    main()
