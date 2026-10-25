"""普通对话的真实HTTP/MCP/持久化链路；供应商受控，不作为模型语义验收。"""
import json
from uuid import uuid4
import httpx
import pytest
from backend.config import PROJECT_ID as P
from backend.services.tools import execute
from backend.storage.db import connect


def session(client):
    return client.post('/api/sessions', json={'project_id': P, 'member_id': 'member_a'}).json()['session_id']


def send(client, sid, text, stream=False):
    body = {'project_id': P, 'session_id': sid, 'member_id': 'member_a',
            'request_id': uuid4().hex, 'message': text}
    response = client.post('/api/chat/stream' if stream else '/api/chat', json=body)
    assert response.status_code == 200
    return response


def provider(monkeypatch, reply, inspect=lambda body: None):
    real_client = httpx.AsyncClient
    def handle(request):
        body = json.loads(request.content)
        inspect(body)
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': reply}}]})
    monkeypatch.setenv('AGENT_MODE', 'llm')
    monkeypatch.setenv('MODEL_API_KEY', 'PRIVATE-TEST-KEY')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://private-test.invalid/compatible-mode/v1')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient',
                        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))


@pytest.mark.parametrize('model_name', ['qwen-plus', 'another-compatible-model'])
def test_runtime_facts_override_old_self_description_without_sending_credentials(client, monkeypatch, model_name):
    sid = session(client)
    wrong = 'fixture旧错误回复：没有接入千问或其他外部模型API，只能处理课业。'
    old = [{'messages': [{'role': 'user', 'content': '你接了模型吗'},
                          {'role': 'assistant', 'content': wrong}], 'events': []}]
    with connect() as db:
        db.execute('UPDATE sessions SET history=? WHERE id=?', (json.dumps(old), sid)); db.commit()
    monkeypatch.setenv('MODEL_NAME', model_name)
    def inspect(body):
        assert body['model'] == model_name
        messages = body['messages']
        old_index = next(i for i, m in enumerate(messages) if m.get('content') == wrong)
        trusted = [(i, m['content']) for i, m in enumerate(messages)
                   if m['role'] == 'system' and '"model_connection"' in m['content']]
        assert len(trusted) == 1 and trusted[0][0] > old_index
        assert model_name in trusted[0][1] and '"weather_query_available":true' in trusted[0][1]
        # 私有地址和密钥只用于HTTP请求，不能作为自我介绍上下文发送给模型。
        prompt = json.dumps(messages)
        assert 'PRIVATE-TEST-KEY' not in prompt and 'private-test.invalid' not in prompt
    provider(monkeypatch, '服务端配置了模型API；实时天气查询尚未接入。', inspect)
    result = send(client, sid, '你不是接了千问的API吗').json()
    assert result['ok'] and '模型API' in result['reply'] and result['tool_events'] == []
    assert all(not ids for ids in result['changed_ids'].values())
    restored = client.get(f'/api/sessions/{sid}', params={'project_id':P, 'member_id':'member_a'}).json()
    assert any(m.get('content') == wrong for m in restored['history'])  # 不改写用户旧记录


@pytest.mark.parametrize('question,reply', [
    ('今天有点累，想聊聊天', '可以，今天什么事情最让你累？'),
    ('今天北京会下雨吗', '我现在查不到实时天气，无法确认北京今天是否下雨。出门前可以看一下天气应用。'),
])
def test_plain_chat_and_weather_preserve_pending_draft_focus_and_business_data(client, monkeypatch, question, reply):
    sid = session(client)
    assert send(client, sid, '创建任务：整理资料，我，30分钟，2030-10-25 22:00截止').json()['ok']
    assert send(client, sid, '拆解演示作业').json()['draft']
    params = {'project_id':P, 'member_id':'member_a'}
    before = client.get(f'/api/sessions/{sid}', params=params).json()
    plan = execute('get_plan', P, {})
    provider(monkeypatch, reply)
    frames = [json.loads(line) for line in send(client, sid, question, stream=True).text.splitlines()]
    result = frames[-1]['data']
    assert result['ok'] and result['reply'] == reply and result['tool_events'] == []
    assert not any(frame.get('kind') == 'tool' for frame in frames)
    assert all(not ids for ids in result['changed_ids'].values())
    after = client.get(f'/api/sessions/{sid}', params=params).json()
    assert after['draft'] == before['draft'] and after['focus_id'] == before['focus_id']
    assert any(m.get('content') == reply for m in after['history'])
    current = execute('get_plan', P, {})
    plan['data'].pop('exported_at')
    current['data'].pop('exported_at')  # 查询生成时间不是业务数据变化
    assert current == plan
