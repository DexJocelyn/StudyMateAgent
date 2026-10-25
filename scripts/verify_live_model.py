"""显式执行真实供应商 → Agent → MCP → SQLite 验收，使用临时项目和临时数据库。

运行会消耗模型额度。仅在用户配置好凭据并明确测试时执行 --run。
从环境读取密钥，不打印密钥或业务空间接口域名，不读取用户现有任务。
"""
import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify(report_path=None):
    from backend.config import now
    if os.getenv('AGENT_MODE', 'demo') != 'llm' or not os.getenv('MODEL_API_KEY', '').strip():
        raise SystemExit('需要先配置 llm 模式和模型密钥。')
    from backend.storage.db import initialize
    from backend.main import app
    from fastapi.testclient import TestClient

    report = {'executed_at': now().isoformat(), 'model': os.getenv('MODEL_NAME', 'qwen-plus'),
              'real_provider': True, 'database': 'temporary', 'transport': 'MCP stdio', 'steps': []}
    with TemporaryDirectory(prefix='studymate-live-') as tmp:
        os.environ['STUDYMATE_DATA_DIR'] = tmp
        os.environ['AGENT_MAX_ROUNDS'] = '6'
        initialize()
        deadline = (now() + timedelta(days=3)).replace(hour=22, minute=0, second=0, microsecond=0)
        with TestClient(app) as client:
            project = client.post('/api/projects', json={'name': 'AI联调临时项目', 'members': ['测试同学'],
                'request_id': uuid4().hex}).json()['id']
            sid = client.post('/api/sessions', json={'project_id': project}).json()['session_id']

            def send(message):
                response = client.post('/api/chat', json={'project_id': project, 'session_id': sid,
                    'request_id': uuid4().hex, 'message': message})
                if response.status_code != 200:
                    raise RuntimeError(f'聊天接口 HTTP {response.status_code}')
                result = response.json()
                report['steps'].append({'input': message, 'ok': result['ok'], 'reply': result['reply'],
                    'events': result['tool_events'], 'error': result['error']})
                print(json.dumps({'step': len(report['steps']), 'ok': result['ok'],
                    'reply': result['reply'], 'tools': [e['name'] for e in result['tool_events']],
                    'error': result['error']}, ensure_ascii=False), flush=True)
                if not result['ok']:
                    raise RuntimeError(result['error']['message'])
                return result

            created = send(f'请直接创建一条任务，不需要草案：标题为「模型联调验收」，负责人是测试同学，'
                           f'预计30分钟，截止时间为北京时间{deadline:%Y-%m-%d %H:%M}。请实际调用工具保存。')
            tasks = client.get(f'/api/projects/{project}/tasks').json()
            if len(tasks) != 1 or tasks[0]['title'] != '模型联调验收':
                raise RuntimeError('真实数据库未出现预期任务，不能视为通过。')
            if not any(e['name'] == 'create_tasks' and e['status'] == 'success' for e in created['tool_events']):
                raise RuntimeError('缺少真实 MCP 创建事件。')
            updated = send('把它标记为已完成，然后导出当前项目计划。请实际执行两个操作。')
            tasks = client.get(f'/api/projects/{project}/tasks').json()
            if len(tasks) != 1 or tasks[0]['status'] != 'done':
                raise RuntimeError('多轮操作没有完成正确任务。')
            download = updated.get('download')
            if not download:
                raise RuntimeError('未生成真实下载元数据。')
            exported = client.get(download['url'], params={'project_id': project})
            if exported.status_code != 200 or '模型联调验收' not in exported.text or '已完成' not in exported.text:
                raise RuntimeError('导出文件内容与任务状态不符。')
            report['passed'] = True
            report['checks'] = ['真实模型选择工具', 'MCP创建任务', 'SQLite核对', '多轮指代完成任务', 'MCP导出与附件核对']
    if report_path:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('真实模型联调通过；使用临时数据库，未修改用户项目或任务。', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='确认执行真实模型调用，会消耗供应商额度')
    parser.add_argument('--report', help='可选的本地 JSON 验收记录路径（不含密钥）')
    args = parser.parse_args()
    if not args.run:
        raise SystemExit('未调用模型。准备好后显式传入 --run；此操作会消耗供应商额度。')
    verify(args.report)
