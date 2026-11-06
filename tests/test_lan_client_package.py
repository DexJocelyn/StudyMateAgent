from zipfile import ZipFile
import json
import hashlib
import pytest
from scripts import package_lan_client as package


def test_client_package_contains_only_client_files_and_preserves_plain_commands(tmp_path,monkeypatch):
    monkeypatch.setattr(package,'ROOT',tmp_path)
    archive=package.build('http://192.168.50.10:8766')
    with ZipFile(archive) as z:
        assert z.testzip() is None
        names=z.namelist()
        assert not any('.env' in n or 'sqlite' in n or n.endswith('.exe') for n in names)
        assert z.read('课伴教室版/server.txt').decode().strip()=='http://192.168.50.10:8766'
        manifest=json.loads(z.read('课伴教室版/文件校验.json'))
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(z.read('课伴教室版/'+name)).hexdigest()==digest
        for name in names:
            if name.endswith('.cmd'):
                command=z.read(name).decode('utf8')
                assert '\r\n' in command and all(len(line)<8191 for line in command.splitlines())
                assert 'ExecutionPolicy' not in command and 'EncodedCommand' not in command
        client=z.read('课伴教室版/打开课伴.cmd').decode('utf8')
        assert '$request.Proxy=$null' in client and 'AllowAutoRedirect=$false' in client
        assert 'Read-Server $base' in client and '--app=' in client


@pytest.mark.parametrize('server',['http://8.8.8.8:8766','http://user:secret@192.168.1.1:8766','http://192.168.1.1:8766/path'])
def test_installer_default_host_rejects_credentials_and_public_hosts(tmp_path,monkeypatch,server):
    monkeypatch.setattr(package,'ROOT',tmp_path)
    with pytest.raises(ValueError):package.build(server)
