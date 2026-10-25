"""Open-Meteo只读天气工具；无密钥，仅发送城市或经纬度，不发送聊天。"""
import asyncio
from datetime import datetime, timezone
import math
import os
import re
from urllib.parse import urlencode
import httpx
from backend.config import now
from backend.services.tools import success, error_result, project
from backend.storage.db import connect

GEO = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST = 'https://api.open-meteo.com/v1/forecast'
MAX_BYTES = 256 * 1024
CODES = {0:'晴', 1:'大部晴朗', 2:'局部多云', 3:'阴', 45:'雾', 48:'雾凇',
         51:'小毛毛雨',53:'中等毛毛雨',55:'强毛毛雨',56:'冻毛毛雨',57:'强冻毛毛雨',
         61:'小雨',63:'中雨',65:'大雨',66:'小冻雨',67:'强冻雨',71:'小雪',73:'中雪',75:'大雪',
         77:'米雪',80:'小阵雨',81:'中等阵雨',82:'强阵雨',85:'小阵雪',86:'强阵雪',
         95:'雷暴',96:'雷暴伴小冰雹',99:'雷暴伴强冰雹'}


def configuration():
    return {'provider':'open-meteo', 'available':os.getenv('WEATHER_ENABLED','1') == '1',
            'requires_api_key':False, 'data_kind':'天气模型估计，非现场传感器实测'}


async def _json(http, url, params):
    async with http.stream('GET', url, params=params) as response:
        if response.status_code == 429:
            raise RuntimeError('WEATHER_RATE_LIMIT')
        if response.status_code != 200:
            raise RuntimeError('WEATHER_PROVIDER_ERROR')
        data = bytearray()
        async for block in response.aiter_bytes():
            data.extend(block)
            if len(data) > MAX_BYTES:
                raise ValueError('response too large')
    import json
    value = json.loads(data)
    if not isinstance(value,dict) or value.get('error'):
        raise ValueError('invalid provider response')
    return value


def _number(value, lower, upper):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('invalid number')
    return value


def _location(item):
    identifier = item['id']
    if type(identifier) is not int or identifier <= 0:
        raise ValueError('invalid location')
    def label(key):
        value=item.get(key,'')
        if not isinstance(value,str) or re.search(r'[\x00-\x1f\x7f]',value):
            raise ValueError('invalid location label')
        return value[:100]
    return {'location_id':identifier,'name':label('name'),'region':label('admin1'),
            'country':label('country'),'country_code':label('country_code'),
            'latitude':_number(item['latitude'],-90,90),'longitude':_number(item['longitude'],-180,180)}


