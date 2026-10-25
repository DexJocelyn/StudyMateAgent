"""显式--run才验收真实模型→MCP→Tavily搜索，会使用模型和搜索额度。"""
import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify(report_path, keep_data=False):
    from backend.config import now
    from backend.services.search import configuration
    from backend.services.tools import execute
    from backend.storage.db import initialize, connect
    from backend.main import app
    from fastapi.testclient import TestClient
    if os.getenv('AGENT_MODE') != 'llm' or not os.getenv('MODEL_API_KEY', '').strip() or not configuration()['available']:
        raise SystemExit('需要llm、模型密钥及TAVILY_API_KEY；配置未齐，未调用供应商。')
    report = {'executed_at':now().isoformat(), 'model':os.getenv('MODEL_NAME','qwen-plus'),
        'real_model':True, 'real_search_provider':'tavily', 'database':'temporary',
        'passed':False, 'semantic_review':'pending', 'steps':[]}
    secrets = [os.getenv(k) for k in ('MODEL_API_KEY','TAVILY_API_KEY','MODEL_BASE_URL','STUDYMATE_ACCESS_PASSWORD')]
    def clean(value):
        if isinstance(value,dict): return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list): return [clean(v) for v in value]
        if isinstance(value,str):
            for secret in secrets:
                if secret: value=value.replace(secret,'[已隐藏]')
        return value
    old = {k:os.environ.get(k) for k in ('STUDYMATE_DATA_DIR','STUDYMATE_PUBLIC','STUDYMATE_ACCESS_PASSWORD')}
    logger=logging.getLogger('backend.agent.runner'); disabled=logger.disabled
    started=monotonic()
    try:
        with TemporaryDirectory(prefix='studymate-web-search-') as folder:
            os.environ.update({'STUDYMATE_DATA_DIR':folder,'STUDYMATE_PUBLIC':'0','STUDYMATE_ACCESS_PASSWORD':''})
            logger.disabled=True
            initialize()
            with TestClient(app) as client:
                pid=client.post('/api/projects',json={'name':'联网搜索真实验收','members':['测试同学'],'request_id':uuid4().hex}).json()['id']
                plan=client.get(f'/api/projects/{pid}/plan').json(); actor=plan['members'][0]['id']
                sid=client.post('/api/sessions',json={'project_id':pid,'member_id':actor}).json()['session_id']
                baseline=execute('get_plan',pid,{})['data']; baseline.pop('exported_at')
                scenarios=[
                    ('一般资料检索','请联网搜索Python官方关于venv虚拟环境的说明，用中文给出两点解释并引用实际来源。不要创建任务。',True),
                    ('当前信息检索','请联网查询Python官网当前下载页列出的最新稳定Python版本，给出来源。如果结果不能确认最新版本，请如实说明。不要创建任务。',True),
                    ('普通聊天不搜索','谢谢，今天有点累，我们就随便聊聊，不要搜索或安排任务。',False),
                ]
                for label,text,needs_search in scenarios:
                    begin=monotonic()
                    response=client.post('/api/chat',json={'project_id':pid,'member_id':actor,'session_id':sid,
                        'request_id':uuid4().hex,'message':text})
                    if response.status_code!=200: raise RuntimeError(label+f' HTTP {response.status_code}')
                    result=response.json()
                    item={'label':label,'input':text,'ok':result['ok'],'reply':result['reply'],
                          'events':result['tool_events'],'sources':result.get('sources',[]),
                          'changed_ids':result['changed_ids'],'error':result['error'],'seconds':round(monotonic()-begin,2)}
                    report['steps'].append(item)
                    print(json.dumps(clean(item),ensure_ascii=False),flush=True)
                    if not result['ok']: raise RuntimeError(label+'接口业务失败')
                    searches=[e for e in result['tool_events'] if e['name']=='web_search' and e['status']=='success']
                    if needs_search and (not searches or not item['sources']): raise RuntimeError(label+'缺少实际搜索事件或来源')
                    if not needs_search and searches: raise RuntimeError(label+'不应额外搜索')
                    if any(result['changed_ids'].values()): raise RuntimeError(label+'不应修改业务数据')
                    current=execute('get_plan',pid,{})['data']; current.pop('exported_at')
                    if current!=baseline: raise RuntimeError(label+'任务/成员/日程发生变化')
                restored=client.get(f'/api/sessions/{sid}',params={'project_id':pid,'member_id':actor}).json()
                if len([m for m in restored['history'] if m['role']=='sources'])!=2:
                    raise RuntimeError('刷新历史未恢复两轮来源')
                with connect() as db:
                    report['search_attempts']=db.execute('SELECT COUNT(*) FROM web_search_calls').fetchone()[0]
                report['structural_passed']=True
                if keep_data:
                    relative=Path('data/verification')/('search-'+now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6])
                    dest=ROOT/relative; dest.mkdir(parents=True,exist_ok=False,mode=0o700)
                    with sqlite3.connect(Path(folder)/'studymate.sqlite3') as source, sqlite3.connect(dest/'studymate.sqlite3') as target:
                        source.backup(target)
                    os.chmod(dest/'studymate.sqlite3',0o600)
                    report['browser_demo']={'data_path':str(relative),'project_id':pid,'member_id':actor,'session_id':sid}
    except Exception as exc:
        report['failure']=clean(str(exc))
        raise RuntimeError(report['failure']) from None
    finally:
        for k,v in old.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v
        logger.disabled=disabled
        report['seconds']=round(monotonic()-started,2)
        Path(report_path).write_text(json.dumps(clean(report),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f'记录已保存：{report_path}；请人工审阅真实回复后再标记语义通过。',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--keep-data',action='store_true',help='保存独立验收库供浏览器检查，不修改原库')
    parser.add_argument('--report',default=str(ROOT/'docs/WEB_SEARCH_VERIFICATION.json'))
    args=parser.parse_args()
    if not args.run: raise SystemExit('未调用供应商。显式--run才执行真实搜索验收。')
    verify(args.report,args.keep_data)
