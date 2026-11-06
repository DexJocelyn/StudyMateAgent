import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from dotenv import dotenv_values
from scripts import package_private_demo as private


def test_private_demo_includes_only_authorized_model_credentials(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir()
    (root/'.env').write_text('MODEL_API_KEY=test-private-model\nMODEL_BASE_URL=https://example.test/v1\nMODEL_NAME=test-model\nTAVILY_API_KEY=not-authorized-search\nSTUDYMATE_DATA_DIR=/private/old/data\n')
    def public(destination,root):
        with ZipFile(destination,'w') as z:
            for name,value in {'backend/main.py':'safe source','文件校验.json':'{}','README-先看这里.md':'old guide',
                               '1-配置自己的模型-Windows.bat':'old config','2-启动课伴-Windows.bat':'old launcher'}.items():
                z.writestr('StudyMate/'+name,value)
        return destination
    monkeypatch.setattr(private,'build_public',public)
    out=tmp_path/'private.zip'
    with pytest.raises(ValueError):private.build(out,root=root)
    assert not out.exists()
    private.build(out,root=root,include_model_key=True)
    with ZipFile(out) as z:
        contents=[z.read(n) for n in z.namelist()]
        config=dotenv_values(stream=__import__('io').StringIO(z.read('StudyMate/.env').decode()),interpolate=False)
        assert config['AGENT_MODE']=='llm' and config['MODEL_API_KEY']=='test-private-model'
        assert config['MODEL_NAME']=='test-model' and config['MODEL_BASE_URL']=='https://example.test/v1'
        assert config['TAVILY_API_KEY']=='' and config['WEB_SEARCH_ENABLED']=='0'
        assert not any(b'not-authorized-search' in c or b'/private/old/data' in c for c in contents)
        assert sum(b'test-private-model' in c for c in contents)==1
        assert 'StudyMate/1-配置自己的模型-Windows.bat' not in z.namelist()
        assert b'--configure' not in z.read('StudyMate/启动课伴-Windows.bat')
        manifest=json.loads(z.read('StudyMate/文件校验.json'))
        assert manifest['contains_model_api_key'] and not manifest['contains_host_database']
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(z.read('StudyMate/'+name)).hexdigest()==digest
    assert out.stat().st_mode&0o077==0
    with pytest.raises(FileExistsError):private.build(out,root=root,include_model_key=True)


def test_missing_model_key_does_not_create_private_archive(tmp_path):
    (tmp_path/'.env').write_text('AGENT_MODE=demo\n')
    with pytest.raises(ValueError):private.build(tmp_path/'private.zip',root=tmp_path,include_model_key=True)
    assert not (tmp_path/'private.zip').exists()
