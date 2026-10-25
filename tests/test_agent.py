from uuid import uuid4
from backend.config import PROJECT_ID as P


def session(client):
    response = client.post('/api/sessions', json={'project_id': P})
    assert response.status_code == 200
    return response.json()['session_id']


def send(client, sid, text='', action=None, request_id=None):
    response = client.post('/api/chat', json={'project_id': P, 'session_id': sid, 'request_id': request_id or uuid4().hex,
                                             'message': text, 'action': action})
    assert response.status_code == 200, response.text
    return response.json()


def test_mcp_draft_apply_retry_export_restore(client):
    sid = session(client)
    draft = send(client, sid, '拆解演示作业')['draft']
    assert draft and len(draft['data']) == 5
    assert client.get(f'/api/projects/{P}/tasks').json() == []
    request_id = uuid4().hex
    result = send(client, sid, '采用', request_id=request_id)
    assert result['ok'] and len(result['changed_ids']['tasks']) == 5
    assert any(e['name'] == 'create_tasks' and e['transport'] == 'MCP stdio' for e in result['tool_events'])
    assert result == send(client, sid, '采用', request_id=request_id)
    assert len(client.get(f'/api/projects/{P}/tasks').json()) == 5
    assert not send(client, sid, action={'type': 'apply_draft', 'draft_id': draft['id']})['ok']
    restored = client.get(f'/api/sessions/{sid}', params={'project_id': P}).json()
    assert restored['history'] and restored['draft'] is None
    exported = send(client, sid, '导出计划')
    file = client.get(exported['download']['url'])
    assert file.status_code == 200 and '梳理需求并接入模型' in file.text
    assert 'attachment' in file.headers['content-disposition']
    assert client.get('/api/downloads/file_nonexistent').status_code == 404


def test_reference_and_session_isolation(client):
    sid = session(client)
    result = send(client, sid, '创建任务：接口联调，成员 A，60分钟，2030-10-25 22:00截止')
    assert result['ok'] and len(result['changed_ids']['tasks']) == 1
    task_id = result['changed_ids']['tasks'][0]
    assert send(client, sid, '把它延后一天')['ok']
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['deadline'].startswith('2030-10-26')
    other = session(client)
    assert '无法唯一确定' in send(client, other, '把它完成')['reply']
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['status'] == 'todo'
    assert send(client, sid, '把它完成')['ok']
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['id'] == task_id
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['status'] == 'done'


def test_request_content_conflict(client):
    sid = session(client); rid = uuid4().hex
    send(client, sid, '查询任务', request_id=rid)
    conflict = client.post('/api/chat', json={'project_id': P, 'session_id': sid, 'request_id': rid, 'message': '导出计划'})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'REQUEST_CONFLICT'


def test_no_key_fails_honestly(client, monkeypatch):
    monkeypatch.setenv('AGENT_MODE', 'llm')
    result = send(client, session(client), '帮我创建一个任务')
    assert not result['ok'] and result['error']['code'] == 'MODEL_NOT_CONFIGURED'
    assert client.get(f'/api/projects/{P}/tasks').json() == []


def test_ambiguous_title_not_modified(client):
    sid = session(client)
    for _ in range(2):
        assert send(client, sid, '创建任务：接口联调，成员 A，60分钟，2030-10-25 22:00截止')['ok']
    result = send(client, sid, '完成接口联调')
    assert '无法唯一确定' in result['reply']
    assert all(t['status'] == 'todo' for t in client.get(f'/api/projects/{P}/tasks').json())


def test_actual_mcp_schedule_chain(client):
    sid = session(client)
    send(client, sid, '创建任务：接口联调，成员 A，60分钟，2030-10-25 22:00截止')
    insufficient = send(client, sid, '安排日程')
    assert '缺 60 分钟' in insufficient['reply'] and insufficient['draft'] is None
    assert send(client, sid, action={'type': 'set_preferences', 'assignee_id': 'member_a',
      'windows': [{'start_at': '2030-10-24T19:00:00+08:00', 'end_at': '2030-10-24T21:00:00+08:00'}]})['ok']
    draft = send(client, sid, '安排日程')['draft']
    assert draft['type'] == 'schedule'
    assert client.get(f'/api/projects/{P}/schedule').json()['blocks'] == []
    result = send(client, sid, '采用')
    assert result['ok']
    assert len(client.get(f'/api/projects/{P}/schedule').json()['blocks']) == 1


