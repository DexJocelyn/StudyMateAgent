"""显式 --run 才验证真实模型自然对话；使用临时库，会消耗供应商额度。"""
import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify(output):
    from backend.config import now
    from backend.storage.db import initialize, connect, dumps
    from backend.services.tools import execute
    from backend.main import app
    from fastapi.testclient import TestClient
    if os.getenv('AGENT_MODE') != 'llm' or not os.getenv('MODEL_API_KEY', '').strip():
        raise SystemExit('需要先在本机配置真实模型；未进行调用。')
    report = {'executed_at': now().isoformat(), 'model': os.getenv('MODEL_NAME', 'qwen-plus'),
              'real_provider': True, 'database': 'temporary', 'seeded_history_is_fixture': True,
              'structural_passed': False, 'semantic_review': 'pending', 'steps': []}
    def clean(value):
        if isinstance(value, dict): return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list): return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in (os.getenv('MODEL_API_KEY'), os.getenv('MODEL_BASE_URL'), os.getenv('STUDYMATE_ACCESS_PASSWORD')):
                if secret: value = value.replace(secret, '[已隐藏]')
            return re.sub(r'https?://[^\s"<>]+', '[地址已隐藏]', value)
        return value
    old = {k: os.environ.get(k) for k in ('STUDYMATE_DATA_DIR', 'STUDYMATE_PUBLIC', 'STUDYMATE_ACCESS_PASSWORD', 'WEB_SEARCH_ENABLED', 'WEATHER_ENABLED')}
    logger = logging.getLogger('backend.agent.runner')
    old_disabled = logger.disabled
    started = monotonic()
    try:
        with TemporaryDirectory(prefix='studymate-conversation-') as folder:
            os.environ.update({'STUDYMATE_DATA_DIR': folder, 'STUDYMATE_PUBLIC': '0', 'STUDYMATE_ACCESS_PASSWORD': ''})
            os.environ['WEB_SEARCH_ENABLED'] = '0'
            os.environ['WEATHER_ENABLED'] = '0'
            report['search_disabled_for_this_test'] = True
            report['weather_disabled_for_this_test'] = True
            logger.disabled = True  # 不输出可能带私有网络信息的供应商异常栈；只保存清理后的业务错误。
            initialize()
            with TestClient(app) as client:
                project = client.post('/api/projects', json={'name':'自然对话真实验收', 'members':['甲'],
                    'request_id':uuid4().hex}).json()['id']
                actor = client.get(f'/api/projects/{project}/plan').json()['members'][0]['id']
                sid = client.post('/api/sessions', json={'project_id':project, 'member_id':actor}).json()['session_id']
                fixture = [{'messages':[{'role':'user', 'content':'你不是接了千问的API吗'},
                    {'role':'assistant', 'content':'没有接入千问或其他外部大模型API；我只能处理课业任务。'}],
                    'events':[], 'download':None, 'member_id':actor, 'member_name':'甲'}]
                with connect() as db:
                    db.execute('UPDATE sessions SET history=? WHERE id=?', (dumps(fixture),sid)); db.commit()
                baseline = execute('get_plan', project, {})['data']; baseline.pop('exported_at')
                scenarios = [
                    ('纠正模型接入自述', '你不是接了千问的API吗？请准确说明现在接的模型，不要列任务功能。'),
                    ('普通知识问答', '什么是软件工程？用三句话解释就好。'),
                    ('自然闲聊', '今天有点累，只想随便聊聊天，不想安排任务。'),
                    ('实时天气边界', '今天北京会下雨吗？'),
                    ('依据用户天气给建议', '我查到今天北京会下雨，出门有什么建议？'),
                ]
                for label, text in scenarios:
                    turn_started = monotonic()
                    response = client.post('/api/chat', json={'project_id':project, 'member_id':actor,
                        'session_id':sid, 'request_id':uuid4().hex, 'message':text})
                    if response.status_code != 200: raise RuntimeError(f'{label} HTTP {response.status_code}')
                    result = response.json()
                    item = {'label':label, 'input':text, 'reply':result['reply'], 'ok':result['ok'],
                            'tools':[e['name'] for e in result['tool_events']], 'changed_ids':result['changed_ids'],
                            'error':result['error'], 'seconds':round(monotonic()-turn_started,2)}
                    report['steps'].append(item)
                    print(json.dumps(clean(item), ensure_ascii=False), flush=True)
                    if not result['ok']: raise RuntimeError(label+'模型业务调用未成功')
                    if result['tool_events'] or any(result['changed_ids'].values()):
                        raise RuntimeError(label+'不应调用业务工具或改变任务')
                    current = execute('get_plan', project, {})['data']; current.pop('exported_at')
                    if current != baseline: raise RuntimeError(label+'实际业务数据发生变化')
                    if not result['reply'].strip(): raise RuntimeError(label+'没有有效回复')
                report['structural_passed'] = True
    except Exception as exc:
        report['failure'] = clean(str(exc))
        raise RuntimeError(report['failure']) from None
    finally:
        for key,value in old.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        logger.disabled = old_disabled
        report['seconds'] = round(monotonic()-started,2)
        Path(output).write_text(json.dumps(clean(report),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f'记录已保存：{output}；结构检查={report["structural_passed"]}，回复语义仍需审阅。',flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--report',default=str(ROOT/'docs/CONVERSATION_VERIFICATION.json'))
    args=parser.parse_args()
    if not args.run: raise SystemExit('未调用模型。显式传入--run才执行真实供应商测试，会消耗额度。')
    verify(args.report)
