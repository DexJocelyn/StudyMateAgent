"""成员视角：会话、指代与草案分开，项目任务共享。"""
import json
import pytest
from uuid import uuid4
from backend.config import PROJECT_ID as P
from backend.services.tools import execute
from backend.storage.db import initialize, connect


def session(client, member=None, project=P):
    response = client.post('/api/sessions', json={'project_id': project, 'member_id': member})
    assert response.status_code == 200, response.text
    return response.json()['session_id']


def send(client, sid, member, text='', action=None):
    return client.post('/api/chat', json={'project_id': P, 'session_id': sid, 'member_id': member,
        'request_id': uuid4().hex, 'message': text, 'action': action})


def history(client, sid, member):
    params = {'project_id': P}
    if member:
        params['member_id'] = member
    return client.get(f'/api/sessions/{sid}', params=params)


def test_member_histories_focus_and_shared_tasks(client):
    a, b = session(client, 'member_a'), session(client, 'member_b')
    created = send(client, a, 'member_a', '创建任务：个人接口，我，30分钟，2030-10-25 22:00截止').json()
    assert created['ok']
    task_id = created['changed_ids']['tasks'][0]
    assert execute('list_tasks', P, {})['data'][0]['assignee_id'] == 'member_a'
    assert '个人接口' not in send(client, b, 'member_b', '查询我的任务').json()['reply']
    assert '个人接口' in send(client, b, 'member_b', '查询任务').json()['reply']
    assert '无法唯一确定' in send(client, b, 'member_b', '把它完成').json()['reply']
    # 显式协作仍可改共享任务，成员视角不是权限系统。
    assert send(client, b, 'member_b', action={'type': 'update_task', 'task_id': task_id, 'patch': {'status': 'doing'}}).json()['ok']
    assert send(client, a, 'member_a', '暂停它').json()['ok']
    assert execute('list_tasks', P, {})['data'][0]['status'] == 'paused'
    initialize()
    ca, cb = history(client, a, 'member_a').json(), history(client, b, 'member_b').json()
    assert ca['member_id'] == 'member_a' and cb['member_id'] == 'member_b'
    assert all(m['member_name'].startswith('成员 A') for m in ca['history'] if m['role'] == 'user')
    assert '创建任务' not in json.dumps(cb['history'], ensure_ascii=False)
    assert '个人接口' in send(client, a, 'member_a', '我的任务').json()['reply']


def test_status_updates_preserve_owner_until_explicit_transfer(client):
    """卡片状态更新只改变状态；会话发言者不能被误用为负责人。"""
    sid = session(client, 'member_a')
    task = execute('create_tasks', P, {'tasks': [{'title': '乙的报告', 'assignee_id': 'member_b',
        'deadline': '2030-10-25T22:00:00+08:00', 'estimated_minutes': 30}]}, uuid4().hex)['data'][0]
    for status in ('doing', 'paused', 'doing', 'done', 'todo'):
        action = {'type': 'update_task', 'task_id': task['id'], 'patch': {'status': status}}
        assert send(client, sid, 'member_a', action=action).json()['ok']
        current = execute('list_tasks', P, {})['data'][0]
        assert current['status'] == status and current['assignee_id'] == 'member_b'
        assert history(client, sid, 'member_a').json()['member_id'] == 'member_a'
    assert send(client, sid, 'member_a', action={'type': 'update_task', 'task_id': task['id'],
        'patch': {'assignee_id': 'member_a'}}).json()['ok']
    assert execute('list_tasks', P, {})['data'][0]['assignee_id'] == 'member_a'


def test_member_scope_rejects_wrong_or_missing_member(client):
    sid = session(client, 'member_a')
    for actor in ('member_b', None):
        assert history(client, sid, actor).status_code == 400
        response = send(client, sid, actor, '查询任务')
        assert response.status_code == 400
        assert response.json()['error']['code'] == 'SESSION_MEMBER_MISMATCH'
    assert history(client, sid, 'member_a').json()['history'] == []
    assert client.post('/api/sessions', json={'project_id': P, 'member_id': 'nonexistent'}).status_code == 400
    project = client.post('/api/projects', json={'name': '另一组', 'members': ['小张'], 'request_id': uuid4().hex}).json()['id']
    assert client.post('/api/sessions', json={'project_id': project, 'member_id': 'member_a'}).status_code == 400


def test_legacy_public_history_is_not_assigned_to_a_member(client):
    sid = session(client)
    assert send(client, sid, None, '查询任务').json()['ok']
    ctx = history(client, sid, None).json()
    assert ctx['member_id'] is None and ctx['history'][0]['member_name'] is None
    assert history(client, sid, 'member_a').status_code == 400
    assert '选择' in send(client, sid, None, '我的任务').json()['reply']
    own = session(client, 'member_a')
    assert history(client, own, 'member_a').json()['history'] == []


def test_draft_cannot_cross_member_sessions(client):
    a, b = session(client, 'member_a'), session(client, 'member_b')
    draft = send(client, a, 'member_a', '拆解演示作业').json()['draft']
    assert draft
    assert history(client, b, 'member_b').json()['draft'] is None
    result = send(client, b, 'member_b', action={'type': 'apply_draft', 'draft_id': draft['id']}).json()
    assert not result['ok'] and execute('list_tasks', P, {})['data'] == []
    assert send(client, a, 'member_a', action={'type': 'apply_draft', 'draft_id': draft['id']}).json()['ok']


