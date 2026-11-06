from uuid import uuid4
import pytest
from backend.config import PROJECT_ID as P
from backend.services.tools import execute
from backend.storage.db import connect, initialize


def create(client):return client.post('/api/projects',json={'name':'待整理的练习项目','members':['同学'],'request_id':uuid4().hex}).json()['id']


def test_rename_archive_restore_preserve_tasks_members_history_and_preferences(client):
    p=create(client)
    member=client.get(f'/api/projects/{p}/members').json()[0]['id']
    assert execute('create_tasks',p,{'tasks':[{'title':'需要保留的任务','assignee_id':member,'deadline':'2030-11-08T20:00:00+08:00','estimated_minutes':60}]},uuid4().hex)['ok']
    sid=client.post('/api/sessions',json={'project_id':p,'member_id':member}).json()['session_id']
    client.put(f'/api/review/{p}/instructions',json={'text':'需要保留的老师要求。'})
    before=client.get(f'/api/projects/{p}/plan').json()
    renamed=client.patch(f'/api/projects/{p}',json={'name':' 正式课程项目 '})
    assert renamed.status_code==200 and renamed.json()['name']=='正式课程项目'
    after=client.get(f'/api/projects/{p}/plan').json()
    assert before['tasks']==after['tasks'] and before['members']==after['members']
    assert before['version']==after['version']
    assert client.patch(f'/api/projects/{p}',json={'archived':True}).json()['archived'] is True
    assert p not in [project['id'] for project in client.get('/api/projects').json()]
    archived=next(project for project in client.get('/api/projects?include_archived=true').json() if project['id']==p)
    assert archived['task_count']==1 and archived['member_count']==1
    initialize()
    assert p not in [project['id'] for project in client.get('/api/projects').json()]
    assert client.get(f'/api/sessions/{sid}',params={'project_id':p,'member_id':member}).status_code==200
    assert client.get(f'/api/review/{p}').json()['instructions']=='需要保留的老师要求。'
    assert client.patch(f'/api/projects/{p}',json={'archived':False}).json()['archived'] is False
    assert p in [project['id'] for project in client.get('/api/projects').json()]
    restored=client.get(f'/api/projects/{p}/plan').json()
    for key in ('tasks','members','preferences','schedule','version'):assert restored[key]==before[key]


def test_last_active_guard_and_repeated_archive_are_safe(client):
    p=create(client)
    assert client.patch(f'/api/projects/{p}',json={'archived':True}).status_code==200
    assert client.patch(f'/api/projects/{p}',json={'archived':True}).status_code==200
    failure=client.patch(f'/api/projects/{P}',json={'archived':True})
    assert failure.status_code==400 and failure.json()['error']['code']=='LAST_ACTIVE_PROJECT'
    assert client.get('/api/projects').json()[0]['id']==P


@pytest.mark.parametrize('patch',[{}, {'name':'  '},{'name':'abc','archived':True},{'archived':'false'},{'project_id':'other'},{'archived':None}])
def test_invalid_management_inputs_leave_project_unchanged(client,patch):
    before=client.get('/api/projects?include_archived=true').json()
    assert client.patch(f'/api/projects/{P}',json=patch).status_code in (400,422)
    assert client.get('/api/projects?include_archived=true').json()==before


def test_unknown_project_and_additive_upgrade(client):
    assert client.patch('/api/projects/not-existing',json={'name':'未知'}).status_code==400
    before=client.get('/api/projects').json()
    with connect() as db:db.execute('DROP TABLE project_archives');db.commit()
    initialize()
    assert client.get('/api/projects').json()==before
