"""分享入口访问保护；使用临时库及受控HTTP，不调用模型或公网隧道。"""
import base64
from contextlib import contextmanager
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.access import AccessConfig, load_access_config

PASSWORD = 'only-for-unit-tests-2026'

def basic(user='group', password=PASSWORD):
    return 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()


@pytest.mark.parametrize('password', ['', 'short', 'replace-with-random-password', 'abcdefghijklmnop\n'])
def test_public_configuration_refuses_weak_or_missing_password(monkeypatch, password):
    monkeypatch.setenv('STUDYMATE_PUBLIC', '1')
    monkeypatch.setenv('STUDYMATE_ACCESS_PASSWORD', password)
    with pytest.raises(RuntimeError, match='密码'):
        load_access_config()


def test_credentials_are_hidden_and_utf8_comparison_is_supported():
    access = AccessConfig(True, '组员', '这是测试用的长密码-not-real-2026')
    assert access.password not in repr(access)
    assert access.accepts(basic('组员', access.password))
    assert not access.accepts(basic('组员', 'wrong'))
    for value in (None, '', 'Bearer token', 'Basic ???', 'Basic YQ==', 'Basic /w=='):
        assert not access.accepts(value)


def test_main_public_gate_protects_assets_api_and_downloads(isolated, monkeypatch):
    from backend.main import app
    monkeypatch.setenv('STUDYMATE_PUBLIC', '1'); monkeypatch.setenv('STUDYMATE_ACCESS_PASSWORD', PASSWORD)
    with TestClient(app) as client:
        for path in ('/', '/static/app.js', '/api/health', '/api/projects', '/api/downloads/file_test', '/docs', '/openapi.json'):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic')
            assert PASSWORD not in response.text
        assert client.get('/healthz').json() == {'ok': True}
        assert client.head('/healthz').status_code == 200
        assert client.post('/api/projects', json={}).status_code == 401
        assert client.get('/api/projects', headers={'Authorization':basic(password='wrong')}).status_code == 401
        authorized = client.get('/api/projects', headers={'Authorization':basic()})
        assert authorized.status_code == 200 and len(authorized.json()) == 1
        assert authorized.headers['cache-control'] == 'no-store'


class Chunks(httpx.AsyncByteStream):
    def __init__(self, values): self.values, self.closed = values, False
    async def __aiter__(self):
        for value in self.values: yield value
    async def aclose(self): self.closed = True


@contextmanager
def gateway(monkeypatch, handler):
    import backend.share_gateway as module
    monkeypatch.setenv('STUDYMATE_PUBLIC','1'); monkeypatch.setenv('STUDYMATE_ACCESS_PASSWORD',PASSWORD)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with TestClient(module.app, base_url='https://test.example.com') as client:
        yield client


def test_gateway_requires_password_before_any_proxy_request(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(200, stream=Chunks([b'ok']))
    with gateway(monkeypatch, handler) as client:
        for path in ('/', '/static/app.js', '/api/projects', '/api/chat/stream', '/api/downloads/file_test', '/healthz'):
            assert client.get(path).status_code == 401
        assert client.post('/api/chat/stream', json={'message':'do not run'}).status_code == 401
        assert not calls
        assert client.get('/', headers={'Authorization':basic()}).status_code == 200
        assert len(calls) == 1


def test_gateway_preserves_stream_files_query_and_scope(monkeypatch):
    received, streams = [], []
    def handler(request):
        received.append(request)
        if request.url.path == '/api/chat/stream':
            stream = Chunks([b'{"type":"progress"}\n', b'{"type":"result","data":{"ok":true}}\n'])
            headers = {'content-type':'application/x-ndjson', 'connection':'keep-alive, x-hop', 'x-hop':'drop-me'}
        else:
            stream = Chunks(['# 课伴计划'.encode()]); headers = {'content-disposition':'attachment; filename=plan.md', 'content-type':'text/markdown; charset=utf-8'}
        streams.append(stream)
        return httpx.Response(200, stream=stream, headers=headers)
    headers = {'Authorization':basic(), 'Cookie':'private-cookie=drop', 'Connection':'x-secret', 'x-secret':'drop'}
    with gateway(monkeypatch, handler) as client:
        result = client.post('/api/chat/stream', json={'message':'test'}, headers=headers)
        assert result.headers['content-type'].startswith('application/x-ndjson')
        assert [row['type'] for row in map(__import__('json').loads, result.text.splitlines())] == ['progress','result']
        assert 'x-hop' not in result.headers and result.headers['cache-control'] == 'no-store'
        assert received[0].url.host == '127.0.0.1' and received[0].url.port == 8000
        assert all(name not in received[0].headers for name in ('authorization','cookie','x-secret'))
        download = client.get('/api/downloads/file_test?project_id=project_a', headers=headers)
        assert download.headers['content-disposition'] == 'attachment; filename=plan.md'
        assert download.text == '# 课伴计划'
        assert received[-1].url.params['project_id'] == 'project_a'
    assert all(stream.closed for stream in streams)


def test_gateway_redirects_public_http_before_password_challenge(monkeypatch):
    def handler(request): raise AssertionError('must not proxy')
    with gateway(monkeypatch, handler) as client:
        response = client.get('http://test.example.com/api/projects', follow_redirects=False)
        assert response.status_code == 307
        assert response.headers['location'] == 'https://test.example.com/api/projects'
        assert 'www-authenticate' not in response.headers


def test_gateway_rejects_large_body_and_sanitizes_origin_failure(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request); raise httpx.ConnectError('private-internal-error', request=request)
    with gateway(monkeypatch, handler) as client:
        assert client.post('/api/chat', content=b'x'*(1024*1024+1), headers={'Authorization':basic()}).status_code == 413
        assert not calls
        failure = client.get('/api/projects', headers={'Authorization':basic()})
        assert failure.status_code == 502 and 'private-internal-error' not in failure.text


def test_gateway_cannot_start_without_access_protection(monkeypatch):
    from backend.share_gateway import app
    monkeypatch.setenv('STUDYMATE_PUBLIC', '0'); monkeypatch.delenv('STUDYMATE_ACCESS_PASSWORD', raising=False)
    with pytest.raises(RuntimeError, match='必须配置'):
        with TestClient(app): pass
