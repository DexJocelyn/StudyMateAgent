"""显式 --run 验证“甲”与姓名为“我”的成员消歧；真实模型，临时数据库。
会消耗模型额度，不修改用户项目。保留一段错误历史作为回归输入，不冒充真实模型回复。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from datetime import timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify(output):
    from backend.config import now
    from backend.main import app
    from backend.storage.db import initialize, connect, dumps
    from fastapi.testclient import TestClient
    if os.getenv('AGENT_MODE') != 'llm' or not os.getenv('MODEL_API_KEY', '').strip():
        raise SystemExit('需要真实模型配置。')
    report = {'executed_at': now().isoformat(), 'model': os.getenv('MODEL_NAME', 'qwen-plus'),
              'database': 'temporary', 'real_provider': True, 'passed': False,
              'seeded_history_is_fixture': True, 'steps': [], 'checks': []}
    def check(ok, label):
        if not ok:
            raise RuntimeError(label)
        report['checks'].append(label)
    def clean(value):
        if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
        if isinstance(value, list): return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in (os.getenv('MODEL_API_KEY'),os.getenv('MODEL_BASE_URL')):
                if secret: value=value.replace(secret,'[已隐藏]')
            return re.sub(r'https?://[^\s"<>]+','[接口地址已隐藏]',value)
        return value
    old_data = os.environ.get('STUDYMATE_DATA_DIR')
    try:
        with TemporaryDirectory(prefix='studymate-identity-') as tmp:
            os.environ['STUDYMATE_DATA_DIR'] = tmp
            initialize()
            with TestClient(app) as client:
                project = client.post('/api/projects', json={'name':'同名代词回归项目','members':['甲','我'],'request_id':uuid4().hex}).json()['id']
                def plan(): return client.get(f'/api/projects/{project}/plan').json()
                people = {m['name']:m['id'] for m in plan()['members']}
                actor = people['甲']
                sid = client.post('/api/sessions', json={'project_id':project,'member_id':actor}).json()['session_id']
                # 回归fixture模拟此前错误称呼；报告明确标注，不把它当作本轮供应商输出。
                fixture = [{'messages':[{'role':'user','content':'当前是谁在聊天？'},
                    {'role':'assistant','content':'当前项目有甲和您两位成员。任务是给您自己，还是给甲同学？'}],
                    'events':[],'download':None,'member_id':actor,'member_name':'甲'}]
                with connect() as db:
                    db.execute('UPDATE sessions SET history=? WHERE id=?',(dumps(fixture),sid));db.commit()
                def send(label,text):
                    response = client.post('/api/chat',json={'project_id':project,'member_id':actor,'session_id':sid,
                        'request_id':uuid4().hex,'message':text})
                    check(response.status_code==200,label+' HTTP成功')
                    result=response.json()
                    report['steps'].append({'label':label,'input':text,'reply':result['reply'],'ok':result['ok'],
                        'events':result['tool_events'],'changed_ids':result['changed_ids'],'error':result['error']})
                    print(json.dumps({'step':len(report['steps']),'label':label,'ok':result['ok'],
                        'tools':[e['name'] for e in result['tool_events']]},ensure_ascii=False),flush=True)
                    check(result['ok'],label+' 接口业务成功')
                    return result
                first=send('缺截止时间时只澄清时间','帮我创建一个nosql学习的任务，预计6小时。')
                check(not plan()['tasks'],'缺截止时间时未擅自创建任务')
                check('截止' in first['reply'],'只缺日期时确实询问截止信息')
                check(not re.search(r'(?:您|你|自己|我)[^。\n]{0,25}还是[^。\n]{0,12}甲|甲[^。\n]{0,12}还是[^。\n]{0,12}(?:您|你|自己|我)|分配给谁|哪位成员|负责人是谁',first['reply']),
                      '没有重复询问给自己还是给甲')
                check(not re.search(r'确认[^。\n]{0,15}身份|(?:默认|分配|负责人)[^。\n]{0,30}是否(?:正确|确认)|甲[^。\n]{0,10}是否正确', first['reply']),
                      '不再次要求确认已绑定的身份或默认负责人')
                deadline=(now()+timedelta(days=3)).replace(hour=22,minute=0,second=0,microsecond=0)
                send('沿用当前身份完成创建',f'截止北京时间{deadline:%Y-%m-%d %H:%M}，其他按刚才的要求，请直接保存。')
                tasks=plan()['tasks']
                check(len(tasks)==1 and tasks[0]['assignee_id']==actor and tasks[0]['estimated_minutes']==360,
                      '补齐时间后NoSQL六小时任务归甲')
                send('代词给我归甲',f'再给我直接创建任务「接口联调」，预计30分钟，截止北京时间{deadline:%Y-%m-%d %H:%M}。')
                send('省略负责人默认当前甲',f'直接创建任务「撰写复盘」，预计30分钟，截止北京时间{deadline:%Y-%m-%d %H:%M}。')
                send('明确姓名我时归另一位成员',f'给姓名为“我”的那位成员直接创建任务「字面姓名任务」，预计30分钟，截止北京时间{deadline:%Y-%m-%d %H:%M}。我当前聊天身份仍是甲，不是切换身份。')
                tasks=plan()['tasks']
                for title,owner in [('接口联调',actor),('撰写复盘',actor),('字面姓名任务',people['我'])]:
                    check(any(t['title']==title and t['assignee_id']==owner for t in tasks),title+' 真实负责人正确')
                check(len(tasks)==4,'共四项任务无重复或多余创建')
                send('我的任务只查当前甲','查看我的任务，只列出属于当前聊天成员的任务。')
                with connect() as db:
                    history=json.loads(db.execute('SELECT history FROM sessions WHERE id=?',(sid,)).fetchone()[0])
                queries=[json.loads(c['function']['arguments']) for m in history[-1]['messages'] for c in m.get('tool_calls',[]) if c['function']['name']=='list_tasks']
                check(any(q.get('assignee_id')==actor for q in queries),'我的任务使用甲的ID过滤真实查询')
                report['final_tasks']=tasks
                report['members']=plan()['members']
                report['passed']=True
    except Exception as exc:
        report['failure']=clean(str(exc))
        raise
    finally:
        if old_data is None: os.environ.pop('STUDYMATE_DATA_DIR',None)
        else: os.environ['STUDYMATE_DATA_DIR']=old_data
        Path(output).write_text(json.dumps(clean(report),ensure_ascii=False,indent=2)+'\n')
        print(f'身份回归记录已保存，passed={report["passed"]}；未更改用户项目。',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--report',default=str(ROOT/'docs/IDENTITY_PRONOUN_VERIFICATION.json'))
    args=parser.parse_args()
    if not args.run: raise SystemExit('未调用模型；显式传入 --run 才执行真实模型测试。')
    verify(args.report)