def test_model_loop_with_scripted_provider_real_mcp(client, monkeypatch):
    """仅模拟供应商；MCP 和 SQLite 真实。此测试不算真实模型验收。"""
    import json
    import httpx
    real_client = httpx.AsyncClient
    seen = []
    def handler(request):
        body = json.loads(request.content); seen.append(body)
        schemas = {t['function']['name']: t['function']['parameters'] for t in body['tools']}
        assert 'project_id' not in schemas['create_tasks']['properties']
        assert 'mutation_id' not in schemas['create_tasks']['properties']
        if len(seen) == 1:
            msg = {'role': 'assistant', 'tool_calls': [{'id': 'controlled_1', 'type': 'function', 'function': {
                'name': 'create_tasks', 'arguments': json.dumps({'tasks': [{'title': '受控模型工具测试', 'assignee_id': 'member_b',
                'deadline': '2030-10-25T22:00:00+08:00', 'estimated_minutes': 30}]})}}]}
        else:
            assert body['messages'][-1]['role'] == 'tool' and json.loads(body['messages'][-1]['content'])['ok']
            msg = {'role': 'assistant', 'content': '已通过工具创建。'}
        return httpx.Response(200, json={'choices': [{'message': msg}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = send(client, session(client), '创建一个明确任务')
    assert result['ok'] and len(seen) == 2
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['title'] == '受控模型工具测试'


def test_pending_request_is_not_replayed(client):
    from backend.memory.store import register_request
    sid = session(client); rid = uuid4().hex
    assert register_request(P, sid, rid, '查询任务', None) is None
    response = client.post('/api/chat', json={'project_id': P, 'session_id': sid, 'request_id': rid, 'message': '查询任务'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'REQUEST_PENDING'


def test_other_session_cannot_adopt_draft(client):
    a, b = session(client), session(client)
    draft = send(client, a, '拆解演示作业')['draft']
    result = send(client, b, action={'type': 'apply_draft', 'draft_id': draft['id']})
    assert not result['ok'] and result['error']['code'] == 'DRAFT_EXPIRED'
    assert client.get(f'/api/projects/{P}/tasks').json() == []


def test_model_partial_success_and_history_pairing(client, monkeypatch):
    import json
    import httpx
    from backend.memory.store import load_context
    real_client = httpx.AsyncClient
    counter = 0
    def handler(request):
        nonlocal counter
        counter += 1
        if counter == 1:
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'tool_calls': [
                {'id': 'first', 'type': 'function', 'function': {'name': 'create_tasks', 'arguments': json.dumps({'tasks': [
                    {'title': '部分完成任务', 'assignee_id': 'member_a', 'deadline': '2030-10-25T22:00:00+08:00', 'estimated_minutes': 30}]})}}
            ]}}]})
        return httpx.Response(503, json={'error': 'controlled unavailable'})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    sid = session(client)
    result = send(client, sid, '明确创建任务')
    assert not result['ok'] and result['error']['code'] == 'MODEL_HTTP_ERROR'
    assert '部分操作已经执行' in result['reply'] and len(result['changed_ids']['tasks']) == 1
    assert client.get(f'/api/projects/{P}/tasks').json()[0]['title'] == '部分完成任务'
    messages = load_context(P, sid)['history'][-1]['messages']
    assert any(m['role'] == 'tool' and m['tool_call_id'] == 'first' for m in messages)
    assert messages[-1]['role'] == 'assistant'


def test_model_loop_is_bounded(client, monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    counter = 0
    def handler(request):
        nonlocal counter
        counter += 1
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'tool_calls': [
            {'id': f'query_{counter}', 'type': 'function', 'function': {'name': 'list_tasks', 'arguments': '{}'}}]}}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only'); monkeypatch.setenv('AGENT_MAX_ROUNDS', '2')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = send(client, session(client), '查询任务')
    assert counter == 2 and result['error']['code'] == 'ROUND_LIMIT'
    assert len(result['tool_events']) == 2


def test_flexible_team_through_real_mcp(client):
    sid = session(client)
    added = send(client, sid, action={'type': 'manage_member', 'operation': 'add', 'name': '小林'})
    assert added['ok']
    mid = added['changed_ids']['members'][0]
    for letter in 'abcde':
        assert send(client, sid, action={'type': 'manage_member', 'operation': 'remove', 'member_id': f'member_{letter}'})['ok']
    # 每次请求启动新的 MCP 子进程，也不能重新插入已移除的示例成员。
    assert len(client.get(f'/api/projects/{P}/members').json()) == 1
    draft = send(client, sid, '拆解演示作业')['draft']
    assert {t['assignee_id'] for t in draft['data']} == {mid}
    assert '1 位成员' in draft['assumptions'][1]
    assert send(client, sid, '创建任务：联调，小林，30分钟，2030-10-25 22:00截止')['ok']
    task = client.get(f'/api/projects/{P}/tasks').json()[0]
    assert task['assignee_id'] == mid
    assert send(client, sid, action={'type': 'manage_member', 'operation': 'rename', 'member_id': mid, 'name': '小陈'})['ok']
    result = send(client, sid, '导出计划')
    assert '小陈' in client.get(result['download']['url']).text


def test_export_button_and_typed_command_always_produce_files(client, monkeypatch):
    """导出是明确的用户操作，即使模型未配置或漏调工具，也须真实执行并返回附件。"""
    monkeypatch.setenv('AGENT_MODE', 'llm')
    sid = session(client)
    for text, action in [('导出计划', None), ('请导出当前项目计划', None), ('导出计划', {'type': 'export_plan'})]:
        result = send(client, sid, text, action)
        assert result['ok'] and result['download']
        assert any(e['name'] == 'export_plan' and e['status'] == 'success' for e in result['tool_events'])
        assert client.get(result['download']['url']).status_code == 200
    restored = client.get(f'/api/sessions/{sid}', params={'project_id': P}).json()
    downloads = [m for m in restored['history'] if m['role'] == 'download']
    assert len(downloads) == 3


def test_model_cannot_claim_a_download_without_export(client, monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant',
            'content': '已从数据库生成 Markdown，点击下方文件下载。'}}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = send(client, session(client), '先检查任务，再帮我导出全部计划')
    assert not result['ok'] and result['error']['code'] == 'EXPORT_NOT_EXECUTED'
    assert result['download'] is None and result['tool_events'] == []
    assert '未实际生成' in result['reply']


def test_export_help_question_does_not_write_a_file(client, monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant',
            'content': '点击顶部的“导出计划”，再点击返回的文件链接即可下载。'}}]})
    monkeypatch.setenv('AGENT_MODE', 'llm'); monkeypatch.setenv('MODEL_API_KEY', 'controlled-test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = send(client, session(client), '导出功能怎么使用？')
    assert result['ok'] and result['download'] is None and not result['tool_events']


def test_task_plan_wording_does_not_claim_a_file():
    from backend.agent.runner import claims_exported_file
    assert not claims_exported_file('已生成任务计划草案，请检查分工。')
    assert claims_exported_file('已导出当前项目计划。')
