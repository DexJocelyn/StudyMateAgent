"""B：HTTP 和 MCP 共享的业务规则。所有写入与防重结果在同一事务提交。"""
import hashlib
import json
from datetime import timedelta
from backend.config import now
from backend.storage.db import transaction, rows, uid, dumps
from backend.services.models import TaskInput, Window, Block, timestamp
from backend.exports.markdown import export_markdown

WRITES = {'create_tasks', 'update_task', 'set_preferences', 'save_schedule', 'export_plan', 'manage_member'}


class BusinessError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise BusinessError(code, message)


def changed():
    return {'tasks': [], 'schedule': [], 'preferences': [], 'members': []}


def success(data, ids=None):
    return {'ok': True, 'data': data, 'error': None, 'changed_ids': ids or changed()}


def error_result(code, message):
    return {'ok': False, 'data': None, 'error': {'code': code, 'message': message, 'retryable': False},
            'changed_ids': changed()}


def project(db, project_id):
    value = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if value is None:
        fail('NOT_FOUND', '项目不存在')
    return dict(value)


def member(db, project_id, assignee_id):
    if not db.execute('SELECT 1 FROM members WHERE id=? AND project_id=?', (assignee_id, project_id)).fetchone():
        fail('INVALID_MEMBER', '负责人不属于当前项目')


def list_tasks(db, project_id, status=None, assignee_id=None):
    sql, values = 'SELECT * FROM tasks WHERE project_id=?', [project_id]
    if status is not None:
        if status not in ('todo', 'doing', 'paused', 'done'):
            fail('INVALID_STATUS', '状态必须为 todo/doing/paused/done')
        sql += ' AND status=?'
        values.append(status)
    if assignee_id is not None:
        member(db, project_id, assignee_id)
        sql += ' AND assignee_id=?'
        values.append(assignee_id)
    return rows(db, sql + ' ORDER BY deadline,assignee_id,id', values)


def get_preferences(db, project_id, assignee_id=None):
    if assignee_id:
        member(db, project_id, assignee_id)
    members = rows(db, 'SELECT id FROM members WHERE project_id=? ORDER BY id', (project_id,))
    result = []
    for m in members:
        if assignee_id and m['id'] != assignee_id:
            continue
        row = db.execute('SELECT windows FROM preferences WHERE project_id=? AND assignee_id=?',
                         (project_id, m['id'])).fetchone()
        result.append({'assignee_id': m['id'], 'windows': json.loads(row['windows']) if row else []})
    return result


def normalize_windows(windows):
    if len(windows) > 100:
        fail('LIMIT', '每位成员最多 100 段可用时间')
    result = [Window.model_validate(w).model_dump() for w in windows]
    result.sort(key=lambda w: w['start_at'])
    previous = None
    for w in result:
        start, end = timestamp(w['start_at']), timestamp(w['end_at'])
        if start >= end:
            fail('INVALID_WINDOW', '可用时间的开始必须早于结束')
        if previous and start < previous:
            fail('OVERLAP', '同一成员的可用时间不能重叠')
        previous = end
    # 合并相邻窗口，避免一个合法日程跨越两个相邻窗口时被错误拒绝。
    merged = []
    for w in result:
        if merged and merged[-1]['end_at'] == w['start_at']:
            merged[-1]['end_at'] = w['end_at']
        else:
            merged.append(w.copy())
    return merged


def snapshot(db, project_id):
    p = project(db, project_id)
    return {'project': p, 'members': rows(db, 'SELECT * FROM members WHERE project_id=? ORDER BY id', (project_id,)),
            'tasks': list_tasks(db, project_id),
            'schedule': rows(db, 'SELECT * FROM schedule_blocks WHERE project_id=? ORDER BY start_at,id', (project_id,)),
            'preferences': get_preferences(db, project_id), 'version': p['version'], 'exported_at': now().isoformat()}


