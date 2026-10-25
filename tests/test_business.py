import pytest
from backend.config import PROJECT_ID as P
from backend.services.tools import execute
from backend.storage.db import connect, initialize


def call(name, args=None, mutation='one'):
    return execute(name, P, args or {}, mutation)


def create(data, mutation='create'):
    result = call('create_tasks', {'tasks': [data]}, mutation)
    assert result['ok'], result
    return result['data'][0]


def available(windows=None):
    return call('set_preferences', {'assignee_id': 'member_a', 'windows': windows or [
        {'start_at': '2030-10-24T19:00:00+08:00', 'end_at': '2030-10-24T22:00:00+08:00'}]}, 'prefs')


def block(task, start='2030-10-24T19:00:00+08:00', end='2030-10-24T20:00:00+08:00'):
    return {'task_id': task['id'], 'assignee_id': task['assignee_id'], 'start_at': start, 'end_at': end}


def test_persistence_and_idempotency(isolated, task_data):
    a = call('create_tasks', {'tasks': [task_data]})
    assert a == call('create_tasks', {'tasks': [task_data]})
    initialize()
    assert len(call('list_tasks')['data']) == 1
    assert call('create_tasks', {'tasks': [{**task_data, 'title': '不同内容'}]})['error']['code'] == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('patch', [
    {'assignee_id': 'member_unknown'}, {'estimated_minutes': -1}, {'estimated_minutes': 1.5},
    {'deadline': '2030-10-25T22:00:00'}, {'deadline': 'not-a-date'}, {'title': '  '}, {'arbitrary': 'field'},
])
def test_invalid_task_atomic(isolated, task_data, patch):
    result = call('create_tasks', {'tasks': [task_data, {**task_data, **patch}]})
    assert not result['ok']
    assert call('list_tasks')['data'] == []


def test_project_scope_and_patch_validation(isolated, task_data):
    t = create(task_data)
    with connect() as db:
        db.execute("INSERT INTO projects(id,name) VALUES('other','其他项目')")
        db.commit()
    assert not execute('update_task', 'other', {'task_id': t['id'], 'patch': {'status': 'done'}}, 'foreign')['ok']
    assert not call('update_task', {'task_id': t['id'], 'patch': {'id': 'x'}})['ok']
    assert not call('list_tasks', {'project_id': 'other'})['ok']
    assert call('list_tasks')['data'][0]['status'] == 'todo'


def test_adjacent_schedule_and_version(isolated, task_data):
    a, b = create(task_data), create({**task_data, 'title': '第二项'}, 'b')
    assert available()['ok']
    blocks = [block(a), block(b, '2030-10-24T20:00:00+08:00', '2030-10-24T21:00:00+08:00')]
    assert call('check_schedule', {'proposed_blocks': blocks})['data']['feasible']
    result = call('save_schedule', {'blocks': blocks, 'expected_version': 0}, 'save')
    assert result['ok'] and result['data']['version'] == 1
    assert not call('save_schedule', {'blocks': blocks, 'expected_version': 0}, 'old')['ok']
    assert len(call('get_plan')['data']['schedule']) == 2


@pytest.mark.parametrize('start,end', [
    ('2030-10-24T20:00:00+08:00', '2030-10-24T19:00:00+08:00'),
    ('2030-10-24T18:00:00+08:00', '2030-10-24T19:00:00+08:00'),
    ('2030-10-26T19:00:00+08:00', '2030-10-26T20:00:00+08:00'),
])
def test_schedule_bounds(isolated, task_data, start, end):
    t = create(task_data); available()
    result = call('save_schedule', {'blocks': [block(t, start, end)], 'expected_version': 0})
    assert not result['ok']
    assert call('get_plan')['data']['schedule'] == []


def test_overlap_and_gap(isolated, task_data):
    a, b = create(task_data), create({**task_data, 'title': '第二项'}, 'b')
    available()
    result = call('check_schedule', {'proposed_blocks': [block(a), block(b)]})
    assert not result['data']['feasible'] and result['data']['conflicts']
    gap = call('check_schedule', {'proposed_blocks': [block(a)]})['data']
    assert gap['missing_minutes_by_assignee'] == {'member_a': 60}