async def get_weather(project_id, city, country_code=None, location_id=None):
    if not configuration()['available']:
        return error_result('WEATHER_DISABLED','天气查询已在主机关闭。')
    if (not isinstance(city,str) or not 2 <= len(city.strip()) <= 80
            or not re.fullmatch(r"[\w\s\u00b7.,，'’()（）-]+",city.strip())
            or any(c in city for c in '\n\r\t')
            or (country_code is not None and (not isinstance(country_code,str) or not re.fullmatch('[A-Za-z]{2}',country_code)))
            or (location_id is not None and (type(location_id) is not int or location_id <= 0))):
        return error_result('WEATHER_INVALID_CITY','请提供城市名；同名城市可补充国家或省份，不要提供网址、密钥或聊天全文。')
    city=city.strip()
    if any(secret and secret in city for secret in (os.getenv('MODEL_API_KEY'),os.getenv('TAVILY_API_KEY'))) or re.search(r'(?:sk-|tvly-)[A-Za-z0-9_-]{12,}',city):
        return error_result('WEATHER_INVALID_CITY','城市名中不能包含凭据。')
    with connect() as db:
        project(db,project_id)
    params={'name':city,'language':'zh','count':5,'format':'json'}
    if country_code: params['countryCode']=country_code.upper()
    try:
        async with asyncio.timeout(24):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10,connect=5),follow_redirects=False) as http:
                geo=await _json(http,GEO,params)
                results=geo.get('results',[])
                if not isinstance(results,list): raise ValueError('invalid locations')
                # 不按人口猜测同名城市；明确选择的ID也必须来自本次城市查询。
                locations=list({loc['location_id']:loc for loc in [_location(i) for i in results[:5]]}.values())
                if not locations:
                    return error_result('WEATHER_CITY_NOT_FOUND','没有找到这个城市，请补充省份/国家，或尝试城市英文名。')
                if location_id is not None:
                    locations=[loc for loc in locations if loc['location_id']==location_id]
                    if not locations:
                        return error_result('WEATHER_LOCATION_CHANGED','所选地点不在本次城市结果中，请重新确认城市。')
                if len(locations)>1:
                    return success({'needs_selection':True,'candidates':locations,
                                    'message':'找到多个地点，请用户确认省份/国家；下一次保持city查询不变并提供对应location_id。'})
                loc=locations[0]
                query={'latitude':loc['latitude'],'longitude':loc['longitude'],
                       'current':'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
                       'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
                       'forecast_days':2,'timezone':'auto','temperature_unit':'celsius','wind_speed_unit':'kmh'}
                raw=await _json(http,FORECAST,query)
                current=raw['current']; units=raw['current_units']; daily=raw['daily']; daily_units=raw['daily_units']
                if (units.get('temperature_2m')!='°C' or units.get('apparent_temperature')!='°C'
                    or units.get('relative_humidity_2m')!='%' or units.get('wind_speed_10m')!='km/h'
                    or daily_units.get('temperature_2m_max')!='°C' or daily_units.get('temperature_2m_min')!='°C'
                    or daily_units.get('precipitation_probability_max')!='%'):
                    raise ValueError('unexpected units')
                offset=_number(raw['utc_offset_seconds'],-50400,50400)
                stamp=datetime.fromisoformat(current['time'])
                # API本地时间 + 显式UTC偏移；不依赖Windows系统时区。
                from datetime import timedelta
                if stamp.tzinfo is not None: raise ValueError('unexpected time format')
                stamp=stamp.replace(tzinfo=timezone(timedelta(seconds=offset)))
                retrieved=now(); age=(retrieved-stamp).total_seconds()
                code=current['weather_code']
                if type(code) is not int: raise ValueError('invalid code')
                forecast=[]
                for i,date in enumerate(daily['time'][:2]):
                    datetime.strptime(date,'%Y-%m-%d')
                    forecast.append({'date':date,'temperature_min_c':_number(daily['temperature_2m_min'][i],-100,70),
                                     'temperature_max_c':_number(daily['temperature_2m_max'][i],-100,70),
                                     'precipitation_probability_max_percent':_number(daily['precipitation_probability_max'][i],0,100)})
                if not forecast: raise ValueError('missing forecast')
                return success({'needs_selection':False,'location':loc,'provider':'Open-Meteo',
                    'data_kind':'数值天气模型估计，不是现场传感器实测；日最高降水概率不等于整天下雨。',
                    'data_time':stamp.isoformat(),'timezone':str(raw['timezone'])[:80],
                    'retrieved_at':retrieved.isoformat(),'fresh':-1800 <= age <= 7200,
                    'freshness_note':'时间匹配' if -1800 <= age <= 7200 else '数据时间与当前系统时间不匹配，不能宣称为当前天气，请展示数据时间。',
                    'temperature_c':_number(current['temperature_2m'],-100,70),
                    'apparent_temperature_c':_number(current['apparent_temperature'],-120,90),
                    'humidity_percent':_number(current['relative_humidity_2m'],0,100),
                    'wind_speed_kmh':_number(current['wind_speed_10m'],0,500),
                    'weather_code':code,'condition':CODES.get(code,'未知天气类型'), 'forecast':forecast,
                    'source':{'title':'Open-Meteo 天气数据（CC BY 4.0）','url':FORECAST+'?'+urlencode(query)}})
    except (httpx.TimeoutException,TimeoutError):
        return error_result('WEATHER_TIMEOUT','天气接口暂时超时，请稍后重试；未取得有效天气。')
    except httpx.HTTPError:
        return error_result('WEATHER_NETWORK_ERROR','暂时无法连接天气服务，请检查主机网络；未取得有效天气。')
    except RuntimeError as exc:
        code=str(exc)
        return error_result(code,'天气服务请求受限，请稍后再试。' if code=='WEATHER_RATE_LIMIT' else '天气服务暂时不可用，未取得有效天气。')
    except (ValueError,KeyError,TypeError,IndexError,OverflowError):
        return error_result('WEATHER_INVALID_RESPONSE','天气接口返回的数据不完整，暂时无法可靠回答。')
