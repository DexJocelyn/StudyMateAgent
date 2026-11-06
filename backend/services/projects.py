"""本地项目空间的创建与枚举；项目内操作继续通过可信 project_id 范围执行。"""
import hashlib
from backend.storage.db import transaction, uid, rows, dumps
from backend.services.tools import fail


PROJECTS_SQL='''SELECT p.id,p.name,p.version,COALESCE(a.archived,0) AS archived,
    (SELECT COUNT(*) FROM members m WHERE m.project_id=p.id) AS member_count,
    (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) AS task_count
    FROM projects p LEFT JOIN project_archives a ON a.project_id=p.id'''


def list_projects(include_archived=False):
    with transaction() as db:
        return [{**p,'archived':bool(p['archived'])} for p in rows(db,PROJECTS_SQL+('' if include_archived else ' WHERE COALESCE(a.archived,0)=0')+' ORDER BY p.rowid')]


def update_project(project_id,name=None,archived=None):
    if (name is None)==(archived is None):fail('INVALID_ARGUMENTS','每次请选择重命名或归档操作。')
    if name is not None:
        name=name.strip()
        if not 1<=len(name)<=80:fail('INVALID_PROJECT_NAME','项目名称需为1～80个字符。')
    if archived is not None and type(archived) is not bool:fail('INVALID_ARGUMENTS','归档状态不合法。')
    with transaction() as db:
        current=db.execute(PROJECTS_SQL+' WHERE p.id=?',(project_id,)).fetchone()
        if current is None:fail('NOT_FOUND','项目不存在。')
        if name is not None:db.execute('UPDATE projects SET name=? WHERE id=?',(name,project_id))
        else:
            if archived and not current['archived']:
                count=db.execute('SELECT COUNT(*) FROM projects p LEFT JOIN project_archives a ON a.project_id=p.id WHERE COALESCE(a.archived,0)=0').fetchone()[0]
                if count<=1:fail('LAST_ACTIVE_PROJECT','请至少保留一个在用项目。可以先新建或恢复其他项目，再归档这个项目。')
            db.execute('INSERT INTO project_archives(project_id,archived) VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET archived=excluded.archived',(project_id,int(archived)))
        value=dict(db.execute(PROJECTS_SQL+' WHERE p.id=?',(project_id,)).fetchone())
        value['archived']=bool(value['archived'])
        return value


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