def test_capacity_reports_without_silent_save(isolated, task_data):
    create({**task_data, 'estimated_minutes': 120})
    available([{'start_at': '2030-10-24T19:00:00+08:00', 'end_at': '2030-10-24T20:00:00+08:00'}])
    result = call('propose_schedule')['data']
    assert not result['feasible'] and result['missing_minutes_by_assignee'] == {'member_a': 60}
    assert call('get_plan')['data']['schedule'] == []


def test_change_protection_and_done_removes_blocks(isolated, task_data):
    t = create(task_data); available()
    assert call('save_schedule', {'blocks': [block(t)], 'expected_version': 0})['ok']
    assert call('update_task', {'task_id': t['id'], 'patch': {'estimated_minutes': 120}}, 'bad')['error']['code'] == 'REPLAN_REQUIRED'
    assert not call('set_preferences', {'assignee_id': 'member_a', 'windows': []}, 'badprefs')['ok']
    assert call('update_task', {'task_id': t['id'], 'patch': {'status': 'done'}}, 'done')['ok']
    plan = call('get_plan')['data']
    assert plan['schedule'] == [] and plan['tasks'][0]['status'] == 'done' and plan['version'] == 2


def test_export_escapes_and_is_snapshot(isolated, task_data):
    t = create({**task_data, 'title': '中文|任务\n第二行'})
    exported = call('export_plan')['data']
    assert exported['url'].startswith('/api/downloads/file_')
    call('update_task', {'task_id': t['id'], 'patch': {'title': '已改名'}}, 'update')
    with connect() as db:
        content = db.execute('SELECT content FROM downloads WHERE id=?', (exported['file_id'],)).fetchone()[0]
    assert '中文\\|任务 第二行' in content and '尚未保存日程' in content and '已改名' not in content


def test_adjacent_availability_is_continuous(isolated, task_data):
    t = create({**task_data, 'estimated_minutes': 120})
    assert available([{'start_at': '2030-10-24T19:00:00+08:00', 'end_at': '2030-10-24T20:00:00+08:00'},
        {'start_at': '2030-10-24T20:00:00+08:00', 'end_at': '2030-10-24T21:00:00+08:00'}])['ok']
    assert call('check_schedule', {'proposed_blocks': [block(t, end='2030-10-24T21:00:00+08:00')]})['data']['feasible']


def test_members_add_rename_remove_survives_restart(isolated):
    added = call('manage_member', {'operation': 'add', 'name': '小林'}, 'add')
    assert added['ok'] and len(added['data']['members']) == 6
    assert added == call('manage_member', {'operation': 'add', 'name': '小林'}, 'add')
    mid = added['data']['member_id']
    assert call('manage_member', {'operation': 'rename', 'member_id': mid, 'name': '小陈'}, 'rename')['ok']
    assert call('manage_member', {'operation': 'remove', 'member_id': 'member_e'}, 'remove')['ok']
    initialize()
    members = call('get_plan')['data']['members']
    assert len(members) == 5 and not any(m['id'] == 'member_e' for m in members)
    assert any(m['name'] == '小陈' for m in members)


def test_members_tasks_transfer_and_last_member(isolated, task_data):
    task = create(task_data)
    denied = call('manage_member', {'operation': 'remove', 'member_id': 'member_a'}, 'denied')
    assert denied['error']['code'] == 'MEMBER_HAS_TASKS'
    assert call('update_task', {'task_id': task['id'], 'patch': {'assignee_id': 'member_b'}}, 'transfer')['ok']
    for letter in 'acde':
        assert call('manage_member', {'operation': 'remove', 'member_id': f'member_{letter}'}, f'remove_{letter}')['ok']
    initialize()
    assert len(call('get_plan')['data']['members']) == 1
    assert call('manage_member', {'operation': 'remove', 'member_id': 'member_b'}, 'last')['error']['code'] == 'LAST_MEMBER'


@pytest.mark.parametrize('args', [
    {'operation': 'add', 'name': '  '}, {'operation': 'add', 'name': '成员 A · 智能体与集成'},
    {'operation': 'rename', 'member_id': 'foreign', 'name': '小林'},
    {'operation': 'remove', 'member_id': 'foreign'},
])
def test_invalid_member_updates_have_no_side_effects(isolated, args):
    assert not call('manage_member', args)['ok']
    assert len(call('get_plan')['data']['members']) == 5
