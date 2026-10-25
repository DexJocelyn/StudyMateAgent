"""E：只使用一致快照生成 Markdown，不直接猜测数据库结构。"""
def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def export_markdown(snapshot):
    members = {m['id']: m['name'] for m in snapshot['members']}
    statuses = {'todo': '待开始', 'doing': '进行中', 'paused': '已暂停', 'done': '已完成'}
    lines = ['# 课伴 StudyMate · 项目计划', '', f"项目：{cell(snapshot['project']['name'])}", '', f"导出时间：{snapshot['exported_at']}",
             f"日程版本：{snapshot['version']}", '', '## 任务', '',
             '| 任务 | 负责人 | 截止时间 | 预计分钟 | 状态 |', '| --- | --- | --- | ---: | --- |']
    for t in snapshot['tasks']:
        lines.append('| ' + ' | '.join(map(cell, [t['title'], members[t['assignee_id']],
                     t['deadline'], t['estimated_minutes'], statuses[t['status']]])) + ' |')
    lines += ['', '## 日程', '', '| 任务 | 负责人 | 开始 | 结束 |', '| --- | --- | --- | --- |']
    titles = {t['id']: t['title'] for t in snapshot['tasks']}
    for b in snapshot['schedule']:
        lines.append('| ' + ' | '.join(map(cell, [titles[b['task_id']], members[b['assignee_id']],
                     b['start_at'], b['end_at']])) + ' |')
    if not snapshot['schedule']:
        lines += ['', '尚未保存日程。']
    lines += ['', '## 成员可用时间', '']
    for p in snapshot['preferences']:
        for w in p['windows']:
            lines.append(f"- {members[p['assignee_id']]}：{w['start_at']} ～ {w['end_at']}")
    return '\n'.join(lines) + '\n'
