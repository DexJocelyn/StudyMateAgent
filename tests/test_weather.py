"""天气接口边界及模型工具循环；供应商受控，不消耗模型额度。"""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4
import httpx
import pytest
from backend.config import PROJECT_ID as P, now
from backend.services import weather
from backend.services.tools import execute

LOC={'id':1809858,'name':'广州','admin1':'广东','country':'中国','country_code':'CN','latitude':23.13,'longitude':113.26}


def forecast():
    time=now().replace(second=0,microsecond=0,tzinfo=None).isoformat(timespec='minutes')
    date=time[:10]
    return {'utc_offset_seconds':28800,'timezone':'Asia/Shanghai',
            'current':{'time':time,'temperature_2m':26.5,'apparent_temperature':28,'relative_humidity_2m':70,'wind_speed_10m':8,'weather_code':2},
            'current_units':{'temperature_2m':'°C','apparent_temperature':'°C','relative_humidity_2m':'%','wind_speed_10m':'km/h'},
            'daily_units':{'temperature_2m_max':'°C','temperature_2m_min':'°C','precipitation_probability_max':'%'},
            'daily':{'time':[date],'temperature_2m_min':[22],'temperature_2m_max':[29],'precipitation_probability_max':[20]}}


def setup(monkeypatch,handler):
    real=httpx.AsyncClient
    monkeypatch.setenv('WEATHER_ENABLED','1')
    monkeypatch.setattr(weather.httpx,'AsyncClient',lambda **kw: real(transport=httpx.MockTransport(handler),**kw))


def run(**args):
    return asyncio.run(weather.get_weather(P,**{'city':'广州',**args}))


def test_weather_structured_data_fixed_endpoints_units_and_no_business_changes(isolated,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        if request.url.host=='geocoding-api.open-meteo.com':
            assert request.url.params['name']=='广州' and request.url.params['countryCode']=='CN'
            return httpx.Response(200,json={'results':[LOC]})
        assert request.url.host=='api.open-meteo.com' and request.url.params['timezone']=='auto'
        return httpx.Response(200,json=forecast())
    setup(monkeypatch,handler)
    result=run(country_code='CN')
    assert result['ok'] and result['data']['temperature_c']==26.5 and result['data']['fresh']
    assert result['data']['data_time'].endswith('+08:00') and result['data']['condition']=='局部多云'
    assert result['data']['forecast'][0]['precipitation_probability_max_percent']==20
    assert len(calls)==2 and not any(result['changed_ids'].values())
    assert execute('list_tasks',P,{})['data']==[]


@pytest.mark.parametrize('city',['','广','https://127.0.0.1','广州\n密码','sk-abcdefghijklmnopqrstuvw'])
def test_invalid_city_never_sends_http(isolated,monkeypatch,city):
    setup(monkeypatch,lambda r: pytest.fail('must not call network'))
    assert run(city=city)['error']['code']=='WEATHER_INVALID_CITY'


def test_ambiguous_city_requires_explicit_matching_location(isolated,monkeypatch):
    cities=[LOC,{**LOC,'id':123,'admin1':'另一省'}]
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'results':cities} if 'geocoding-' in request.url.host else forecast())
    setup(monkeypatch,handler)
    result=run()
    assert result['data']['needs_selection'] and count==1
    assert run(location_id=999)['error']['code']=='WEATHER_LOCATION_CHANGED'
    assert run(location_id=123)['data']['location']['location_id']==123


@pytest.mark.parametrize('case,expected',[
    ('empty','WEATHER_CITY_NOT_FOUND'),('rate','WEATHER_RATE_LIMIT'),('redirect','WEATHER_PROVIDER_ERROR'),
    ('timeout','WEATHER_TIMEOUT'),('network','WEATHER_NETWORK_ERROR'),('bad','WEATHER_INVALID_RESPONSE'),
    ('units','WEATHER_INVALID_RESPONSE'),('null','WEATHER_INVALID_RESPONSE'),('oversize','WEATHER_INVALID_RESPONSE')])
