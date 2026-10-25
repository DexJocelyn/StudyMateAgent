"""B：唯一的 SQLite schema 和事务入口；HTTP、MCP、记忆共用此库。"""
import json
import sqlite3
from contextlib import contextmanager
from uuid import uuid4
from backend.config import data_dir, now, PROJECT_ID


def uid(prefix):
    return f'{prefix}_{uuid4().hex[:16]}'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(data_dir() / 'studymate.sqlite3', timeout=20, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=20000')
    return db


@contextmanager
def transaction():
    with connect() as db:
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def initialize():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS members (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            assignee_id TEXT NOT NULL REFERENCES members(id), deadline TEXT NOT NULL,
            estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes>0),
            status TEXT NOT NULL CHECK(status IN ('todo','doing','paused','done')));
        CREATE TABLE IF NOT EXISTS preferences (
            project_id TEXT NOT NULL REFERENCES projects(id), assignee_id TEXT NOT NULL REFERENCES members(id),
            windows TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(project_id,assignee_id));
        CREATE TABLE IF NOT EXISTS schedule_blocks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            task_id TEXT NOT NULL REFERENCES tasks(id), assignee_id TEXT NOT NULL REFERENCES members(id),
            start_at TEXT NOT NULL, end_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            history TEXT NOT NULL DEFAULT '[]', focus_id TEXT, draft TEXT,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (
            session_id TEXT NOT NULL REFERENCES sessions(id), request_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, state TEXT NOT NULL, response TEXT,
            PRIMARY KEY(session_id,request_id));
        CREATE TABLE IF NOT EXISTS mutations (
            project_id TEXT NOT NULL REFERENCES projects(id), mutation_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(project_id,mutation_id));
        CREATE TABLE IF NOT EXISTS downloads (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            filename TEXT NOT NULL, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_creations (
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id));
        CREATE TABLE IF NOT EXISTS web_search_calls (
            id TEXT PRIMARY KEY, month TEXT NOT NULL, created_at TEXT NOT NULL,
            query_hash TEXT NOT NULL, result TEXT);
        CREATE INDEX IF NOT EXISTS web_search_cache ON web_search_calls(query_hash,created_at);
        CREATE INDEX IF NOT EXISTS tasks_by_project ON tasks(project_id);
        CREATE INDEX IF NOT EXISTS blocks_by_project ON schedule_blocks(project_id);
        ''')
        migrate_task_status(db)
        db.execute('BEGIN IMMEDIATE')
        if 'member_id' not in {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}:
            db.execute('ALTER TABLE sessions ADD COLUMN member_id TEXT')
        created = db.execute('INSERT OR IGNORE INTO projects(id,name) VALUES(?,?)', (PROJECT_ID, '软件工程 · 课伴项目')).rowcount
        # 仅在新项目初始化时提供示例成员；重启或启动 MCP 子进程不能把用户移除的成员加回来。
        if created:
            for letter, role in zip('abcde', ['智能体与集成', '工具与数据库', '前端与交互', '记忆与语音', '测试与交付']):
                db.execute('INSERT INTO members VALUES(?,?,?)',
                           (f'member_{letter}', PROJECT_ID, f'成员 {letter.upper()} · {role}'))
        db.commit()


def migrate_task_status(db):
    """扩展旧库 CHECK 约束；保留任务 ID 和日程外键，事务内整体替换。"""
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
    if "'paused'" in schema:
        return
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        # 等待另一进程的迁移锁后，重新检查，避免 HTTP/MCP 同时初始化重复迁移。
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
        if "'paused'" not in schema:
            db.execute('''CREATE TABLE tasks_with_pause (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                assignee_id TEXT NOT NULL REFERENCES members(id), deadline TEXT NOT NULL,
                estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes>0),
                status TEXT NOT NULL CHECK(status IN ('todo','doing','paused','done')))''')
            db.execute('INSERT INTO tasks_with_pause SELECT * FROM tasks')
            db.execute('DROP TABLE tasks')
            db.execute('ALTER TABLE tasks_with_pause RENAME TO tasks')
            db.execute('CREATE INDEX tasks_by_project ON tasks(project_id)')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise sqlite3.IntegrityError('任务状态迁移外键校验失败')
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params).fetchall()]
