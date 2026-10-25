"""官方下载校验，伪造小附件验证流程，不下载或执行程序。"""
import hashlib
import io
import json
import tarfile
import pytest
from scripts import install_cloudflared as installer


def setup(monkeypatch,tmp_path,wrong_digest=False):
    monkeypatch.setattr(installer,'ROOT',tmp_path)
    monkeypatch.setattr(installer.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(installer.platform,'machine',lambda:'arm64')
    payload=b'unit-test-binary-never-run'
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as t:
        member=tarfile.TarInfo('cloudflared'); member.size=len(payload); t.addfile(member,io.BytesIO(payload))
    data=buffer.getvalue()
    url=f'https://github.com/cloudflare/cloudflared/releases/download/{installer.VERSION}/cloudflared-darwin-arm64.tgz'
    metadata={'assets':[{'name':'cloudflared-darwin-arm64.tgz','browser_download_url':url,'size':len(data),
        'digest':'sha256:'+('0'*64 if wrong_digest else hashlib.sha256(data).hexdigest())}]}
    def open_url(request,timeout):
        actual=request.full_url if hasattr(request,'full_url') else request
        return io.BytesIO(json.dumps(metadata).encode() if actual.startswith('https://api.github.com/') else data)
    monkeypatch.setattr(installer.urllib.request,'urlopen',open_url)
    return payload


def test_official_attachment_digest_is_verified_before_install(monkeypatch,tmp_path):
    payload=setup(monkeypatch,tmp_path)
    installer.main()
    assert (tmp_path/'.tools/cloudflared').read_bytes() == payload


def test_changed_download_is_not_installed(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path,wrong_digest=True)
    with pytest.raises(SystemExit,match='校验未通过'): installer.main()
    assert not (tmp_path/'.tools/cloudflared').exists()
