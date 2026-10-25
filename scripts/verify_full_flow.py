"""完整真实模型验收。显式 --run 才调用供应商并消耗额度；使用临时库。

覆盖：作业拆解草案、采用防重、容量不足、可行排程、任务暂停/继续、
多轮完成与导出、成员上下文隔离、重启恢复。不包含麦克风或另一台电脑验收。
"""
import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify(report_path):
    from backend.config import now
    from backend.main import app
    from backend.storage.db import initialize
    from fastapi.testclient import TestClient
    if os.getenv('AGENT_MODE') != 'llm' or not os.getenv('MODEL_API_KEY', '').strip():
        raise SystemExit('需要先在本机配置真实模型和 llm 模式。')
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'executed_at': now().isoformat(), 'model': os.getenv('MODEL_NAME', 'qwen-plus'),
              'real_provider': True, 'database': 'temporary', 'transport': 'MCP stdio',
              'passed': False, 'steps': [], 'checks': [],
              'not_tested': ['真实麦克风及朗读', '另一台电脑安装复现', '公网部署与账号登录']}

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in (os.getenv('MODEL_API_KEY'), os.getenv('MODEL_BASE_URL')):
                if secret:
                    value = value.replace(secret, '[已隐藏]')
            return re.sub(r'https?://[^\s"<>]+', '[接口地址已隐藏]', value)
        return value

    def require(condition, description):
        if not condition:
            raise RuntimeError(description)
        report['checks'].append(description)

    previous_data = os.environ.get('STUDYMATE_DATA_DIR')
    started = monotonic()
    try:
        with TemporaryDirectory(prefix='studymate-full-') as tmp:
            os.environ['STUDYMATE_DATA_DIR'] = tmp
            initialize()
            with TestClient(app) as client:
                project = client.post('/api/projects', json={'name': '课程验收临时项目',
                    'members': ['测试同学甲', '测试同学乙'], 'request_id': uuid4().hex}).json()['id']
                def plan():
                    return client.get(f'/api/projects/{project}/plan').json()
                members = plan()['members']
                actor = next(m for m in members if m['name'] == '测试同学甲')['id']
                other = next(m for m in members if m['name'] == '测试同学乙')['id']
                sid = client.post('/api/sessions', json={'project_id': project, 'member_id': actor}).json()['session_id']
                other_sid = client.post('/api/sessions', json={'project_id': project, 'member_id': other}).json()['session_id']
                def context(session=sid, member=actor):
                    return client.get(f'/api/sessions/{session}', params={'project_id': project, 'member_id': member}).json()
                def send(label, message='', action=None, request_id=None):
                    begin = monotonic()
                    response = client.post('/api/chat', json={'project_id': project, 'member_id': actor,
                        'session_id': sid, 'request_id': request_id or uuid4().hex, 'message': message, 'action': action})
                    if response.status_code != 200:
                        raise RuntimeError(f'{label} HTTP {response.status_code}')
                    result = response.json()
                    step = {'label': label, 'input': message, 'action': action, 'ok': result['ok'],
                            'reply': result['reply'], 'events': result['tool_events'], 'error': result['error'],
                            'draft': result.get('draft'),
                            'elapsed_seconds': round(monotonic() - begin, 2), 'changed_ids': result['changed_ids']}
                    report['steps'].append(clean(step))
                    print(json.dumps({'step': len(report['steps']), 'label': label, 'ok': result['ok'],
                        'tools': [e['name'] for e in result['tool_events']]}, ensure_ascii=False), flush=True)
                    require(result['ok'], label + '：接口成功')
                    return result

                tomorrow = (now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
                deadline = tomorrow.replace(hour=22)
                first = send('真实作业拆解', f'老师要求小组开发一个能自然语言对话、实际调用工具的智能体，并提交需求描述、业务流、实现说明、演示和博客。'
                    f'我们做课业任务助理，当前有测试同学甲、测试同学乙两人。请拆成4个可演示的最小任务草案，涵盖需求与业务流、模型与工具链、网页交互、测试与说明，'
                    f'每人2项，每项按30分钟的最小演示范围估时，截止北京时间{deadline:%Y-%m-%d %H:%M}。这些分工和估时已经确认。'
                    '请实际调用草案工具，等待我确认，不要直接创建正式任务。')
                draft = first.get('draft')
                require(bool(draft and draft['type'] == 'tasks' and len(draft['data']) == 4), '生成四项待确认任务草案')
                # 草案自然语言需人工复核；不能仅因“非 mock”含有 mock 关键字就判为失败。
                report['draft_content_review'] = '请根据步骤中的完整草案人工核对范围、估时和真实工具交付要求；自动通过不代表语义建议全部正确。'
                require(not plan()['tasks'], '草案尚未采用时任务库为空')
                rid = uuid4().hex
                action = {'type': 'apply_draft', 'draft_id': draft['id']}
                applied = send('确认采用任务', '采用任务草案', action, rid)
                repeated = send('同请求重试', '采用任务草案', action, rid)
                require(applied == repeated and len(plan()['tasks']) == 4, '重复采用请求不重复创建任务')
                require(all(t['estimated_minutes'] == 30 for t in plan()['tasks']), '已保存估时与确认的30分钟一致')
                require(all(sum(t['assignee_id'] == m['id'] for t in plan()['tasks']) == 2 for m in members), '两名成员各有两项任务')
                for m in members:
                    send('保存不足的可用时间', action={'type': 'set_preferences', 'assignee_id': m['id'],
                        'windows': [{'start_at': tomorrow.isoformat(), 'end_at': (tomorrow + timedelta(minutes=15)).isoformat()}]})
                short = send('容量不足检查', '请根据实际任务和已保存空闲时间生成日程草案；如果排不完，请说明每个人缺多少时间，不要少排任务或自行增加可用时间。')
                require(not short.get('draft') and not plan()['schedule'], '容量不足时不保存日程、不生成可采用草案')
                require(any(e['name'] == 'propose_schedule' for e in short['tool_events']), '容量不足判断来自真实MCP排程工具')
                for m in members:
                    send('保存充足的可用时间', action={'type': 'set_preferences', 'assignee_id': m['id'],
                        'windows': [{'start_at': tomorrow.isoformat(), 'end_at': (tomorrow + timedelta(hours=2)).isoformat()}]})
                scheduled = send('可行日程草案', '现在可用时间已补充。请实际调用排程草案工具，按已保存的任务和可用时间安排，先不要保存，等我确认。')
                sd = scheduled.get('draft')
                require(bool(sd and sd['type'] == 'schedule'), '可用时间足够后生成待确认日程草案')
                require(not plan()['schedule'], '日程草案尚未采用时数据库不保存日程')
                send('确认保存日程', '确认保存日程草案', {'type': 'apply_draft', 'draft_id': sd['id']})
                require(len(plan()['schedule']) == 4, '日程采用后四项任务均有真实排程')
                task = next(t for t in plan()['tasks'] if t['assignee_id'] == actor)
                initial = plan()
                def status():
                    return next(t['status'] for t in plan()['tasks'] if t['id'] == task['id'])
                send('自然语言开始任务', f'把我的任务「{task["title"]}」标记为进行中，请实际更新。')
                require(status() == 'doing', '自然语言开始了正确成员的任务')
                send('多轮暂停', '先暂停它，我有空再继续。请只暂停这一个任务。')
                require(status() == 'paused' and plan()['schedule'] == initial['schedule'], '多轮暂停正确任务且保留原日程')
                send('多轮继续', '我现在有空了，继续刚才暂停的那个任务，请实际更新状态。')
                require(status() == 'doing', '多轮继续恢复同一任务为进行中')
                completed = send('多轮完成并导出', '把它标记为已完成，然后导出当前项目计划，请实际执行这两个操作。')
                require(status() == 'done', '多轮完成的是同一个任务')
                require(not any(b['task_id'] == task['id'] for b in plan()['schedule']), '完成任务后清除对应日程')
                download = completed.get('download')
                require(bool(download), '完成后实际生成下载附件')
                exported = client.get(download['url'], params={'project_id': project})
                require(exported.status_code == 200 and task['title'] in exported.text and '已完成' in exported.text,
                        '实际下载内容与数据库最终任务状态一致')
                export_path = destination.with_suffix('.md')
                export_path.write_text(exported.text)
                report['export_artifact'] = export_path.name
                require(not context(other_sid, other)['history'] and not context(other_sid, other)['draft'], '其他成员的对话和草案未混入本次会话')
                before = context()
                initialize()
                require(context() == before, '数据库重新初始化后恢复同一成员对话、焦点和草案状态')
                report['final_snapshot'] = plan()
                report['passed'] = True
    except Exception as exc:
        report['failure'] = clean(str(exc))
        raise
    finally:
        if previous_data is None:
            os.environ.pop('STUDYMATE_DATA_DIR', None)
        else:
            os.environ['STUDYMATE_DATA_DIR'] = previous_data
        report['elapsed_seconds'] = round(monotonic() - started, 2)
        destination.write_text(json.dumps(clean(report), ensure_ascii=False, indent=2) + '\n')
        print(f'验收记录已保存，passed={report["passed"]}。未使用用户项目数据。', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='执行真实模型请求，会消耗模型额度')
    parser.add_argument('--report', default=str(ROOT / 'docs' / 'FULL_FLOW_VERIFICATION.json'))
    args = parser.parse_args()
    if not args.run:
        raise SystemExit('未调用供应商。准备好后显式传入 --run；会消耗模型额度。')
    verify(args.report)
