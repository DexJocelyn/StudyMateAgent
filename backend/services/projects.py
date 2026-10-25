"""本地项目空间的创建与枚举；项目内操作继续通过可信 project_id 范围执行。"""
import hashlib
from backend.storage.db import transaction, uid, rows, dumps
from backend.services.tools import fail


def list_projects():
    with transaction() as db:
        return rows(db, '''SELECT p.id,p.name,p.version,
            (SELECT COUNT(*) FROM members m WHERE m.project_id=p.id) AS member_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) AS task_count
            FROM projects p ORDER BY p.rowid''')


def create_project(name, members, request_id):
    name = name.strip()
    names = [value.strip() for value in members]
    if not 1 <= len(name) <= 80:
        fail('INVALID_PROJECT_NAME', '项目名称需为 1～80 个字符')
    if not names or any(not 1 <= len(value) <= 60 for value in names):
        fail('INVALID_MEMBERS', '至少填写一位成员，每个姓名为 1～60 个字符')
    if len(set(names)) != len(names):
        fail('DUPLICATE_MEMBER', '同一项目的成员姓名不能重复')
    fingerprint = hashlib.sha256(dumps([name, names]).encode()).hexdigest()
    with transaction() as db:
        cached = db.execute('SELECT * FROM project_creations WHERE request_id=?', (request_id,)).fetchone()
        if cached:
            if cached['fingerprint'] != fingerprint:
                fail('REQUEST_CONFLICT', '同一创建请求不能用于不同项目内容')
            return dict(db.execute('SELECT * FROM projects WHERE id=?', (cached['project_id'],)).fetchone())
        project_id = uid('project')
        db.execute('INSERT INTO projects(id,name) VALUES(?,?)', (project_id, name))
        for member_name in names:
            db.execute('INSERT INTO members VALUES(?,?,?)', (uid('member'), project_id, member_name))
        db.execute('INSERT INTO project_creations VALUES(?,?,?)', (request_id, fingerprint, project_id))
        return dict(db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone())
