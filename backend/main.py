import asyncio
import os
from contextlib import asynccontextmanager
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from backend.config import ROOT, PROJECT_ID, now
from backend.storage.db import initialize, connect
from backend.memory import store
from backend.services.tools import execute, BusinessError
from backend.services.models import ChatRequest
from backend.agent.runner import chat
from backend.mcp_client import open_tools
from backend.services.projects import list_projects, create_project
from backend.storage.db import dumps
from backend.access import load_access_config, unauthorized_response
from backend.services.search import configuration as search_configuration
from backend.services.weather import configuration as weather_configuration


@asynccontextmanager
async def lifespan(app):
    app.state.access = load_access_config()
    initialize()
    app.state.locks = {}
    app.state.chat_jobs = set()
    async with open_tools() as client:
        app.state.tool_count = len(client.definitions)
    try:
        yield
    finally:
        jobs = list(app.state.chat_jobs)
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


app = FastAPI(title='课伴 StudyMate', version='0.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')


@app.middleware('http')
async def revalidate_interface(request: Request, call_next):
    access = request.app.state.access
    health_probe = request.url.path == '/healthz' and request.method in ('GET', 'HEAD')
    if not health_probe and not access.accepts(request.headers.get('authorization')):
        return unauthorized_response()
    response = await call_next(request)
    if access.enabled:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
    if not access.enabled and (request.url.path == '/' or request.url.path.startswith('/static/')):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.exception_handler(BusinessError)
async def business_error(request: Request, exc: BusinessError):
    return JSONResponse(status_code=409 if exc.code in ('REQUEST_CONFLICT', 'REQUEST_PENDING') else 400,
        content={'ok': False, 'error': {'code': exc.code, 'message': str(exc), 'retryable': False}})


@app.get('/')
def index():
    return FileResponse(ROOT / 'frontend' / 'index.html')


@app.api_route('/healthz', methods=['GET', 'HEAD'])
def health_probe():
    # 供云平台健康检查；不泄露项目、成员、模型或密钥配置。
    return {'ok': True}


@app.get('/api/health')
def health():
    mode = os.getenv('AGENT_MODE', 'demo')
    return {'ok': True, 'project_id': PROJECT_ID, 'mode': mode,
            'model_configured': bool(os.getenv('MODEL_API_KEY', '').strip()),
            'model_name': os.getenv('MODEL_NAME', 'qwen-plus') if mode == 'llm' else None,
            'mcp_tools': app.state.tool_count, 'server_time': now().isoformat(),
            'web_search': search_configuration(), 'weather': weather_configuration()}


class SessionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str
    member_id: str | None = None


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    members: list[str] = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=100)


@app.get('/api/projects')
def projects_get():
    return list_projects()


@app.post('/api/projects')
def project_create(body: ProjectInput):
    return create_project(body.name, body.members, body.request_id)


@app.post('/api/sessions')
def session_create(body: SessionInput):
    return store.create_session(body.project_id, body.member_id)


@app.get('/api/sessions/latest')
def session_latest(project_id: str, member_id: str | None = None):
    return store.latest_session(project_id, member_id)


@app.get('/api/sessions/{session_id}')
def session_get(session_id: str, project_id: str, member_id: str | None = None):
    return store.display_context(project_id, session_id, member_id)


@app.post('/api/chat')
async def chat_route(body: ChatRequest):
    # 同会话串行，以免两个回合覆盖历史；数据库仍是最终防重复边界。
    store.load_context(body.project_id, body.session_id, body.member_id)
    lock = app.state.locks.setdefault(body.session_id, asyncio.Lock())
    if lock.locked():
        raise BusinessError('REQUEST_PENDING', '本会话正在处理上一条消息，请稍后重试同一请求。')
    async with lock:
        return await chat(body)


@app.post('/api/chat/stream')
async def chat_stream_route(body: ChatRequest):
    store.load_context(body.project_id, body.session_id, body.member_id)
    lock = app.state.locks.setdefault(body.session_id, asyncio.Lock())
    if lock.locked():
        raise BusinessError('REQUEST_PENDING', '本会话正在处理上一条消息，请稍后重试同一请求。')
    await lock.acquire()
    queue = asyncio.Queue()

    async def run():
        try:
            queue.put_nowait({'type': 'progress', 'kind': 'phase', 'label': '已收到，正在开始处理'})
            result = await chat(body, on_progress=queue.put_nowait)
            queue.put_nowait({'type': 'result', 'data': result})
        except BusinessError as exc:
            queue.put_nowait({'type': 'error', 'error': {'code': exc.code, 'message': str(exc)}})
        except Exception:
            queue.put_nowait({'type': 'error', 'error': {'code': 'STREAM_FAILED',
                'message': '未取得最终结果，请核对实际任务后重试同一请求。'}})
        finally:
            lock.release()
            queue.put_nowait(None)

    # 浏览器断线仅停止接收；已授权的操作继续完成并缓存结果，避免半途取消后重复写入。
    task = asyncio.create_task(run())
    app.state.chat_jobs.add(task)
    task.add_done_callback(app.state.chat_jobs.discard)

    async def events():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield dumps({'type': 'heartbeat'}) + '\n'
                continue
            if event is None:
                break
            yield dumps(event) + '\n'

    return StreamingResponse(events(), media_type='application/x-ndjson',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


def read_tool(name, project_id, args=None):
    result = execute(name, project_id, args or {})
    if not result['ok']:
        raise BusinessError(result['error']['code'], result['error']['message'])
    return result['data']


@app.get('/api/projects/{project_id}/plan')
def plan(project_id: str):
    return read_tool('get_plan', project_id)


@app.get('/api/projects/{project_id}/members')
def members(project_id: str):
    return read_tool('get_plan', project_id)['members']


@app.get('/api/projects/{project_id}/tasks')
def tasks(project_id: str, status: str | None = None, assignee_id: str | None = None):
    return read_tool('list_tasks', project_id, {'status': status, 'assignee_id': assignee_id})


@app.get('/api/projects/{project_id}/schedule')
def schedule(project_id: str):
    data = read_tool('get_plan', project_id)
    return {'blocks': data['schedule'], 'version': data['version']}


@app.get('/api/projects/{project_id}/preferences/{assignee_id}')
def preferences(project_id: str, assignee_id: str):
    return read_tool('get_preferences', project_id, {'assignee_id': assignee_id})[0]


@app.get('/api/downloads/{file_id}')
def download(file_id: str, project_id: str = PROJECT_ID):
    with connect() as db:
        row = db.execute('SELECT * FROM downloads WHERE id=? AND project_id=?', (file_id, project_id)).fetchone()
    if row is None:
        return JSONResponse(status_code=404, content={'ok': False, 'error': {'message': '文件不存在'}})
    return Response(row['content'], media_type='text/markdown; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(row['filename'])}", 'X-Content-Type-Options': 'nosniff'})
