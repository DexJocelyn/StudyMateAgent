"""免费公网分享入口：只代理本机课伴，不读取模型密钥或数据库。"""
import os
import ipaddress
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from backend.access import AccessConfig, load_access_config, unauthorized_response

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}


def clean_headers(headers, omit=()):
    # Connection还可指明额外逐跳字段，不能将它们跨连接转发。
    blocked = HOP_HEADERS | {part.strip().lower() for part in headers.get('connection', '').split(',')} | set(omit)
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def origin_url():
    port = os.getenv('STUDYMATE_ORIGIN_PORT', '8000')
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise RuntimeError('课伴本机端口必须是1～65535。')
    return f'http://127.0.0.1:{int(port)}'


def lan_address(value):
    try:
        address = ipaddress.IPv4Address(value)
        return str(address) if any(address in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')) else None
    except ipaddress.AddressValueError:
        return None


@asynccontextmanager
async def lifespan(app):
    app.state.origin = origin_url()
    configured_lan = os.getenv('STUDYMATE_LAN_HOST', '')
    if configured_lan and not lan_address(configured_lan):
        raise RuntimeError('局域网入口必须绑定具体的内网IPv4地址。')
    no_auth = os.getenv('STUDYMATE_LAN_NO_AUTH','0')
    if no_auth not in ('0','1') or (no_auth=='1' and (not configured_lan or os.getenv('STUDYMATE_PUBLIC','0')=='1')):
        raise RuntimeError('免登录仅适用于绑定具体内网地址的教室入口。')
    app.state.access = AccessConfig(False,'','') if no_auth=='1' else load_access_config()
    if no_auth!='1' and not app.state.access.enabled:
        raise RuntimeError('公网分享入口必须配置小组访问密码。')
    app.state.lan_host = configured_lan
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5), trust_env=False,
                                 follow_redirects=False) as client:
        app.state.client = client
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware('http')
async def require_access(request: Request, call_next):
    lan = request.app.state.lan_host
    if lan:
        # The LAN launcher binds only this private interface, never a public/all-interface address.
        if request.url.hostname != lan:
            return JSONResponse(status_code=400, content={'error': {'message':'请使用主机提供的局域网地址。'}})
    # cloudflared在受信任的本机连接上提供转发协议；公网HTTP先跳HTTPS，不发密码挑战。
    if not lan and request.url.hostname not in ('127.0.0.1', 'localhost') and request.url.scheme != 'https':
        return RedirectResponse(str(request.url.replace(scheme='https')), status_code=307)
    if not request.app.state.access.accepts(request.headers.get('authorization')):
        return unauthorized_response()
    response = await call_next(request)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'})
    return response


@app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
async def forward(request: Request, path: str):
    # 先验密码再读请求体；输入有界，不将网关做成任意URL代理。
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            return JSONResponse(status_code=413, content={'error': {'message': '请求内容过大。'}})
    target = httpx.URL(request.app.state.origin).copy_with(
        path='/' + path, query=request.url.query.encode('ascii'))
    headers = clean_headers(request.headers, {'host', 'authorization', 'cookie', 'content-length'})
    upstream = request.app.state.client.build_request(request.method, target, headers=headers, content=bytes(body))
    try:
        response = await request.app.state.client.send(upstream, stream=True)
    except httpx.HTTPError:
        return JSONResponse(status_code=502, content={'error': {'message': '本机课伴服务暂时不可用，请联系主机同学。'}})

    async def chunks():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(chunks(), status_code=response.status_code,
                             headers=clean_headers(response.headers))