def test_provider_failures_do_not_invent_weather(isolated,monkeypatch,case,expected):
    def handler(request):
        if case=='empty': return httpx.Response(200,json={'results':[]})
        if case=='rate': return httpx.Response(429)
        if case=='redirect': return httpx.Response(302,headers={'location':'http://127.0.0.1/'})
        if case=='timeout': raise httpx.ReadTimeout('private-error')
        if case=='network': raise httpx.ConnectError('private-error')
        if case=='oversize': return httpx.Response(200,content=b'x'*(weather.MAX_BYTES+1))
        if 'geocoding-' in request.url.host: return httpx.Response(200,json={'results':[LOC]})
        data=forecast()
        if case=='units': data['current_units']['temperature_2m']='°F'
        if case=='null': data['current']['temperature_2m']=None
        if case=='bad': return httpx.Response(200,json={'current':{}})
        return httpx.Response(200,json=data)
    setup(monkeypatch,handler)
    result=run()
    assert not result['ok'] and result['error']['code']==expected
    assert 'private-error' not in json.dumps(result)


def test_stale_weather_retains_actual_time_without_claiming_current(isolated,monkeypatch):
    data=forecast(); data['current']['time']='2000-01-01T12:00'
    setup(monkeypatch,lambda r:httpx.Response(200,json={'results':[LOC]} if 'geocoding-' in r.url.host else data))
    result=run()['data']
    assert not result['fresh'] and result['data_time'].startswith('2000-01-01')


def test_disabled_weather_not_offered_to_model(client,monkeypatch):
    from backend.mcp_client import open_tools
    monkeypatch.setenv('WEATHER_ENABLED','0')
    async def check():
        async with open_tools() as tools:
            assert 'get_weather' in tools.definitions
            assert 'get_weather' not in [t['function']['name'] for t in tools.model_tools()]
    asyncio.run(check())
    assert run()['error']['code']=='WEATHER_DISABLED'


@pytest.mark.parametrize('write_after_weather',[False,True])
def test_model_weather_progress_sources_restore_and_readonly(client,monkeypatch,write_after_weather):
    from backend.mcp_client import ToolClient
    original=ToolClient.call
    async def adapter(self,name,args,project_id,mutation_id):
        if name=='get_weather': return await weather.get_weather(project_id,**args)
        return await original(self,name,args,project_id,mutation_id)
    monkeypatch.setattr(ToolClient,'call',adapter)
    calls=0
    def handler(request):
        nonlocal calls
        if request.url.host=='geocoding-api.open-meteo.com': return httpx.Response(200,json={'results':[LOC]})
        if request.url.host=='api.open-meteo.com': return httpx.Response(200,json=forecast())
        calls+=1
        body=json.loads(request.content)
        if calls==1:
            assert any(t['function']['name']=='get_weather' for t in body['tools'])
            msg={'tool_calls':[{'id':'weather','type':'function','function':{'name':'get_weather','arguments':'{"city":"广州"}'}}]}
        elif calls==2 and write_after_weather:
            msg={'tool_calls':[{'id':'write','type':'function','function':{'name':'create_tasks','arguments':'{"tasks":[]}'}}]}
        else:
            if write_after_weather: assert json.loads(body['messages'][-1]['content'])['error']['code']=='SEARCH_READ_ONLY_TURN'
            msg={'content':'广州局部多云，26.5°C。来源：Open-Meteo 天气模型估计。'}
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant',**msg}}]})
    setup(monkeypatch,handler)
    monkeypatch.setenv('AGENT_MODE','llm'); monkeypatch.setenv('MODEL_API_KEY','unit-test-only')
    sid=client.post('/api/sessions',json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    body={'project_id':P,'session_id':sid,'member_id':'member_a','request_id':uuid4().hex,'message':'广州现在天气如何'}
    response=client.post('/api/chat/stream',json=body)
    frames=[json.loads(line) for line in response.text.splitlines()]
    result=frames[-1]['data']
    assert result['ok'] == (not write_after_weather)
    assert result['tool_events'][0]['name']=='get_weather' and len(result['sources'])==1
    assert any(f.get('label')=='查询城市天气' and f.get('status')=='success' for f in frames)
    assert execute('list_tasks',P,{})['data']==[]
    assert client.post('/api/chat',json=body).json()==result
    history=client.get(f'/api/sessions/{sid}',params={'project_id':P,'member_id':'member_a'}).json()['history']
    assert any(m.get('sources')==result['sources'] for m in history)
