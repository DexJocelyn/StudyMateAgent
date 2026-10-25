"""只读联网搜索：固定Tavily基础搜索、应用侧配额、短缓存与有界结果。"""
import asyncio
from datetime import timedelta
import hashlib
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit
import httpx
from backend.config import now
from backend.storage.db import transaction, dumps, uid
from backend.services.tools import project, success, error_result

ENDPOINT = 'https://api.tavily.com/search'
MAX_RESPONSE = 1024 * 1024


def configuration():
    key = os.getenv('TAVILY_API_KEY', '').strip()
    enabled = os.getenv('WEB_SEARCH_ENABLED', '1') == '1'
    try:
        limit = int(os.getenv('WEB_SEARCH_MONTHLY_LIMIT', '100'))
    except ValueError:
        limit = 0
    valid = 1 <= limit <= 1000
    return {'provider': 'tavily', 'configured': bool(key), 'enabled': enabled,
            'available': bool(key) and enabled and valid, 'monthly_limit': limit if valid else None}


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\x00-\x20\x7f\\]', value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if '%' in host: return None
        if parsed.scheme not in ('https', 'http') or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443):
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global: return None
        except ValueError:
            # 浏览器可把127.1等缩写解释为回环IPv4，不能作为公开来源链接。
            if re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*',host):
                return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
    except ValueError:
        return None


def _clean_text(value, maximum):
    if not isinstance(value, str): return ''
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    for secret in (os.getenv('TAVILY_API_KEY'), os.getenv('MODEL_API_KEY')):
        if secret: value = value.replace(secret, '[凭据已隐藏]')
    return value.strip()[:maximum]


async def web_search(project_id, query, time_range=None, max_results=5):
    if (not isinstance(query, str) or not 2 <= len(query.strip()) <= 500
            or re.search(r'[\x00-\x1f\x7f]', query)
            or time_range not in (None, 'day', 'week', 'month', 'year')
            or type(max_results) is not int or not 1 <= max_results <= 5):
        return error_result('SEARCH_INVALID_QUERY', '搜索词需为2～500字，最多5条结果；时间范围为day/week/month/year或留空。')
    query = query.strip()
    if re.search(r'(?:sk-|tvly-)[A-Za-z0-9_-]{16,}', query) or any(
            secret and secret in query for secret in (os.getenv('TAVILY_API_KEY'), os.getenv('MODEL_API_KEY'))):
        return error_result('SEARCH_PRIVATE_QUERY', '搜索词疑似含有API凭据，未发送到外部服务。')
    settings = configuration()
    if not settings['enabled']:
        return error_result('SEARCH_DISABLED', '联网搜索已在主机关闭。')
    if not settings['configured']:
        return error_result('SEARCH_NOT_CONFIGURED', '尚未配置搜索密钥，主机需在.env填写TAVILY_API_KEY并重启。')
    if not settings['monthly_limit']:
        return error_result('SEARCH_INVALID_CONFIG', 'WEB_SEARCH_MONTHLY_LIMIT必须为1～1000。')
    moment = now()
    month = moment.strftime('%Y-%m')
    fingerprint = hashlib.sha256(dumps([query, time_range, max_results]).encode()).hexdigest()
    call_id = uid('search')
    # 只在短事务中预留额度；不在数据库写锁内等待外部网络。
    with transaction() as db:
        project(db, project_id)
        cached = db.execute('''SELECT result FROM web_search_calls WHERE query_hash=? AND result IS NOT NULL
            AND created_at>=? ORDER BY created_at DESC LIMIT 1''',
            (fingerprint, (moment-timedelta(minutes=10)).isoformat())).fetchone()
        used = db.execute('SELECT COUNT(*) FROM web_search_calls WHERE month=?', (month,)).fetchone()[0]
        if cached:
            data = json.loads(cached['result'])
            data.update({'cached': True, 'usage': {'used': used, 'limit': settings['monthly_limit']}})
            return success(data)
        if used >= settings['monthly_limit']:
            return error_result('SEARCH_QUOTA_EXCEEDED', '已达到本应用本月搜索上限；不会自动切换付费服务。')
        db.execute('INSERT INTO web_search_calls(id,month,created_at,query_hash) VALUES(?,?,?,?)',
                   (call_id, month, moment.isoformat(), fingerprint))
    payload = {'query': query, 'search_depth': 'basic', 'auto_parameters': False,
               'max_results': max_results, 'topic': 'general', 'include_answer': False,
               'include_raw_content': False, 'include_images': False}
    if time_range: payload['time_range'] = time_range
    try:
        async with asyncio.timeout(22):
            async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=5), follow_redirects=False) as http:
                async with http.stream('POST', ENDPOINT, headers={
                        'Authorization': 'Bearer '+os.getenv('TAVILY_API_KEY', '').strip()}, json=payload) as response:
                    if response.status_code in (401,403):
                        return error_result('SEARCH_AUTH_FAILED', '搜索服务拒绝认证，请主机检查搜索密钥或账号权限。')
                    if response.status_code in (429,432,433):
                        return error_result('SEARCH_RATE_LIMITED', '搜索服务限流或额度已用完，请稍后再试或等待免费额度重置。')
                    if response.status_code != 200:
                        return error_result('SEARCH_UNAVAILABLE', f'搜索服务暂不可用（HTTP {response.status_code}），未获得可用结果。')
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            return error_result('SEARCH_INVALID_RESPONSE', '搜索服务返回内容过大，未采用。')
        raw = json.loads(content)
        if not isinstance(raw, dict) or not isinstance(raw.get('results'), list):
            raise ValueError('invalid results')
    except (httpx.TimeoutException, TimeoutError):
        return error_result('SEARCH_TIMEOUT', '联网搜索超时；未获取最新信息，不能据此确认实时情况。')
    except httpx.HTTPError:
        return error_result('SEARCH_NETWORK_ERROR', '无法连接搜索服务，请主机检查网络或代理；未获取最新信息。')
    except (ValueError, UnicodeDecodeError):
        return error_result('SEARCH_INVALID_RESPONSE', '搜索服务未返回可用的结构化结果。')
    results, seen = [], set()
    for item in raw['results'][:20]:
        if not isinstance(item, dict): continue
        url = public_url(item.get('url'))
        if not url or url in seen: continue
        # 外部页面不能把凭据伪装成来源URL带回浏览器。
        if any(secret and secret in url for secret in (os.getenv('TAVILY_API_KEY'), os.getenv('MODEL_API_KEY'))): continue
        seen.add(url)
        results.append({'title': _clean_text(item.get('title'), 200) or urlsplit(url).hostname,
                        'url': url, 'snippet': _clean_text(item.get('content'), 1600),
                        'published_at': _clean_text(item.get('published_date'), 100) or None})
        if len(results) >= max_results: break
    data = {'query': query, 'provider': 'tavily', 'retrieved_at': moment.isoformat(),
            'results': results, 'cached': False, 'untrusted_content': True,
            'evidence_scope': '搜索索引摘要，不是本次实时读取原网页；内容可能滞后。检索时间不是页面发布时间。',
            'usage': {'used': used+1, 'limit': settings['monthly_limit']}}
    with transaction() as db:
        db.execute('UPDATE web_search_calls SET result=? WHERE id=?', (dumps(data), call_id))
    return success(data)
