from backend.exports.markdown import export_markdown


def test_readable_export_groups_local_dates_without_losing_cross_midnight_times():
    snapshot = {'project':{'name':'课程展示'},'members':[{'id':'member_x','name':'小陈'},{'id':'member_y','name':'小肖'}],
        'exported_at':'2026-11-03T14:44:16.123456Z','version':1,
        'tasks':[{'id':'task_x','title':'整理需求','assignee_id':'member_x','deadline':'2026-11-06T12:00:00Z',
                  'estimated_minutes':90,'status':'todo','description':'保留真实任务说明。'}],
        'schedule':[{'task_id':'task_x','assignee_id':'member_x','start_at':'2026-11-05T15:30:00Z','end_at':'2026-11-05T17:00:00Z'}],
        'preferences':[{'assignee_id':'member_x','windows':[{'start_at':'2026-11-05T15:00:00Z','end_at':'2026-11-05T18:00:00Z'}]}]}
    text = export_markdown(snapshot)
    assert '2026年11月3日 22:44' in text and '2026年11月6日 20:00' in text
    assert '### 2026年11月5日（周四）' in text and '23:30—2026年11月6日 01:00' in text
    assert '1小时30分钟' in text and '保留真实任务说明。' in text
    assert '### 小肖\n\n尚未填写。' in text
    assert 'task_x' not in text and 'member_x' not in text and 'T15:' not in text
    # Export does not change source times or identifiers.
    assert snapshot['schedule'][0]['start_at']=='2026-11-05T15:30:00Z'


def test_empty_export_has_useful_guidance_instead_of_empty_tables():
    text = export_markdown({'project':{'name':'练习'},'members':[], 'tasks':[], 'schedule':[],
        'preferences':[], 'version':0, 'exported_at':'2026-11-03T22:44:00+08:00'})
    assert '尚未创建任务' in text and '尚未保存日程' in text
    assert '| ---' not in text and '0分钟' in text
