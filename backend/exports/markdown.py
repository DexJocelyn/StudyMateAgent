"""E：只使用一致快照生成 Markdown，不直接猜测数据库结构。"""
from collections import defaultdict
import re
from backend.exports.formatting import local_time, date_time


def cell(value):
    value = str(value).replace('\\', '\\\\').replace('\n', ' ').replace('\r', ' ')
    return re.sub(r'([`*_{}\[\]<>#!|])', r'\\\1', value)


def duration(minutes):
    hours, minutes = divmod(minutes, 60)
    return (f'{hours}小时' if hours else '') + (f'{minutes}分钟' if minutes else '') or '0分钟'


def time_range(start, end):
    start, end = local_time(start), local_time(end)
    finish = end.strftime('%H:%M') if start.date() == end.date() else f'{end.year}年{end.month}月{end.day}日 {end:%H:%M}'
    return f'{start:%H:%M}—{finish}'


def export_markdown(snapshot):
    members = {m['id']: m['name'] for m in snapshot['members']}
    statuses = {'todo': '待开始', 'doing': '进行中', 'paused': '已暂停', 'done': '已完成'}
    tasks = sorted(snapshot['tasks'], key=lambda t: (local_time(t['deadline']), members[t['assignee_id']], t['title']))
    lines = [f"# {cell(snapshot['project']['name'])} · 任务计划", '',
             f"> 导出时间：{date_time(snapshot['exported_at'])} · 所有时间均为北京时间", '', '## 计划概览', '',
             f"- 共 **{len(members)} 位成员、{len(tasks)} 项任务**，预计总用时 **{duration(sum(t['estimated_minutes'] for t in tasks))}**。",
             '- ' + ' · '.join(f"{name} {sum(t['status'] == key for t in tasks)} 项" for key, name in statuses.items()) + '。',
             '', '## 时间安排', '']
    by_id = {t['id']: t for t in tasks}
    days = defaultdict(list)
    for block in sorted(snapshot['schedule'], key=lambda b: (local_time(b['start_at']), members[b['assignee_id']])):
        days[local_time(block['start_at']).date()].append(block)
    for day, blocks in days.items():
        lines += [f"### {day.year}年{day.month}月{day.day}日（周{'一二三四五六日'[day.weekday()]}）", '',
                  '| 时间 | 负责人 | 任务 | 本次用时 |', '| --- | --- | --- | --- |']
        for b in blocks:
            minutes = int((local_time(b['end_at']) - local_time(b['start_at'])).total_seconds() // 60)
            lines.append('| ' + ' | '.join(map(cell, [time_range(b['start_at'], b['end_at']), members[b['assignee_id']],
                         by_id[b['task_id']]['title'], duration(minutes)])) + ' |')
        lines.append('')
    if not snapshot['schedule']:
        lines += ['尚未保存日程。填写成员空闲时间后，生成草案并确认保存，再重新导出。', '']
    lines += ['## 任务清单', '']
    if tasks:
        lines += ['| 负责人 | 任务 | 预计用时 | 截止时间 | 状态 |', '| --- | --- | --- | --- | --- |']
        for t in tasks:
            lines.append('| ' + ' | '.join(map(cell, [members[t['assignee_id']], t['title'], duration(t['estimated_minutes']),
                         date_time(t['deadline']), statuses[t['status']]])) + ' |')
    else:
        lines.append('尚未创建任务。先确认分工并保存任务，再安排时间。')
    descriptions = [t for t in tasks if t.get('description', '').strip()]
    if descriptions:
        lines += ['', '## 任务说明', '']
        for t in descriptions:
            lines += [f"### {cell(t['title'])} · {cell(members[t['assignee_id']])}", '']
            lines += [cell(line) + '  ' if line.strip() else '' for line in t['description'].splitlines()]
            lines.append('')
    lines += ['', '## 成员空闲时间', '']
    preferences = {p['assignee_id']: p['windows'] for p in snapshot['preferences']}
    for mid, name in members.items():
        lines += [f'### {cell(name)}', '']
        windows = sorted(preferences.get(mid, []), key=lambda w: local_time(w['start_at']))
        if not windows:
            lines += ['尚未填写。', '']
            continue
        for w in windows:
            start = local_time(w['start_at'])
            lines.append(f'- {start.year}年{start.month}月{start.day}日（周{"一二三四五六日"[start.weekday()]}） {time_range(w["start_at"], w["end_at"])}')
        lines.append('')
    lines += ['---', '', f"由课伴 StudyMate 导出 · 已保存日程版本 {snapshot['version']}。本文件保留导出时的计划，后续变更请重新导出。"]
    return '\n'.join(lines) + '\n'
