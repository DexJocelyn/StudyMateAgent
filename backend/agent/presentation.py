"""Trusted clock/deadline facts and readable labels for model replies."""
import re
from datetime import datetime
from backend.config import TZ
from backend.services.models import timestamp
from backend.agent.progress import TOOL_LABELS


def project_display_names(project_id):
    """Presentation-only lookup, strictly scoped to the selected project."""
    from backend.storage.db import connect
    with connect() as db:
        members = db.execute('SELECT id,name FROM members WHERE project_id=?', (project_id,)).fetchall()
        tasks = db.execute('SELECT id,title FROM tasks WHERE project_id=?', (project_id,)).fetchall()
    return {row[0]: row[1] for row in [*members, *tasks]}


def wants_raw_details(question):
    if re.search(r'可读性|看不懂|不直观|太乱', question):
        return False
    question = re.sub(r'(?:不要|不用|无需|不必|别|禁止)[^，。！？\n]{0,20}', '', question)
    if re.search(r'\b(?:MCP|API|JSON|ISO)\b', question):
        return True
    return bool(re.search(
        r'(?:显示|列出|查看|给我|告诉我|提供|保留|输出)[^，。！？\n]{0,14}(?:ID|编号|标识|原始字段|工具名)|'
        r'(?:ID|编号|标识)(?:是什么|的含义|什么意思)|'
        r'(?:原始|完整)(?:的)?(?:JSON|响应|工具结果|接口数据)|JSON格式|ISO格式|代码示例|接口示例', question, re.I))


def clock_facts(current):
    local = current.astimezone(TZ)
    return {'now': local.isoformat(), 'date': local.date().isoformat(),
            'weekday': '周' + '一二三四五六日'[local.weekday()], 'timezone': 'Asia/Shanghai'}


def deadline_facts(tasks, current):
    facts = []
    for task in tasks:
        due = timestamp(task['deadline']).astimezone(TZ)
        seconds = (due - current).total_seconds()
        minutes = int(abs(seconds) // 60)
        days, hours = minutes // 1440, minutes % 1440 // 60
        duration = (f'{days}天' if days else '') + (f'{hours}小时' if hours else '')
        duration += f'{minutes % 60}分钟' if minutes % 60 else ''
        duration = duration or '不足1分钟'
        completed = task['status'] == 'done'
        relation = 'completed' if completed else 'overdue' if seconds < 0 else 'due_now' if seconds == 0 else 'upcoming'
        label = '已完成' if completed else f'已逾期{duration}' if seconds < 0 else '已到截止时间' if seconds == 0 else f'距截止还有{duration}'
        facts.append({'task_id': task['id'], 'title': task['title'],
                      'deadline': due.isoformat(), 'deadline_weekday': '周' + '一二三四五六日'[due.weekday()],
                      'relation': relation, 'time_label': label})
    return facts


def readable_reply(text, names, question):
    # IDs stay unchanged in tool calls and records; users may explicitly request them.
    if wants_raw_details(question):
        return text
    def replace(match):
        identifier, shortened = match['identifier'], match['shortened']
        candidates = [key for key in names if key.startswith(identifier)] if shortened else [identifier] if identifier in names else []
        if len(candidates) != 1:
            return match[0]  # Never guess an ambiguous prefix.
        return '「' + names[candidates[0]] + '」'
    def format_prose(prose):
        prose = re.sub(r'(?P<tick>`?)(?P<identifier>\b(?:task|member)_[A-Za-z0-9]+)(?P<shortened>\.{3}|…)?(?P=tick)', replace, prose)
        # Normalize only unambiguous, valid timezone-bearing ISO timestamps.
        def date_label(match):
            try:
                value = datetime.fromisoformat(match[1].replace('Z', '+00:00')).astimezone(TZ)
            except ValueError:
                return match[0]
            time = value.strftime('%H:%M:%S' if value.second else '%H:%M')
            return f'{value.year}年{value.month}月{value.day}日 {time}（北京时间）'
        prose = re.sub(r'`?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:\d{2}|Z))`?', date_label, prose)
        for pattern, label in [(r'`?conflicts\s*:\s*\[\s*\]`?', '无时间冲突'),
                               (r'`?missing_minutes_by_assignee\s*:\s*\{\s*\}`?', '各成员的任务均可安排'),
                               (r'`?missing_minutes_by_task\s*:\s*\{\s*\}`?', '所有任务均可安排')]:
            prose = re.sub(pattern, label, prose)
        labels = {**TOOL_LABELS, 'draft_schedule':'生成日程草案', 'draft_tasks':'生成分工草案',
                  'windows':'空闲时段', 'preferences':'空闲时间设置', 'task_id':'任务', 'assignee_id':'负责人',
                  'missing_minutes_by_assignee':'各成员未排下的时长', 'missing_minutes_by_task':'各任务未排下的时长',
                  'conflicts':'时间冲突', 'MCP':'工具调用'}
        pattern = r'`?(?<![A-Za-z0-9_])(' + '|'.join(map(re.escape, sorted(labels, key=len, reverse=True))) + r')(?![A-Za-z0-9_])`?'
        prose = re.sub(pattern, lambda m: labels[m[1]], prose)
        prose = re.sub(r'(?:为)?空数组\s*`?\[\s*\]`?', '未填写', prose)
        # Once IDs become titles, remove the misleading "ID" prefix as well.
        prose = re.sub(r'\bID\s*[:：]?\s*(?=「)', '', prose)
        prose = re.sub(r'已通过\s*工具调用\s*保存', '已保存', prose)
        prose = re.sub(r'（读取空闲时间\s*确认存在非空\s*空闲时段）', '', prose)
        prose = prose.replace('无时间冲突（无时间冲突）', '无时间冲突')
        return prose
    # Preserve links and fenced code verbatim; these are not display labels.
    return ''.join(part if re.match(r'https?://|```', part) else format_prose(part)
                   for part in re.split(r'(```[\s\S]*?```|https?://[^\s<>`]+)', text))
