"""暂停/继续经真实 MCP 保存，并兼容已有任务和日程。"""
import sqlite3
from uuid import uuid4
from backend.config import PROJECT_ID as P
from backend.storage.db import initialize, connect
from backend.services.tools import execute


def test_pause_resume_through_mcp_and_export(client, task_data):
    session = client.post('/api/sessions', json={'project_id': P}).json()['session_id']
    task = execute('create_tasks', P, {'tasks': [task_data]}, 'create-pause')['data'][0]
    def send(message='', action=None, request_id=None):
        return client.post('/api/chat', json={'project_id': P, 'session_id': session,
            'request_id': request_id or uuid4().hex, 'message': message, 'action': action}).json()
    for status in ('doing', 'paused', 'doing', 'paused', 'done'):
        rid = uuid4().hex
        action = {'type': 'update_task', 'task_id': task['id'], 'patch': {'status': status}}
        result = send(action=action, request_id=rid)
        assert result['ok'] and result['changed_ids']['tasks'] == [task['id']]
        assert result == send(action=action, request_id=rid)
        current = client.get(f'/api/projects/{P}/tasks', params={'status': status}).json()
        assert current == [{**task, 'status': status}]
        initialize()
        assert execute('list_tasks', P, {})['data'][0]['status'] == status
        if status == 'paused':
            exported = send('导出计划')['download']
            assert '已暂停' in client.get(exported['url']).text
    assert len(client.get(f'/api/projects/{P}/tasks').json()) == 1


def test_demo_pause_continue_reference(client, task_data):
    sid = client.post('/api/sessions', json={'project_id': P}).json()['session_id']
    task = execute('create_tasks', P, {'tasks': [task_data]}, 'create-pause')['data'][0]
    for text, status in (('暂停接口联调', 'paused'), ('继续它', 'doing')):
        result = client.post('/api/chat', json={'project_id': P, 'session_id': sid,
            'request_id': uuid4().hex, 'message': text}).json()
        assert result['ok']
        assert execute('list_tasks', P, {})['data'] == [{**task, 'status': status}]


def test_old_database_migrates_preserving_schedule(tmp_path, monkeypatch, task_data):
    monkeypatch.setenv('STUDYMATE_DATA_DIR', str(tmp_path))
    with sqlite3.connect(tmp_path / 'studymate.sqlite3') as db:
        db.executescript('''
            CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE members(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL);
            CREATE TABLE tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', assignee_id TEXT NOT NULL REFERENCES members(id),
                deadline TEXT NOT NULL, estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes>0),
                status TEXT NOT NULL CHECK(status IN ('todo','doing','done')));
            CREATE TABLE schedule_blocks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                task_id TEXT NOT NULL REFERENCES tasks(id), assignee_id TEXT NOT NULL REFERENCES members(id),
                start_at TEXT NOT NULL, end_at TEXT NOT NULL);
        ''')
        db.execute('INSERT INTO projects VALUES(?,?,?)', (P, '原项目', 3))
        db.execute('INSERT INTO members VALUES(?,?,?)', ('member_a', P, '原成员'))
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)', ('old_task', P, task_data['title'], '原说明',
            'member_a', task_data['deadline'], task_data['estimated_minutes'], 'doing'))
        db.execute('INSERT INTO schedule_blocks VALUES(?,?,?,?,?,?)', ('old_block', P, 'old_task', 'member_a',
            '2030-10-24T19:00:00+08:00', '2030-10-24T20:00:00+08:00'))
    initialize()
    original = execute('get_plan', P, {})['data']
    assert original['project']['name'] == '原项目' and original['version'] == 3
    assert original['schedule'][0]['task_id'] == 'old_task'
    assert len(original['members']) == 1
    for status in ('paused', 'doing'):
        result = execute('update_task', P, {'task_id': 'old_task', 'patch': {'status': status}}, status)
        assert result['ok'], result
        initialize()
        plan = execute('get_plan', P, {})['data']
        assert plan['schedule'] == original['schedule'] and plan['version'] == original['version']
        assert plan['tasks'][0] == {**original['tasks'][0], 'status': status}
    with connect() as db:
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='tasks_by_project'").fetchone()
