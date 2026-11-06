import base64
import io
import zipfile
from uuid import uuid4
import pytest
from backend.config import PROJECT_ID as P
from backend.services import review
from backend.services.tools import BusinessError


def material(client, name='报告.md', text='系统采用FastAPI后端，使用SQLite保存任务。'):
    return client.post(f'/api/review/{P}/materials',json={'name':name,'content':text}).json()


def setup(client):
    client.put(f'/api/review/{P}/instructions',json={'text':'必须说明系统架构。\n必须提供团队合照。'})
    return material(client)


def items(w):
    return [{'title':'系统架构','requirement_quote':'必须说明系统架构','status':'evidence_found','explanation':'材料说明了架构技术，质量仍需人工判断。',
             'evidence':[{'material_id':w['materials'][0]['id'],'quote':'系统采用FastAPI后端'}],'suggestion':'人工核对流程。'},
            {'title':'团队合照','requirement_quote':'必须提供团队合照','status':'uncertain','explanation':'纯文字不能核验实际照片。','evidence':[],'suggestion':'补充真实团队合照。'}]


def test_persistent_sources_and_version(client):
    assert client.get('/').status_code==200
    assert 'id="project-delivery"' in client.get('/').text
    assert 'id="results-list"' in client.get('/review').text
    assert '任务与日程' in client.get('/tasks').text
    w=setup(client)
    version=w['version']
    assert material(client)['version']==version
    assert client.get(f'/api/review/{P}').json()['materials'][0]['name']=='报告.md'
    assert client.get('/api/review/nonexistent').status_code==400


def test_quotes_must_be_real_and_in_project(client):
    w=setup(client); original=items(w)
    assert review.validate_items(w,original)[0]['evidence'][0]['line']==1
    for field in ('requirement','evidence','material_id'):
        bad=items(w)
        if field=='requirement': bad[0]['requirement_quote']='没有出现的要求'
        if field=='evidence': bad[0]['evidence'][0]['quote']='并不存在的证据'
        if field=='material_id': bad[0]['evidence'][0]['material_id']='other-project'
        with pytest.raises(BusinessError): review.validate_items(w,bad)
    bad=items(w);bad[0]['evidence']=[]
    with pytest.raises(BusinessError):review.validate_items(w,bad)
    bad=items(w);bad[0]['status']='missing'
    with pytest.raises(BusinessError):review.validate_items(w,bad)


def test_analysis_real_mcp_persistence_and_expiry(client,monkeypatch):
    w=setup(client); calls=[]
    async def model(s): calls.append(s);return items(s)
    monkeypatch.setattr(review,'ask_model',model)
    rid=uuid4().hex
    a=client.post(f'/api/review/{P}/analyze',json={'request_id':rid})
    assert a.status_code==200,a.text
    r=a.json()['latest_run'];assert r['items'][0]['human_status']=='unreviewed'
    assert len(r['events'])==2
    assert client.post(f'/api/review/{P}/analyze',json={'request_id':rid}).json()['latest_run']['id']==r['id']
    assert len(calls)==1
    human={'run_id':r['id'],'index':1,'status':'needs_work','note':'已人工确认需要提供照片'}
    assert client.patch(f'/api/review/{P}/human',json=human).status_code==200
    export=client.post(f'/api/review/{P}/export',json={'run_id':r['id']}).json()
    doc=client.get(export['url']);assert '需要补充' in doc.text and '老师要求' not in doc.text[:5]
    changed=client.put(f'/api/review/{P}/instructions',json={'text':'必须提供公开代码仓库链接。'}).json()
    assert changed['stale']
    assert client.patch(f'/api/review/{P}/human',json=human).status_code==400
    old=client.post(f'/api/review/{P}/export',json={'run_id':r['id']}).json()
    assert '旧结果' in client.get(old['url']).text


def test_stale_save_and_fabricated_output_do_not_write(client,monkeypatch):
    setup(client)
    async def fabricated(w):
        x=items(w);x[0]['requirement_quote']='凭空新增的要求';return x
    monkeypatch.setattr(review,'ask_model',fabricated)
    assert client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex}).status_code==400
    assert client.get(f'/api/review/{P}').json()['latest_run'] is None
    async def race(w):
        review.set_instructions(P,'要求变化后必须重新自查。');return items(w)
    monkeypatch.setattr(review,'ask_model',race)
    r=client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex})
    assert r.status_code==400 and r.json()['error']['code']=='STALE_REVIEW'
    assert client.get(f'/api/review/{P}').json()['latest_run'] is None


