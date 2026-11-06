from uuid import uuid4
from backend.config import PROJECT_ID as P
from backend.services import review
from backend.services.tools import execute


def test_workspace_and_delivery_are_separate_routes_in_one_app(client):
    home=client.get('/')
    assert home.status_code==200
    assert '当前项目工作流程' in home.text and 'chat-stream' in home.text
    assert 'project-instructions' in home.text and 'project-delivery' in home.text
    assert client.get('/tasks').text==home.text
    delivery=client.get('/review')
    assert delivery.status_code==200 and 'id="results-list"' in delivery.text and 'id="workspace-link"' in delivery.text


def test_delivery_summary_tracks_real_followup_status_without_auto_confirmation(client,monkeypatch):
    client.put(f'/api/review/{P}/instructions',json={'text':'报告必须包含真实用户访谈记录。'})
    w=client.post(f'/api/review/{P}/materials',json={'name':'报告.md','content':'报告已准备，但还没有进行用户访谈。'}).json()
    async def model(w):
        return [{'title':'用户访谈','requirement_quote':'报告必须包含真实用户访谈记录','status':'missing','explanation':'没有访谈记录','evidence':[],'suggestion':'开展真实访谈。'}]
    monkeypatch.setattr(review,'ask_model',model)
    result=client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex})
    assert result.status_code==200,result.text
    rid=result.json()['latest_run']['id']
    client.patch(f'/api/review/{P}/human',json={'run_id':rid,'index':0,'status':'needs_work','note':'已确认需要补充'})
    body={'run_id':rid,'index':0,'assignee_id':'member_a','deadline':'2030-11-08T20:00:00+08:00','estimated_minutes':30}
    created=client.post(f'/api/review/{P}/tasks',json=body)
    assert created.status_code==200,created.text
    summary=client.get(f'/api/review/{P}/summary').json()
    assert summary['followups_open']==1 and summary['needs_work']==1
    updated=execute('update_task',P,{'task_id':created.json()['data']['id'],'patch':{'status':'done'}},'finish-followup')
    assert updated['ok']
    summary=client.get(f'/api/review/{P}/summary').json()
    assert summary['followups_open']==0 and summary['needs_work']==1
    client.put(f'/api/review/{P}/instructions',json={'text':'报告必须包含真实用户访谈记录和访谈日期。'})
    assert client.get(f'/api/review/{P}/summary').json()['stale']
    other=client.post('/api/projects',json={'name':'其他课程','members':['同学'],'request_id':uuid4().hex}).json()['id']
    assert client.get(f'/api/review/{other}/summary').json()['item_count']==0
    assert client.get('/api/review/not-existing/summary').status_code==400