def test_model_receives_bound_member_and_no_other_history(client, monkeypatch):
    import httpx
    a, b = session(client, 'member_a'), session(client, 'member_b')
    send(client, a, 'member_a', '查询任务')
    real_client = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        system = body['messages'][0]['content']
        assert '"member_id":"member_b"' in system and '成员 B' in system
        assert 'assignee_id' in system and '我/我的/给我' in system
        assert not any(m.get('content') == '查询任务' for m in body['messages'][1:])
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '你是成员 B。'}}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert send(client, b, 'member_b', '我是谁？').json()['reply'] == '你是成员 B。'


def test_member_rename_preserves_history_and_updates_identity(client):
    sid = session(client, 'member_a')
    send(client, sid, 'member_a', '我是谁')
    assert execute('manage_member', P, {'operation': 'rename', 'member_id': 'member_a', 'name': '小林'}, 'rename-actor')['ok']
    assert '小林' in send(client, sid, 'member_a', '我是谁').json()['reply']
    users = [m for m in history(client, sid, 'member_a').json()['history'] if m['role'] == 'user']
    assert users[0]['member_name'].startswith('成员 A') and users[1]['member_name'] == '小林'


def test_legacy_sessions_upgrade_without_reassigning_history(isolated):
    from backend.memory.store import create_session, display_context
    sid = create_session(P)['session_id']
    old_history = json.dumps([{'messages': [{'role': 'user', 'content': '原来的公共对话'}], 'events': [], 'download': None}])
    with connect() as db:
        db.execute('UPDATE sessions SET history=? WHERE id=?', (old_history, sid))
        db.execute('ALTER TABLE sessions DROP COLUMN member_id')
        db.commit()
    initialize()
    ctx = display_context(P, sid)
    assert ctx['member_id'] is None and ctx['history'][0]['content'] == '原来的公共对话'
    assert ctx['history'][0]['member_name'] is None
    own = create_session(P, 'member_a')['session_id']
    assert display_context(P, own, 'member_a')['history'] == []
    with connect() as db:
        assert db.execute('SELECT history FROM sessions WHERE id=?', (sid,)).fetchone()[0] == old_history


@pytest.mark.parametrize('request_text,expected_owner', [
    ('给我创建NoSQL回归任务，预计6小时，2030-10-25 22:00截止', 'member_a'),
    ('创建NoSQL回归任务，预计6小时，2030-10-25 22:00截止', 'member_a'),
    ('给姓名为“我”的那位成员创建NoSQL回归任务，预计6小时，2030-10-25 22:00截止', 'member_b'),
])
def test_literal_member_me_keeps_speaker_context_across_tools(client, monkeypatch, request_text, expected_owner):
    import httpx
    for mid, name in [('member_a', '甲'), ('member_b', '我')]:
        assert execute('manage_member', P, {'operation': 'rename', 'member_id': mid, 'name': name}, 'name-'+mid)['ok']
    sid = session(client, 'member_a')
    real_client = httpx.AsyncClient
    round_number = 0
    def handler(request):
        nonlocal round_number
        round_number += 1
        body = json.loads(request.content)
        if round_number == 1:
            message = {'role': 'assistant', 'tool_calls': [{'id': 'actual_members', 'type': 'function',
                'function': {'name': 'get_plan', 'arguments': '{}'}}]}
        elif round_number == 2:
            result = json.loads(body['messages'][-1]['content'])
            assert result['ok']
            identity = result['conversation_identity']
            assert identity['speaker_member_id'] == identity['default_task_assignee_id'] == 'member_a'
            assert identity['speaker_name'] == '甲'
            people = {m['stored_name']: m for m in identity['project_members']}
            assert people['甲']['is_current_speaker'] is True
            assert people['我']['is_current_speaker'] is False
            assert people['我']['refer_to_as'] == '成员「我」'
            # 原始工具结果仍保留真实姓名，没有改名或伪造MCP数据。
            assert {m['name'] for m in result['data']['members']} >= {'甲', '我'}
            owner = people['我']['member_id'] if '姓名为' in request_text else identity['default_task_assignee_id']
            args = {'tasks': [{'title': 'NoSQL回归', 'assignee_id': owner, 'deadline': '2030-10-25T22:00:00+08:00', 'estimated_minutes': 360}]}
            message = {'role': 'assistant', 'tool_calls': [{'id': 'actual_create', 'type': 'function',
                'function': {'name': 'create_tasks', 'arguments': json.dumps(args)}}]}
        else:
            result = json.loads(body['messages'][-1]['content'])
            assert result['data'][0]['assignee_id'] == expected_owner
            assert result['conversation_identity']['speaker_name'] == '甲'
            message = {'role': 'assistant', 'content': '任务已保存。'}
        return httpx.Response(200, json={'choices': [{'message': message}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = send(client, sid, 'member_a', request_text).json()
    assert result['ok']
    tasks = execute('list_tasks', P, {})['data']
    assert len(tasks) == 1 and tasks[0]['assignee_id'] == expected_owner
    assert len([e for e in result['tool_events'] if e['name'] == 'create_tasks']) == 1
