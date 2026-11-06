import json
from datetime import datetime
from uuid import uuid4
import httpx
from backend.agent.presentation import clock_facts, deadline_facts, readable_reply
from backend.config import PROJECT_ID as P, TZ
from backend.services.tools import execute
from backend.storage.db import connect


def test_dates_use_beijing_clock_and_completed_tasks_are_not_overdue():
    current = datetime(2026, 11, 3, 12, tzinfo=TZ)
    assert clock_facts(current)['weekday'] == '周二'
    tasks = [dict(id='task_old', title='需求报告', deadline='2026-10-25T22:00:00+08:00', status='todo'),
             dict(id='task_future', title='PPT', deadline='2026-11-04T20:00:00+08:00', status='doing')]
    old, future = deadline_facts(tasks, current)
    assert old['relation'] == 'overdue' and old['time_label'] == '已逾期8天14小时'
    assert old['deadline_weekday'] == '周日'
    assert future['time_label'] == '距截止还有1天8小时'
    tasks[0]['status'] = 'done'
    assert deadline_facts(tasks, current)[0]['relation'] == 'completed'


def test_names_replace_only_known_unique_ids_and_respect_explicit_id_requests():
    names = {'task_2d33ea1234': '整理报告', 'member_a': '小陈'}
    text = '`task_2d33ea...` 由 member_a 负责，task_unknown 不确定。'
    assert readable_reply(text, names, '安排日程') == '「整理报告」 由 「小陈」 负责，task_unknown 不确定。'
    assert readable_reply(text, names, '告诉我任务ID') == text
    names['task_2d33ea5678'] = '其他报告'
    assert '`task_2d33ea...`' in readable_reply(text, names, '安排日程')


def test_old_history_does_not_set_clock_and_tool_results_keep_real_ids(client, monkeypatch):
    current = datetime(2026, 11, 3, 12, tzinfo=TZ)
    monkeypatch.setattr('backend.agent.runner.now', lambda: current)
    task = execute('create_tasks', P, {'tasks':[{'title':'整理报告','assignee_id':'member_a',
        'deadline':'2030-11-08T20:00:00+08:00','estimated_minutes':60}]}, uuid4().hex)['data'][0]
    sid = client.post('/api/sessions', json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    wrong = '截止于2026-10-25，本周六，距今还有1天。'
    with connect() as db:
        db.execute('UPDATE tasks SET deadline=? WHERE id=?', ('2026-10-25T22:00:00+08:00',task['id']))
        db.execute('UPDATE sessions SET history=? WHERE id=?', (json.dumps([{'messages':[
            {'role':'user','content':'截止时间？'},{'role':'assistant','content':wrong}],'events':[]}]),sid))
        db.commit()
    real_client = httpx.AsyncClient
    calls = []
    def handle(request):
        body = json.loads(request.content); calls.append(body)
        clock_message = next(m for m in body['messages'] if m['role']=='system' and '本轮服务端时间事实' in m['content'])
        assert '2026-11-03' in clock_message['content'] and '周二' in clock_message['content']
        if len(calls) == 1:
            raw = {'role':'assistant','content':'','tool_calls':[{'id':'plan1','type':'function',
                'function':{'name':'get_plan','arguments':'{}'}}]}
        else:
            tool = json.loads(body['messages'][-1]['content'])
            assert tool['data']['tasks'][0]['id'] == task['id']
            assert tool['task_deadline_facts'][0]['time_label'] == '已逾期8天14小时'
            assert '已逾期8天14小时' in clock_message['content']
            raw = {'role':'assistant','content':f"`{task['id']}` 已逾期8天14小时，请 member_a 确认新截止时间。"}
        return httpx.Response(200,json={'choices':[{'message':raw}]})
    monkeypatch.setenv('AGENT_MODE','llm'); monkeypatch.setenv('MODEL_API_KEY','test-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle),**kw))
    result = client.post('/api/chat',json={'project_id':P,'member_id':'member_a','session_id':sid,
        'request_id':uuid4().hex,'message':'请查询任务，帮我安排日程'}).json()
    assert result['ok'] and '「整理报告」' in result['reply'] and 'task_' not in result['reply']
    assert 'member_' not in result['reply'] and len(calls) == 2
    assert execute('list_tasks',P,{})['data'][0]['deadline'].startswith('2026-10-25')


def test_user_facing_schedule_text_formats_fields_dates_and_preserves_raw_requests():
    source = ('两项任务（ID `task_ab123`）。截止2026-11-06T20:00:00+08:00；'
              '`get_preferences` 确认非空 windows。无冲突（`conflicts: []`），'
              '无缺口（`missing_minutes_by_assignee: {}`）。使用 get_plan 与 draft_schedule（MCP）。')
    readable = readable_reply(source, {'task_ab123':'整理需求'}, '说明安排结果，不要输出ID')
    for word in ('task_ab123','get_preferences','windows','conflicts','missing_minutes','get_plan','draft_schedule','MCP','+08:00'):
        assert word not in readable
    assert '「整理需求」' in readable and '2026年11月6日 20:00（北京时间）' in readable
    assert '无时间冲突' in readable and '各成员的任务均可安排' in readable
    assert readable_reply(source, {}, '请显示原始工具结果') == source
    assert readable_reply('MCP 通过 get_plan 查询项目', {}, '解释一下 MCP 是什么') == 'MCP 通过 get_plan 查询项目'
    assert readable_reply('已通过 MCP 保存日程。', {}, '采用草案') == '已保存日程。'
    protected = 'https://example.com/task_ab123\n```json\n{"windows": []}\n```'
    assert readable_reply(protected, {'task_ab123':'整理需求'}, '说明安排') == protected
    assert readable_reply('2026-11-06T12:00:00Z', {}, '说明时间') == '2026年11月6日 20:00（北京时间）'


def test_history_and_new_followup_use_project_names_without_rewriting_stored_messages(client, monkeypatch):
    task = execute('create_tasks', P, {'tasks':[{'title':'跨回合整理需求','assignee_id':'member_a',
        'deadline':'2030-11-08T20:00:00+08:00','estimated_minutes':60}]}, uuid4().hex)['data'][0]
    sid = client.post('/api/sessions',json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    raw = f"任务ID `{task['id']}`，使用 get_preferences 读取 windows，截止2030-11-08T20:00:00+08:00。"
    old = json.dumps([{'messages':[{'role':'user','content':'请说明排程结果'},
                                  {'role':'assistant','content':raw}],'events':[]}])
    with connect() as db:
        db.execute('UPDATE sessions SET history=? WHERE id=?',(old,sid));db.commit()
    view = client.get(f'/api/sessions/{sid}',params={'project_id':P,'member_id':'member_a'}).json()
    assert '跨回合整理需求' in view['history'][-1]['content'] and 'task_' not in view['history'][-1]['content']
    with connect() as db:
        assert db.execute('SELECT history FROM sessions WHERE id=?',(sid,)).fetchone()[0] == old
    # The follow-up does not call a task tool in this round; the display lookup still works.
    from tests.test_conversation import provider
    provider(monkeypatch, raw)
    result=client.post('/api/chat',json={'project_id':P,'member_id':'member_a','session_id':sid,
        'request_id':uuid4().hex,'message':'请把上一条说清楚，不要输出ID'}).json()
    assert result['ok'] and '跨回合整理需求' in result['reply'] and 'task_' not in result['reply']
    assert result['tool_events'] == []
    from backend.agent.presentation import project_display_names
    other=client.post('/api/projects',json={'name':'另一个小组','members':['其他同学'],'request_id':uuid4().hex}).json()['id']
    assert task['id'] not in project_display_names(other)
