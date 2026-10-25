"""面向用户的执行状态；仅来自实际处理阶段，不包含模型内部推理。"""
TOOL_LABELS = {
    'get_weather': '查询城市天气',
    'web_search': '联网搜索公开网页',
    'get_plan': '查询项目状态', 'list_tasks': '查询任务', 'create_tasks': '保存任务',
    'update_task': '更新任务', 'get_preferences': '读取空闲时间',
    'set_preferences': '保存空闲时间', 'propose_schedule': '计算候选日程',
    'check_schedule': '检查日程', 'save_schedule': '保存日程',
    'export_plan': '生成下载文件', 'manage_member': '更新项目成员',
}


def emit(callback, kind, label, **fields):
    if callback:
        callback({'type': 'progress', 'kind': kind, 'label': label, **fields})