def check_schedule(db, project_id, proposed_blocks, range_start=None, range_end=None):
    if len(proposed_blocks) > 500:
        fail('LIMIT', '日程块过多')
    blocks = [Block.model_validate(b).model_dump() for b in proposed_blocks]
    tasks = {t['id']: t for t in list_tasks(db, project_id)}
    preferences = {p['assignee_id']: p['windows'] for p in get_preferences(db, project_id)}
    start_limit = timestamp(range_start) if range_start else None
    end_limit = timestamp(range_end) if range_end else None
    if start_limit and end_limit and start_limit >= end_limit:
        fail('INVALID_RANGE', '排程范围开始必须早于结束')
    conflicts, coverage, timeline = [], {}, {}
    for b in blocks:
        task = tasks.get(b['task_id'])
        start, end = timestamp(b['start_at']), timestamp(b['end_at'])
        if not task or task['assignee_id'] != b['assignee_id']:
            conflicts.append('任务不存在、跨项目或负责人不匹配')
            continue
        if task['status'] == 'done':
            conflicts.append(f"已完成任务不应排入日程：{task['title']}")
        if start >= end:
            conflicts.append(f"开始时间必须早于结束：{task['title']}")
            continue
        if end > timestamp(task['deadline']):
            conflicts.append(f"超出截止时间：{task['title']}")
        if (start_limit and start < start_limit) or (end_limit and end > end_limit):
            conflicts.append(f"超出本次排程范围：{task['title']}")
        if not any(timestamp(w['start_at']) <= start and end <= timestamp(w['end_at'])
                   for w in preferences[b['assignee_id']]):
            conflicts.append(f"不在成员可用时间内：{task['title']}")
        timeline.setdefault(b['assignee_id'], []).append((start, end, task['title']))
        coverage[b['task_id']] = coverage.get(b['task_id'], 0) + int((end - start).total_seconds() // 60)
    for intervals in timeline.values():
        intervals.sort()
        furthest_end = None
        for start, end, title in intervals:
            if furthest_end and start < furthest_end:
                conflicts.append(f'同一成员日程重叠：{title}')
            furthest_end = max(furthest_end, end) if furthest_end else end
    missing, missing_tasks = {}, {}
    for t in tasks.values():
        if t['status'] == 'done':
            continue
        minutes = coverage.get(t['id'], 0)
        gap = max(0, t['estimated_minutes'] - minutes)
        if gap:
            missing[t['assignee_id']] = missing.get(t['assignee_id'], 0) + gap
            missing_tasks[t['id']] = gap
        if minutes > t['estimated_minutes']:
            conflicts.append(f"安排时长超过预计耗时：{t['title']}")
    return {'feasible': not conflicts and not missing, 'conflicts': conflicts,
            'missing_minutes_by_assignee': missing, 'missing_minutes_by_task': missing_tasks,
            'blocks': blocks, 'version': project(db, project_id)['version']}


def propose_schedule(db, project_id):
    """最早截止优先；使用已确认可用时段，允许任务拆段，不假设默认空闲时间。"""
    tasks = [t for t in list_tasks(db, project_id) if t['status'] != 'done']
    cursor = now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    windows = {p['assignee_id']: [[max(timestamp(w['start_at']), cursor), timestamp(w['end_at'])]
               for w in p['windows']] for p in get_preferences(db, project_id)}
    blocks = []
    for t in tasks:
        remaining = t['estimated_minutes']
        for w in windows[t['assignee_id']]:
            end = min(w[1], timestamp(t['deadline']))
            capacity = max(0, int((end - w[0]).total_seconds() // 60))
            take = min(remaining, capacity)
            if take:
                stop = w[0] + timedelta(minutes=take)
                blocks.append({'task_id': t['id'], 'assignee_id': t['assignee_id'],
                               'start_at': w[0].isoformat(), 'end_at': stop.isoformat()})
                w[0], remaining = stop, remaining - take
            if remaining == 0:
                break
    return check_schedule(db, project_id, blocks)


def _dispatch(db, name, project_id, args):
    project(db, project_id)
    ids = changed()
    if name == 'manage_member':
        if set(args) - {'operation', 'member_id', 'name'} or 'operation' not in args:
            fail('INVALID_ARGUMENTS', '成员操作参数不合法')
        operation = args['operation']
        member_id = args.get('member_id')
        name_value = args.get('name', '')
        if operation in ('add', 'rename'):
            if not isinstance(name_value, str) or not 1 <= len(name_value.strip()) <= 60:
                fail('INVALID_NAME', '成员姓名需为 1～60 个字符')
            name_value = name_value.strip()
            duplicate = db.execute('SELECT id FROM members WHERE project_id=? AND name=?', (project_id, name_value)).fetchone()
            if duplicate and duplicate['id'] != member_id:
                fail('DUPLICATE_MEMBER', '已有同名成员，请使用不同的姓名或称呼')
        if operation == 'add':
            if member_id:
                fail('INVALID_ARGUMENTS', '新增成员的 ID 由服务端生成')
            member_id = uid('member')
            db.execute('INSERT INTO members VALUES(?,?,?)', (member_id, project_id, name_value))
        elif operation == 'rename':
            member(db, project_id, member_id)
            db.execute('UPDATE members SET name=? WHERE id=? AND project_id=?', (name_value, member_id, project_id))
        elif operation == 'remove':
            member(db, project_id, member_id)
            if db.execute('SELECT COUNT(*) FROM members WHERE project_id=?', (project_id,)).fetchone()[0] <= 1:
                fail('LAST_MEMBER', '至少保留一位成员，支持单人使用')
            if db.execute('SELECT 1 FROM tasks WHERE project_id=? AND assignee_id=?', (project_id, member_id)).fetchone():
                fail('MEMBER_HAS_TASKS', '该成员仍有任务，请先把任务转交给其他成员后再移除')
            db.execute('DELETE FROM preferences WHERE project_id=? AND assignee_id=?', (project_id, member_id))
            db.execute('DELETE FROM members WHERE project_id=? AND id=?', (project_id, member_id))
            ids['preferences'] = [member_id]
        else:
            fail('INVALID_OPERATION', '成员操作只能为 add、rename 或 remove')
        ids['members'] = [member_id]
        return success({'operation': operation, 'member_id': member_id, 'members':
                       rows(db, 'SELECT * FROM members WHERE project_id=? ORDER BY id', (project_id,))}, ids)
    if name == 'list_tasks':
        return success(list_tasks(db, project_id, **args))
    if name == 'get_preferences':
        return success(get_preferences(db, project_id, **args))
    if name == 'get_plan':
        return success(snapshot(db, project_id))
    if name == 'create_tasks':
        if set(args) != {'tasks'} or not 1 <= len(args['tasks']) <= 50:
            fail('INVALID_TASKS', '一次应创建 1～50 个任务')
        tasks = [TaskInput.model_validate(t).model_dump() for t in args['tasks']]
        created = []
        for t in tasks:
            member(db, project_id, t['assignee_id'])
            t = {'id': uid('task'), 'project_id': project_id, **t}
            db.execute('INSERT INTO tasks VALUES(:id,:project_id,:title,:description,:assignee_id,:deadline,:estimated_minutes,:status)', t)
            created.append(t)
        ids['tasks'] = [t['id'] for t in created]
        return success(created, ids)
    if name == 'update_task':
        if set(args) != {'task_id', 'patch'}:
            fail('INVALID_ARGUMENTS', '需提供 task_id 和 patch')
        old = db.execute('SELECT * FROM tasks WHERE id=? AND project_id=?', (args['task_id'], project_id)).fetchone()
        if not old:
            fail('NOT_FOUND', '当前项目中没有该任务')
        old = dict(old)
        patch = args['patch']
        allowed = set(TaskInput.model_fields)
        if not patch or set(patch) - allowed:
            fail('INVALID_PATCH', '修改字段不合法或为空')
        data = TaskInput.model_validate({**{k: old[k] for k in allowed}, **patch}).model_dump()
        member(db, project_id, data['assignee_id'])
        has_blocks = db.execute('SELECT 1 FROM schedule_blocks WHERE task_id=?', (old['id'],)).fetchone()
        critical = ('assignee_id', 'deadline', 'estimated_minutes')
        if has_blocks and data['status'] != 'done' and any(data[k] != old[k] for k in critical):
            fail('REPLAN_REQUIRED', '此任务已有日程。请先清空日程，再修改负责人、截止或耗时并重新安排。')
        if has_blocks and data['status'] == 'done':
            ids['schedule'] = [b['id'] for b in rows(db, 'SELECT id FROM schedule_blocks WHERE task_id=?', (old['id'],))]
            db.execute('DELETE FROM schedule_blocks WHERE task_id=?', (old['id'],))
            db.execute('UPDATE projects SET version=version+1 WHERE id=?', (project_id,))
        db.execute('UPDATE tasks SET title=:title,description=:description,assignee_id=:assignee_id,deadline=:deadline,estimated_minutes=:estimated_minutes,status=:status WHERE id=:id AND project_id=:project_id',
                   {**data, 'id': old['id'], 'project_id': project_id})
        ids['tasks'] = [old['id']]
        return success({**old, **data}, ids)
    if name == 'set_preferences':
        if set(args) != {'assignee_id', 'windows'}:
            fail('INVALID_ARGUMENTS', '需提供 assignee_id 和 windows')
        member(db, project_id, args['assignee_id'])
        windows = normalize_windows(args['windows'])
        for b in rows(db, 'SELECT * FROM schedule_blocks WHERE project_id=? AND assignee_id=?', (project_id, args['assignee_id'])):
            if not any(w['start_at'] <= b['start_at'] and b['end_at'] <= w['end_at'] for w in windows):
                fail('REPLAN_REQUIRED', '新可用时间与已保存日程冲突，请先清空日程再调整。')
        db.execute('INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(project_id,assignee_id) DO UPDATE SET windows=excluded.windows',
                   (project_id, args['assignee_id'], dumps(windows)))
        ids['preferences'] = [args['assignee_id']]
        return success({'assignee_id': args['assignee_id'], 'windows': windows}, ids)
    if name == 'check_schedule':
        return success(check_schedule(db, project_id, **args))
    if name == 'propose_schedule':
        return success(propose_schedule(db, project_id))
    if name == 'save_schedule':
        if set(args) != {'blocks', 'expected_version'}:
            fail('INVALID_ARGUMENTS', '需提供 blocks 和 expected_version')
        current = project(db, project_id)['version']
        if type(args['expected_version']) is not int or args['expected_version'] != current:
            fail('VERSION_CONFLICT', '日程版本已变化，请重新生成草案')
        # 空列表明确表示清空；非空保存必须覆盖全部未完成任务。
        result = check_schedule(db, project_id, args['blocks'])
        if args['blocks'] and not result['feasible']:
            fail('INVALID_SCHEDULE', '日程校验失败：' + dumps(result))
        ids['schedule'] = [b['id'] for b in rows(db, 'SELECT id FROM schedule_blocks WHERE project_id=?', (project_id,))]
        db.execute('DELETE FROM schedule_blocks WHERE project_id=?', (project_id,))
        saved = []
        for b in result['blocks']:
            b = {'id': uid('block'), 'project_id': project_id, **b}
            db.execute('INSERT INTO schedule_blocks(id,project_id,task_id,assignee_id,start_at,end_at) VALUES(:id,:project_id,:task_id,:assignee_id,:start_at,:end_at)', b)
            saved.append(b)
        db.execute('UPDATE projects SET version=version+1 WHERE id=?', (project_id,))
        ids['schedule'] += [b['id'] for b in saved]
        return success({'blocks': saved, 'version': current + 1}, ids)
    if name == 'export_plan':
        if args.get('format', 'markdown') != 'markdown' or set(args) - {'format'}:
            fail('INVALID_FORMAT', '目前只支持 markdown')
        file_id, filename = uid('file'), f'StudyMate-{now():%Y%m%d-%H%M%S}.md'
        db.execute('INSERT INTO downloads VALUES(?,?,?,?)', (file_id, project_id, filename, export_markdown(snapshot(db, project_id))))
        return success({'file_id': file_id, 'filename': filename, 'url': f'/api/downloads/{file_id}'})
    fail('UNKNOWN_TOOL', f'未知工具：{name}')


def execute(name, project_id, args, mutation_id=None):
    try:
        if set(args) & {'project_id', 'mutation_id'}:
            fail('RESERVED_FIELD', '执行上下文不能由业务参数覆盖')
        fingerprint = hashlib.sha256(dumps([name, args]).encode()).hexdigest()
        with transaction() as db:
            if name in WRITES:
                if not mutation_id:
                    fail('MISSING_MUTATION_ID', '写操作缺少防重复标识')
                cached = db.execute('SELECT * FROM mutations WHERE project_id=? AND mutation_id=?', (project_id, mutation_id)).fetchone()
                if cached:
                    if cached['fingerprint'] != fingerprint:
                        fail('IDEMPOTENCY_CONFLICT', '相同 mutation_id 不能用于不同操作')
                    return json.loads(cached['result'])
            result = _dispatch(db, name, project_id, args)
            if name in WRITES:
                db.execute('INSERT INTO mutations VALUES(?,?,?,?)', (project_id, mutation_id, fingerprint, dumps(result)))
            return result
    except (ValueError, TypeError, KeyError) as exc:
        return error_result(getattr(exc, 'code', 'VALIDATION_ERROR'), str(exc))
