"""新设备恢复上下文与SQLite迁移快照，不操作真实数据或密钥。"""
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from zipfile import ZipFile
import pytest
from backend.config import PROJECT_ID as P
from backend.storage.db import connect
from scripts.package_migration import build


def session(client, member):
    return client.post('/api/sessions', json={'project_id':P, 'member_id':member}).json()['session_id']


def say(client, sid, member, message):
    return client.post('/api/chat', json={'project_id':P, 'session_id':sid, 'member_id':member,
        'request_id':uuid4().hex, 'message':message}).json()


def latest(client, member):
    return client.get('/api/sessions/latest', params={'project_id':P, 'member_id':member})


def test_new_device_recovers_member_history_and_latest_activity(client):
    a1, b = session(client,'member_a'), session(client,'member_b')
    assert say(client,a1,'member_a','创建任务：甲的资料，我，30分钟，2030-10-25 22:00截止')['ok']
    assert say(client,b,'member_b','创建任务：乙的报告，我，30分钟，2030-10-25 22:00截止')['ok']
    empty = session(client,'member_a')
    restored = latest(client,'member_a').json()
    assert restored['session_id'] == a1 and restored['member_id'] == 'member_a'
    assert '甲的资料' in json.dumps(restored,ensure_ascii=False)
    assert '乙的报告' not in json.dumps(restored,ensure_ascii=False)
    assert latest(client,'member_b').json()['session_id'] == b
    assert say(client,empty,'member_a','查询我的任务')['ok']
    assert latest(client,'member_a').json()['session_id'] == empty
    assert say(client,a1,'member_a','查询我的任务')['ok']
    assert latest(client,'member_a').json()['session_id'] == a1
    assert latest(client,'member_c').json() == {'session_id':None}
    assert latest(client,'not-a-member').status_code == 400
    assert client.get('/api/sessions/latest',params={'project_id':'not-a-project','member_id':'member_a'}).status_code == 400
    assert client.get('/api/sessions/latest',params={'project_id':P}).json() == {'session_id':None}


def test_private_migration_preserves_committed_wal_and_config(client, isolated, tmp_path):
    sid=session(client,'member_a')
    assert say(client,sid,'member_a','创建任务：要迁移的任务，我，20分钟，2030-10-25 22:00截止')['ok']
    source = tmp_path/'source.zip'
    with ZipFile(source,'w') as z: z.writestr('StudyMate/README.md','source-code')
    root = tmp_path/'config'; root.mkdir()
    (root/'.env').write_text('MODEL_API_KEY=unit-test-private-config\n',encoding='utf-8')
    destination=tmp_path/'private-migration.zip'
    # 保持WAL连接存在，验证backup而不是只复制主.sqlite文件。
    with connect() as live:
        result=build(source, isolated/'studymate.sqlite3', destination, root, include_model_config=True)
        assert result['counts']['tasks'] == 1 and result['model_config_included']
        assert len(live.execute('SELECT * FROM tasks').fetchall()) == 1
    with ZipFile(destination) as z:
        assert z.read('StudyMate/.env') == (root/'.env').read_bytes()
        assert all('/.venv/' not in n and '/.tools/' not in n for n in z.namelist())
        restored=tmp_path/'restored.sqlite3'; restored.write_bytes(z.read('StudyMate/data/studymate.sqlite3'))
    with sqlite3.connect(restored) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT title FROM tasks').fetchone()[0] == '要迁移的任务'
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
    assert result['path'] == str(destination)


def test_migration_default_excludes_real_config_and_refuses_missing_database(isolated,tmp_path):
    source=tmp_path/'source.zip'
    with ZipFile(source,'w') as z: z.writestr('StudyMate/README.md','source')
    (tmp_path/'.env').write_text('never-copy-by-default')
    dest=tmp_path/'without-config.zip'
    result=build(source, isolated/'studymate.sqlite3', dest,tmp_path)
    assert not result['model_config_included']
    with ZipFile(dest) as z: assert 'StudyMate/.env' not in z.namelist()
    with pytest.raises(FileExistsError): build(source, isolated/'studymate.sqlite3',dest,tmp_path)
    missing=tmp_path/'missing.sqlite3'
    with pytest.raises(FileNotFoundError): build(source, missing,tmp_path/'invalid.zip')
    assert not missing.exists()