def test_followup_requires_human_and_is_idempotent(client,monkeypatch):
    setup(client)
    async def model(w):return items(w)
    monkeypatch.setattr(review,'ask_model',model)
    w=client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex}).json();r=w['latest_run']
    task={'run_id':r['id'],'index':1,'assignee_id':'member_a','deadline':'2030-11-08T22:00:00+08:00','estimated_minutes':30}
    assert client.post(f'/api/review/{P}/tasks',json=task).status_code==400
    client.patch(f'/api/review/{P}/human',json={'run_id':r['id'],'index':1,'status':'needs_work','note':''})
    created=client.post(f'/api/review/{P}/tasks',json=task)
    assert created.status_code==200,created.text
    assert client.post(f'/api/review/{P}/tasks',json=task).json()['data']['id']==created.json()['data']['id']
    assert len(client.get(f'/api/projects/{P}/tasks').json())==1
    task['assignee_id']='member_b'
    assert client.post(f'/api/review/{P}/tasks',json=task).json()['error']['code']=='REQUEST_CONFLICT'
    assert len(client.get(f'/api/projects/{P}/tasks').json())==1


def test_project_scope(client,monkeypatch):
    setup(client)
    other=client.post('/api/projects',json={'name':'另一个作业','members':['同学'],'request_id':uuid4().hex}).json()['id']
    material_id=client.get(f'/api/review/{P}').json()['materials'][0]['id']
    assert client.delete(f'/api/review/{other}/materials/{material_id}').status_code==400
    assert not client.get(f'/api/review/{other}').json()['materials']


def test_docx_text_and_unsupported_formats(client):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>这是真实正文</w:t></w:r></w:p></w:body></w:document>')
    payload={'name':'报告.docx','content_base64':base64.b64encode(b.getvalue()).decode()}
    r=client.post(f'/api/review/{P}/upload',json=payload)
    assert r.status_code==200 and r.json()['materials'][0]['content']=='这是真实正文'
    for name in ['扫描.pdf','图片.png','损坏.docx']:
        r=client.post(f'/api/review/{P}/upload',json={'name':name,'content_base64':base64.b64encode(b'not a document').decode()})
        assert r.status_code==400
    with pytest.raises(BusinessError):review.read_file('长文.md',base64.b64encode(b'x'*524289).decode())


def test_no_model_no_fake_result(client):
    setup(client)
    r=client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex})
    assert r.status_code==400 and r.json()['error']['code']=='MODEL_NOT_CONFIGURED'
    assert client.get(f'/api/review/{P}').json()['latest_run'] is None


def test_link_presence_is_not_access_verification(client):
    client.put(f'/api/review/{P}/instructions',json={'text':'提供可公开访问的GitHub仓库链接。'})
    w=material(client,text='仓库链接：https://github.com/example/project')
    candidate=[{'title':'仓库','requirement_quote':'提供可公开访问的GitHub仓库链接','status':'evidence_found',
                'explanation':'找到了链接','evidence':[{'material_id':w['materials'][0]['id'],'quote':'https://github.com/example/project'}],'suggestion':''}]
    assert review.validate_items(w,candidate)[0]['status']=='uncertain'


def test_material_ids_use_file_names_in_prose_and_export(client,monkeypatch):
    w=setup(client)
    mid=w['materials'][0]['id']
    async def model(source):
        result=items(source)
        result[0]['explanation']=f'{mid} 提供了架构说明。'
        result[0]['suggestion']=f'请核对 {mid} 的正文。'
        return result
    monkeypatch.setattr(review,'ask_model',model)
    response=client.post(f'/api/review/{P}/analyze',json={'request_id':uuid4().hex})
    assert response.status_code==200,response.text
    run=response.json()['latest_run']
    item=run['items'][0]
    assert '材料《报告.md》' in item['explanation'] and mid not in item['explanation']
    assert mid not in item['suggestion']
    assert item['evidence'][0]['material_id']==mid
    exported=client.post(f'/api/review/{P}/export',json={'run_id':run['id']}).json()
    document=client.get(exported['url']).text
    assert mid not in document and '材料《报告.md》' in document


def test_review_export_uses_readable_beijing_time(client,monkeypatch):
    from datetime import datetime
    w=setup(client)
    saved=review.record_analysis(P,'readable-time',w['version'],items(w))
    original_created_at=review.get_workspace(P)['latest_run']['created_at']
    monkeypatch.setattr(review,'now',lambda:datetime.fromisoformat('2026-11-05T23:07:28.233123Z'))
    exported=client.post(f'/api/review/{P}/export',json={'run_id':saved['data']['run_id']}).json()
    content=client.get(exported['url']).text
    assert '生成时间：2026年11月6日 07:07（北京时间）' in content
    assert '.233123' not in content and '+08:00' not in content
    # Formatting the export does not change the saved analysis timestamp.
    assert client.get(f'/api/review/{P}').json()['latest_run']['created_at']==original_created_at
