from uuid import uuid4
import pytest
from backend.storage.db import initialize


def make_project(client, name='数据库课程小组', members=None, request_id=None):
    return client.post('/api/projects', json={'name': name, 'members': members or ['小林', '小陈'],
                                            'request_id': request_id or uuid4().hex})


def make_session(client, pid):
    return client.post('/api/sessions', json={'project_id': pid}).json()['session_id']


def send(client, pid, sid, message='', action=None):
    return client.post('/api/chat', json={'project_id': pid, 'session_id': sid, 'request_id': uuid4().hex,
                                        'message': message, 'action': action})


def test_project_creation_retry_and_restart(client):
    rid = uuid4().hex
    created = make_project(client, request_id=rid)
    assert created.status_code == 200
    assert make_project(client, request_id=rid).json() == created.json()
    assert make_project(client, name='不同内容', request_id=rid).status_code == 409
    initialize()
    projects = client.get('/api/projects').json()
    assert len(projects) == 2
    new = next(p for p in projects if p['id'] == created.json()['id'])
    assert new['member_count'] == 2 and new['task_count'] == 0
    assert len(client.get(f"/api/projects/{new['id']}/members").json()) == 2


@pytest.mark.parametrize('name,members', [('', ['小林']), ('项目', []), ('项目', ['  ']),
                                       ('项目', ['小林', ' 小林 ']), ('项目', ['a' * 61])])
def test_invalid_project_is_atomic(client, name, members):
    response = client.post('/api/projects', json={'name': name, 'members': members, 'request_id': uuid4().hex})
    assert response.status_code in (400, 422)
    assert len(client.get('/api/projects').json()) == 1


def test_projects_isolate_tasks_context_preferences_and_exports(client):
    a = make_project(client, '软件工程小组', ['小林']).json()['id']
    b = make_project(client, '数据库小组', ['小林', '小陈']).json()['id']
    sa, sb = make_session(client, a), make_session(client, b)
    assert send(client, a, sa, '创建任务：软件接口，小林，60分钟，2030-10-25 22:00截止').json()['ok']
    assert send(client, b, sb, '创建任务：数据库查询，小林，30分钟，2030-10-25 22:00截止').json()['ok']
    ta = client.get(f'/api/projects/{a}/tasks').json()[0]
    tb = client.get(f'/api/projects/{b}/tasks').json()[0]
    assert ta['title'] == '软件接口' and tb['title'] == '数据库查询'
    assert ta['assignee_id'] != tb['assignee_id']
    assert send(client, a, sb, '把它完成').status_code == 400
    assert send(client, a, sa, action={'type': 'update_task', 'task_id': tb['id'], 'patch': {'status': 'done'}}).json()['ok'] is False
    assert send(client, a, sa, action={'type': 'set_preferences', 'assignee_id': ta['assignee_id'], 'windows': [
        {'start_at': '2030-10-24T19:00:00+08:00', 'end_at': '2030-10-24T21:00:00+08:00'}]}).json()['ok']
    assert all(not p['windows'] for p in client.get(f'/api/projects/{b}/plan').json()['preferences'])
    assert send(client, b, sb, '把它完成').json()['ok']
    assert client.get(f'/api/projects/{a}/tasks').json()[0]['status'] == 'todo'
    assert client.get(f'/api/projects/{b}/tasks').json()[0]['status'] == 'done'
    exported = send(client, a, sa, '导出计划').json()['download']
    content = client.get(exported['url'], params={'project_id': a})
    assert content.status_code == 200 and '软件工程小组' in content.text and '软件接口' in content.text
    assert '数据库查询' not in content.text
    assert client.get(exported['url'], params={'project_id': b}).status_code == 404
    assert client.get(f'/api/sessions/{sa}', params={'project_id': b}).status_code == 400
    history = client.get(f'/api/sessions/{sa}', params={'project_id': a}).json()['history']
    assert any('软件接口' in m.get('content', '') for m in history)
    assert client.get(f'/api/projects/{a}/tasks').json()[0]['id'] == ta['id']
