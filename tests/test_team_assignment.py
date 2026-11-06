import json
from uuid import uuid4
import httpx
import pytest
from backend.agent.runner import is_team_assignment


@pytest.mark.parametrize('text',[
    '请根据当前项目成员拆分任务',
    '我们团队准备展示，请分配任务',
])
def test_team_assignment_intent(text):
    assert is_team_assignment(text)
    assert not is_team_assignment('帮我创建整理报告任务')
    assert not is_team_assignment('我们聊聊天吧')


@pytest.mark.parametrize('explicit_single',[False,True])
@pytest.mark.parametrize('wrong_assumption',['当前仅有一位成员：小陈。','暂定全部交给当前发言者。'])
def test_team_roster_precedes_model_and_invalid_solo_assumption_is_retried(client,monkeypatch,explicit_single,wrong_assumption):
    pid=client.post('/api/projects',json={'name':'团队分工回归','members':['小陈','小肖'],'request_id':uuid4().hex}).json()['id']
    members=client.get(f'/api/projects/{pid}/members').json()
    owners={m['name']:m['id'] for m in members}
    sid=client.post('/api/sessions',json={'project_id':pid,'member_id':owners['小陈']}).json()['session_id']
    real_client=httpx.AsyncClient
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        payload=json.loads(request.content)
        if calls==1:
            reminders=[m['content'] for m in payload['messages'] if m['role']=='system' and '预先读取名册' in m['content']]
            assert len(reminders)==1
            assert '"member_count":2' in reminders[0]
            assert owners['小肖'] in reminders[0] and '小陈' in reminders[0]
            args={'tasks':[{'title':'联调','assignee_id':owners['小陈'],'estimated_minutes':30,'deadline':'2030-11-08T20:00:00+08:00'}],
                  'assumptions':[wrong_assumption]}
            args['tasks'].append({**args['tasks'][0],'title':'交互检查'})
            if explicit_single and '仅有' not in wrong_assumption:
                args['assumptions']=['当前仅有一位成员：小陈。']
        elif calls==2:
            output=json.loads(payload['messages'][-1]['content'])
            assert output['error']['code']==('MEMBER_FACT_CONFLICT' if explicit_single or '仅有' in wrong_assumption else 'TEAM_ASSIGNMENT_COLLAPSED')
            assert '小肖' in output['error']['message']
            args={'tasks':[{'title':title,'assignee_id':owner,'estimated_minutes':30,'deadline':'2030-11-08T20:00:00+08:00'}
                           for title,owner in [('联调',owners['小陈']),('交互检查',owners['小陈'] if explicit_single else owners['小肖'])]],
                  'assumptions':['按用户明确指定给小陈。' if explicit_single else '按工作量提出暂定分工，待确认。']}
        else:
            output=json.loads(payload['messages'][-1]['content'])
            assert output['ok']
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'已生成待确认草案。'}}]})
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'draft_'+str(calls),'type':'function',
            'function':{'name':'draft_tasks','arguments':json.dumps(args)}}]}}]})
    monkeypatch.setenv('AGENT_MODE','llm');monkeypatch.setenv('MODEL_API_KEY','test-provider-only')
    monkeypatch.setattr('backend.agent.runner.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    message='请按当前项目成员分工，先给草案。' if not explicit_single else '我们团队的两项任务，请明确全部分配给小陈，先给草案。'
    result=client.post('/api/chat',json={'project_id':pid,'session_id':sid,'member_id':owners['小陈'],'request_id':uuid4().hex,'message':message}).json()
    assert result['ok'],result
    assert calls==3
    assert {t['assignee_id'] for t in result['draft']['data']}==({owners['小陈']} if explicit_single else set(owners.values()))
    assert client.get(f'/api/projects/{pid}/tasks').json()==[]
    assert result['tool_events'][0]['name']=='get_plan'
