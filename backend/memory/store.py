"""D：持久化完整回合与会话焦点。请求登记在调用模型之前提交。"""
import hashlib
import json
from backend.storage.db import transaction, uid, dumps
from backend.config import now
from backend.services.tools import fail, project, member


def create_session(project_id, member_id=None):
    with transaction() as db:
        project(db, project_id)
        if member_id is not None:
            member(db, project_id, member_id)
        session_id = uid('session')
        db.execute('INSERT INTO sessions(id,project_id,member_id,created_at) VALUES(?,?,?,?)', (session_id, project_id, member_id, now().isoformat()))
    return {'session_id': session_id, 'project_id': project_id, 'member_id': member_id}


def latest_session(project_id, member_id=None):
    """新设备没有浏览器会话ID时，恢复该项目/成员最近有内容的对话。"""
    with transaction() as db:
        project(db, project_id)
        if member_id is not None:
            member(db, project_id, member_id)
        row = db.execute('''SELECT s.id FROM sessions s
            WHERE s.project_id=? AND s.member_id IS ?
            ORDER BY (s.history != '[]') DESC,
                COALESCE((SELECT MAX(r.rowid) FROM requests r
                    WHERE r.session_id=s.id AND r.state='finished'),0) DESC,
                s.rowid DESC LIMIT 1''', (project_id, member_id)).fetchone()
    return display_context(project_id, row['id'], member_id) if row else {'session_id': None}


def load_context(project_id, session_id, member_id=None):
    with transaction() as db:
        row = db.execute('SELECT * FROM sessions WHERE id=? AND project_id=?', (session_id, project_id)).fetchone()
        if row is None:
            fail('SESSION_NOT_FOUND', '当前项目中没有这个会话，请新建会话')
        if row['member_id'] != member_id:
            fail('SESSION_MEMBER_MISMATCH', '这个对话属于其他成员，请切换成员或新建对话')
        speaker = db.execute('SELECT name FROM members WHERE project_id=? AND id=?', (project_id, member_id)).fetchone() if member_id else None
        return {**dict(row), 'member_name': speaker['name'] if speaker else None,
                'history': json.loads(row['history']), 'draft': json.loads(row['draft']) if row['draft'] else None}


def display_context(project_id, session_id, member_id=None):
    ctx = load_context(project_id, session_id, member_id)
    visible = []
    for turn in ctx['history']:
        for msg in turn['messages']:
            if msg['role'] in ('user', 'assistant') and msg.get('content') and not msg.get('tool_calls'):
                visible.append({'role': msg['role'], 'content': msg['content'],
                                'member_id': turn.get('member_id'), 'member_name': turn.get('member_name')})
        if turn.get('events'):
            visible.append({'role': 'events', 'events': turn['events']})
        if turn.get('download'):
            visible.append({'role': 'download', 'download': turn['download']})
        if turn.get('sources'):
            visible.append({'role': 'sources', 'sources': turn['sources']})
    return {'session_id': session_id, 'project_id': project_id, 'member_id': ctx['member_id'],
            'member_name': ctx['member_name'], 'history': visible, 'draft': ctx['draft'], 'focus_id': ctx['focus_id']}


def register_request(project_id, session_id, request_id, message, action, member_id=None):
    load_context(project_id, session_id, member_id)
    fingerprint = hashlib.sha256(dumps([message, action]).encode()).hexdigest()
    with transaction() as db:
        row = db.execute('SELECT * FROM requests WHERE session_id=? AND request_id=?', (session_id, request_id)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                fail('REQUEST_CONFLICT', '同一 request_id 不能发送不同内容')
            if row['state'] != 'finished':
                fail('REQUEST_PENDING', '该请求正在处理或曾中断，请查询实际任务状态，不要重复执行')
            return json.loads(row['response'])
        db.execute('INSERT INTO requests VALUES(?,?,?,?,NULL)', (session_id, request_id, fingerprint, 'processing'))
    return None


def save_state(project_id, session_id, draft=None, focus_id=None, clear_draft=False, clear_focus=False):
    with transaction() as db:
        if clear_draft:
            db.execute('UPDATE sessions SET draft=NULL WHERE id=? AND project_id=?', (session_id, project_id))
        elif draft is not None:
            db.execute('UPDATE sessions SET draft=? WHERE id=? AND project_id=?', (dumps(draft), session_id, project_id))
        if clear_focus:
            db.execute('UPDATE sessions SET focus_id=NULL WHERE id=? AND project_id=?', (session_id, project_id))
        elif focus_id:
            db.execute('UPDATE sessions SET focus_id=? WHERE id=? AND project_id=?', (focus_id, session_id, project_id))


def finish_request(project_id, session_id, request_id, messages, response):
    with transaction() as db:
        row = db.execute('SELECT history,member_id FROM sessions WHERE id=? AND project_id=?', (session_id, project_id)).fetchone()
        speaker = db.execute('SELECT name FROM members WHERE id=? AND project_id=?', (row['member_id'], project_id)).fetchone()
        history = json.loads(row['history'])
        history.append({'messages': messages, 'events': response['tool_events'], 'download': response['download'],
                        'sources': response.get('sources', []),
                        'member_id': row['member_id'], 'member_name': speaker['name'] if speaker else None})
        # 保留最近 24 个完整回合，不截断工具调用与结果的协议配对。
        history = history[-24:]
        db.execute('UPDATE sessions SET history=? WHERE id=?', (dumps(history), session_id))
        db.execute("UPDATE requests SET state='finished',response=? WHERE session_id=? AND request_id=?", (dumps(response), session_id, request_id))
