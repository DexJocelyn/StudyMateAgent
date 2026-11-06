import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from dotenv import dotenv_values
from scripts import configure_model as config
from scripts import package_members as package


def values(**updates):
    return dict(AGENT_MODE='llm',MODEL_BASE_URL='https://example.test/v1',MODEL_NAME='test-model',
                MODEL_API_KEY='test-only-key',**updates)


def test_config_preserves_other_settings_and_saves_quoted_keys(tmp_path,capsys):
    path=tmp_path/'.env'
    path.write_text('TAVILY_API_KEY=existing-search\n# keep comment\nMODEL_NAME=old\nMODEL_NAME=duplicate\nWEATHER_ENABLED=0\n')
    answers=iter(['1','https://example.test/v1','my-model'])
    key='test-quote-"and\\slash'
    secrets=iter([key,''])
    config.configure(tmp_path,prompt=lambda _:next(answers),secret=lambda _:next(secrets))
    actual=dotenv_values(path,interpolate=False)
    assert actual['MODEL_API_KEY']==key and actual['MODEL_NAME']=='my-model'
    assert actual['TAVILY_API_KEY']=='existing-search' and actual['WEATHER_ENABLED']=='0'
    assert path.read_text().count('MODEL_NAME=')==1
    assert key not in capsys.readouterr().out
    # Pressing enter retains an existing model key.
    answers=iter(['1','','']);secrets=iter(['',''])
    config.configure(tmp_path,prompt=lambda _:next(answers),secret=lambda _:next(secrets))
    assert dotenv_values(path,interpolate=False)['MODEL_API_KEY']==key


@pytest.mark.parametrize('base',['https://example.test/v1/chat/completions','https://key:secret@example.test/v1',
                                 'http://example.test/v1','https://example.test/v1?key=private'])
def test_invalid_model_settings_do_not_overwrite_existing_env(tmp_path,base):
    path=tmp_path/'.env';path.write_text('MODEL_NAME=original\n')
    candidate=values();candidate['MODEL_BASE_URL']=base
    with pytest.raises(ValueError):config.save_config(tmp_path,candidate)
    assert path.read_text()=='MODEL_NAME=original\n'


def test_member_archive_has_latest_source_no_private_state_and_valid_launchers(tmp_path):
    output=package.build(tmp_path/'members.zip')
    with ZipFile(output) as archive:
        names=archive.namelist()
        assert not any('/data/' in n or '/.venv/' in n or '/.git/' in n or n.endswith('/.env') or '.sqlite' in n for n in names)
        manifest=json.loads(archive.read('StudyMate/文件校验.json'))
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('StudyMate/'+name)).hexdigest()==digest
        assert archive.read('StudyMate/frontend/review.js')==(package.ROOT/'frontend/review.js').read_bytes()
        assert archive.read('StudyMate/backend/exports/formatting.py')==(package.ROOT/'backend/exports/formatting.py').read_bytes()
        for name in names:
            if name.endswith('.bat'):
                assert b'\r\n' in archive.read(name)
            if name.endswith('.command'):
                assert (archive.getinfo(name).external_attr>>16)&0o111
        assert 'StudyMate/.gitignore' in names
        assert 'StudyMate/examples/老师要求.txt' in names
    with pytest.raises(FileExistsError):package.build(output)


def test_packager_blocks_credentials_even_if_accidentally_added(monkeypatch,tmp_path):
    (tmp_path/'.env').write_text('MODEL_API_KEY=synthetic-sensitive-key\n')
    monkeypatch.setattr(package,'contents',lambda root:{'frontend/unsafe.js':b'synthetic-sensitive-key'})
    with pytest.raises(RuntimeError):package.build(tmp_path/'unsafe.zip',root=tmp_path)
    assert not (tmp_path/'unsafe.zip').exists()
