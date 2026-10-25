"""实时执行状态来自真实处理阶段，断线与重试不重复写操作。"""
import asyncio
import json
from uuid import uuid4
from backend.config import PROJECT_ID as P
from backend.storage.db import connect
from backend.services.tools import execute


def session(client, member=None):
    return client.post('/api/sessions', json={'project_id': P, 'member_id': member}).json()['session_id']


def body(sid, message='', **extra):
    return {'project_id': P, 'session_id': sid, 'request_id': uuid4().hex, 'message': message, **extra}


def frames(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/x-ndjson')
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_stream_real_mcp_create_cache_and_export(client):
    sid = session(client)
    request = body(sid, '创建任务：实时进度任务，成员 A，30分钟，2030-10-25 22:00截止')
    events = frames(client.post('/api/chat/stream', json=request))
    assert events[0]['type'] == 'progress' and events[-1]['type'] == 'result'
    tools = [e for e in events if e.get('kind') == 'tool']
    for step_id in {e['step_id'] for e in tools}:
        states = [e['status'] for e in tools if e['step_id'] == step_id]
        assert states == ['running', 'success']
    assert any(e['label'] == '保存任务' and e['status'] == 'success' for e in tools)
    result = events[-1]['data']
    assert result['ok'] and len(result['changed_ids']['tasks']) == 1
    restored = frames(client.post('/api/chat/stream', json=request))
    assert any(e.get('kind') == 'cached' for e in restored)
    assert not any(e.get('kind') == 'tool' for e in restored)
    assert restored[-1]['data'] == result
    assert len(execute('list_tasks', P, {})['data']) == 1
    assert client.post('/api/chat', json=request).json() == result
    exported = frames(client.post('/api/chat/stream', json=body(sid, '导出计划')))
    download = exported[-1]['data']['download']
    assert '实时进度任务' in client.get(download['url']).text
    assert any(e.get('label') == '生成下载文件' and e.get('status') == 'success' for e in exported)


def test_stream_model_failure_and_no_private_reasoning(client, monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    counter = 0
    def handler(request):
        nonlocal counter
        counter += 1
        if counter == 1:
            message = {'role': 'assistant', 'reasoning_content': 'PRIVATE_REASONING_SENTINEL',
                'tool_calls': [{'id': 'real_invalid_create', 'type': 'function', 'function': {'name': 'create_tasks',
                'arguments': json.dumps({'tasks': [{'title': '应拒绝任务', 'assignee_id': 'nonexistent',
                    'estimated_minutes': 30, 'deadline': '2030-10-25T22:00:00+08:00'}]})}}]}
        else:
            message = {'role': 'assistant', 'content': '成员不存在，任务未保存。', 'reasoning_content': 'PRIVATE_REASONING_SENTINEL'}
        return httpx.Response(200, json={'choices': [{'message': message}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    response = client.post('/api/chat/stream', json=body(session(client), '创建明确任务'))
    events = frames(response)
    assert 'PRIVATE_REASONING_SENTINEL' not in response.text
    assert any(e.get('label') == '正在分析你的需求' for e in events)
    states = [e['status'] for e in events if e.get('kind') == 'tool']
    assert states == ['running', 'error']
    assert not events[-1]['data']['ok'] and events[-1]['data']['error']['code'] == 'INVALID_MEMBER'
    assert execute('list_tasks', P, {})['data'] == []


def test_infeasible_schedule_is_warning_not_saved(client):
    sid = session(client)
    create = body(sid, '创建任务：需安排任务，成员 A，30分钟，2030-10-25 22:00截止')
    assert client.post('/api/chat', json=create).json()['ok']
    events = frames(client.post('/api/chat/stream', json=body(sid, '安排日程')))
    assert any(e.get('label') == '计算候选日程' and e.get('status') == 'warning' for e in events)
    result = events[-1]['data']
    assert result['draft'] is None and execute('get_plan', P, {})['data']['schedule'] == []


def test_stream_rejects_other_member_and_busy_session(client):
    from backend.main import app
    sid = session(client, 'member_a')
    assert client.post('/api/chat/stream', json=body(sid, '查询任务', member_id='member_b')).status_code == 400
    lock = asyncio.Lock()
    asyncio.run(lock.acquire())
    app.state.locks[sid] = lock
    try:
        response = client.post('/api/chat/stream', json=body(sid, '查询任务', member_id='member_a'))
        assert response.status_code == 409 and response.json()['error']['code'] == 'REQUEST_PENDING'
        assert not app.state.chat_jobs
    finally:
        lock.release()


def test_closed_stream_does_not_cancel_authorized_write(isolated, monkeypatch):
    import backend.main as main
    from backend.memory.store import create_session
    from backend.services.models import ChatRequest
    sid = create_session(P)['session_id']
    request = ChatRequest(**body(sid, '创建任务：断线验证，成员 A，30分钟，2030-10-25 22:00截止'))
    real_chat = main.chat
    async def run():
        main.app.state.locks = {}; main.app.state.chat_jobs = set()
        gate = asyncio.Event()
        async def gated_chat(*args, **kwargs):
            await gate.wait()
            return await real_chat(*args, **kwargs)
        monkeypatch.setattr(main, 'chat', gated_chat)
        response = await main.chat_stream_route(request)
        iterator = response.body_iterator
        first = json.loads(await anext(iterator))
        assert first['type'] == 'progress'
        assert main.app.state.locks[sid].locked()
        await iterator.aclose()
        jobs = list(main.app.state.chat_jobs)
        assert jobs and not jobs[0].done()
        gate.set()
        await asyncio.gather(*jobs)
        assert not main.app.state.locks[sid].locked()
        with connect() as db:
            row = db.execute('SELECT state,response FROM requests WHERE session_id=? AND request_id=?',
                (sid, request.request_id)).fetchone()
            assert row['state'] == 'finished' and json.loads(row['response'])['ok']
        assert len(execute('list_tasks', P, {})['data']) == 1
    asyncio.run(run())
