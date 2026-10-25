"""显式--run验证真实千问→MCP→免费天气API；一轮对话，使用少量现有模型额度。"""
import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def verify(output,keep_data=False,question=None,legacy_history=False,with_search=False):
    from backend.config import now
    from backend.storage.db import initialize,connect
    from backend.services.tools import execute
    from backend.main import app
    from fastapi.testclient import TestClient
    if os.getenv('AGENT_MODE')!='llm' or not os.getenv('MODEL_API_KEY'):
        raise SystemExit('模型配置不齐，未进行调用。')
    report={'executed_at':now().isoformat(),'real_model':True,'provider':'Open-Meteo',
            'database':'independent temporary database','structural_passed':False,'semantic_review':'pending'}
    old={k:os.environ.get(k) for k in ('STUDYMATE_DATA_DIR','STUDYMATE_PUBLIC','STUDYMATE_ACCESS_PASSWORD','WEATHER_ENABLED','WEB_SEARCH_ENABLED')}
    logger=logging.getLogger('backend.agent.runner');disabled=logger.disabled
    try:
        with TemporaryDirectory(prefix='studymate-weather-') as folder:
            os.environ.update({'STUDYMATE_DATA_DIR':folder,'STUDYMATE_PUBLIC':'0','STUDYMATE_ACCESS_PASSWORD':'','WEATHER_ENABLED':'1','WEB_SEARCH_ENABLED':'1' if with_search else '0'})
            logger.disabled=True
            initialize()
            with TestClient(app) as client:
                project=client.post('/api/projects',json={'name':'天气接口真实验收','members':['天气测试同学'],'request_id':uuid4().hex}).json()['id']
                actor=client.get(f'/api/projects/{project}/plan').json()['members'][0]['id']
                sid=client.post('/api/sessions',json={'project_id':project,'member_id':actor}).json()['session_id']
                before=execute('get_plan',project,{})['data'];before.pop('exported_at')
                if legacy_history:
                    fixture=[{'messages':[{'role':'user','content':'广州现在天气如何'},{'role':'assistant','content':'我刚尝试通过联网搜索获取广州今天天气，但未找到权威、明确、带时间戳的实时气温。当前系统未接入天气API，无法确认。所有结果中均未出现广州+2026-10-24+℃的可靠匹配数据。请打开手机天气App，或告诉我需要安排的课业。'}], 'events':[], 'sources':[]}]
                    with connect() as db:
                        db.execute('UPDATE sessions SET history=? WHERE id=?',(json.dumps(fixture,ensure_ascii=False),sid));db.commit()
                    report['seeded_history_is_fixture']=True
                question=question or '广州现在天气如何？顺便说说明天的最高最低温和降雨概率。请用天气接口查询，简短回答，标明数据时间和来源，不要创建任务。'
                result=client.post('/api/chat',json={'project_id':project,'member_id':actor,'session_id':sid,'request_id':uuid4().hex,'message':question}).json()
                report.update({'input':question,'result':result})
                after=execute('get_plan',project,{})['data'];after.pop('exported_at')
                restored=client.get(f'/api/sessions/{sid}',params={'project_id':project,'member_id':actor}).json()
                report['business_data_unchanged']=before==after
                report['sources_restored']=any(m.get('sources')==result.get('sources') and result.get('sources') for m in restored['history'])
                report['structural_passed']=bool(result['ok'] and report['business_data_unchanged'] and report['sources_restored']
                    and any(e['name']=='get_weather' and e['status']=='success' for e in result['tool_events']) and not any(result['changed_ids'].values()))
                if keep_data:
                    dest=ROOT/'data/verification'/('weather-'+now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]);dest.mkdir(parents=True)
                    with connect() as source,sqlite3.connect(dest/'studymate.sqlite3') as target: source.backup(target)
                    report['review_database']=str(dest/'studymate.sqlite3');report['project_id']=project;report['member_id']=actor;report['session_id']=sid
    except Exception as exc:
        report['failure_type']=type(exc).__name__
    finally:
        logger.disabled=disabled
        for key,value in old.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
    text=json.dumps(report,ensure_ascii=False,indent=2)
    for key in ('MODEL_API_KEY','MODEL_BASE_URL','TAVILY_API_KEY','STUDYMATE_ACCESS_PASSWORD'):
        secret=os.getenv(key)
        if secret:text=text.replace(secret,'[已隐藏]')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(text+'\n')
    print(text)
    return report['structural_passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true');parser.add_argument('--keep-data',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/WEATHER_VERIFICATION.json')
    parser.add_argument('--question',default=None)
    parser.add_argument('--legacy-history',action='store_true')
    parser.add_argument('--with-search',action='store_true')
    args=parser.parse_args()
    if not args.run: parser.exit(message='未调用接口；使用--run进行真实验收，会消耗少量现有模型额度。\n')
    raise SystemExit(0 if verify(args.output,args.keep_data,args.question,args.legacy_history,args.with_search) else 1)
