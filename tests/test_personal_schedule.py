from uuid import uuid4
from backend.config import PROJECT_ID as P
from backend.services.tools import execute
from backend.storage.db import connect


def call(name,args=None):return execute(name,P,args or {},uuid4().hex)


def create(owner,title='个人测试任务',minutes=60):
    result=call('create_tasks',{'tasks':[{'title':title,'assignee_id':owner,'deadline':'2030-11-06T22:00:00+08:00','estimated_minutes':minutes}]})
    assert result['ok'];return result['data'][0]


def available(owner):
    assert call('set_preferences',{'assignee_id':owner,'windows':[{'start_at':'2030-11-05T19:00:00+08:00','end_at':'2030-11-05T22:00:00+08:00'}]})['ok']


def proposal(owner):
    value=call('propose_schedule',{'assignee_id':owner});assert value['ok'];return value['data']


def save(owner,draft):return call('save_schedule',{'assignee_id':owner,'blocks':draft['blocks'],'expected_version':draft['version']})


def test_personal_proposal_is_not_blocked_by_another_members_missing_windows(isolated):
    a=create('member_a','甲的任务');create('member_b','乙的任务');available('member_a')
    mine=proposal('member_a')
    assert mine['feasible'] and {b['task_id'] for b in mine['blocks']}=={a['id']}
    assert not call('propose_schedule')['data']['feasible']
    assert save('member_a',mine)['ok']


def test_personal_save_clear_and_retry_preserve_other_schedule_row_ids(isolated):
    create('member_a');create('member_b');available('member_a');available('member_b')
    assert save('member_b',proposal('member_b'))['ok']
    other=[b for b in call('get_plan')['data']['schedule'] if b['assignee_id']=='member_b']
    mine=proposal('member_a')
    args={'assignee_id':'member_a','blocks':mine['blocks'],'expected_version':mine['version']}
    mutation=uuid4().hex
    result=execute('save_schedule',P,args,mutation)
    assert result['ok'] and result==execute('save_schedule',P,args,mutation)
    assert not {b['id'] for b in other}&set(result['changed_ids']['schedule'])
    plan=call('get_plan')['data']
    assert [b for b in plan['schedule'] if b['assignee_id']=='member_b']==other
    assert call('save_schedule',{'assignee_id':'member_a','blocks':[],'expected_version':plan['version']})['ok']
    assert call('get_plan')['data']['schedule']==other


def test_personal_scope_rejects_foreign_member_blocks_and_members(isolated):
    create('member_b');available('member_b');draft=proposal('member_b')
    assert not save('member_a',draft)['ok']
    assert not call('propose_schedule',{'assignee_id':'not-in-this-project'})['ok']
    assert not call('save_schedule',{'assignee_id':'not-in-this-project','blocks':[],'expected_version':0})['ok']
    assert not call('get_plan')['data']['schedule']


def test_concurrent_personal_drafts_require_regeneration_without_overwriting_others(isolated):
    create('member_a');create('member_b');available('member_a');available('member_b')
    a,b=proposal('member_a'),proposal('member_b')
    assert save('member_b',b)['ok']
    result=save('member_a',a)
    assert not result['ok'] and result['error']['code']=='VERSION_CONFLICT'
    before=call('get_plan')['data']['schedule']
    assert save('member_a',proposal('member_a'))['ok']
    assert [b for b in call('get_plan')['data']['schedule'] if b['assignee_id']=='member_b']==before


def test_button_generates_personal_draft_and_apply_and_clear_use_real_mcp(client):
    create('member_a','甲任务');create('member_b','乙任务');available('member_a');available('member_b')
    assert save('member_b',proposal('member_b'))['ok']
    other=[b for b in call('get_plan')['data']['schedule'] if b['assignee_id']=='member_b']
    sid=client.post('/api/sessions',json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    def send(action):
        response=client.post('/api/chat',json={'project_id':P,'member_id':'member_a','session_id':sid,'request_id':uuid4().hex,'action':action})
        assert response.status_code==200;return response.json()
    draft=send({'type':'generate_schedule','scope':'mine'})
    assert draft['ok'] and draft['draft']['data']['assignee_id']=='member_a'
    assert {b['assignee_id'] for b in draft['draft']['data']['blocks']}=={'member_a'}
    assert call('get_plan')['data']['schedule']==other  # Drafting does not save.
    assert any(e['name']=='propose_schedule' and e['transport']=='MCP stdio' for e in draft['tool_events'])
    assert send({'type':'apply_draft','draft_id':draft['draft']['id']})['ok']
    assert [b for b in call('get_plan')['data']['schedule'] if b['assignee_id']=='member_b']==other
    assert send({'type':'clear_schedule','scope':'mine'})['ok']
    assert call('get_plan')['data']['schedule']==other


def test_overdue_button_reports_real_reason_and_does_not_change_deadline(client):
    task=create('member_a','已过期的测试任务');available('member_a')
    with connect() as db:
        db.execute('UPDATE tasks SET deadline=? WHERE id=?',('2020-11-06T22:00:00+08:00',task['id']));db.commit()
    sid=client.post('/api/sessions',json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    result=client.post('/api/chat',json={'project_id':P,'member_id':'member_a','session_id':sid,'request_id':uuid4().hex,
        'action':{'type':'generate_schedule','scope':'mine'}}).json()
    assert result['draft'] is None and '已过截止时间' in result['reply'] and task['title'] in result['reply']
    assert 'member_' not in result['reply'] and 'task_' not in result['reply']
    assert call('list_tasks')['data'][0]['deadline'].startswith('2020-11-06')


def test_failed_regeneration_does_not_leave_an_old_adoptable_schedule(client):
    create('member_a');available('member_a')
    sid=client.post('/api/sessions',json={'project_id':P,'member_id':'member_a'}).json()['session_id']
    def generate():return client.post('/api/chat',json={'project_id':P,'member_id':'member_a','session_id':sid,'request_id':uuid4().hex,
        'action':{'type':'generate_schedule','scope':'mine'}}).json()
    assert generate()['draft']
    assert call('set_preferences',{'assignee_id':'member_a','windows':[]})['ok']
    result=generate()
    assert result['draft'] is None and '未来空闲时段' in result['reply']
