// Project IDs travel in links so switching modules does not depend on a device's last selection.
export function selectProject(projects, search, stored) {
  const requested = new URLSearchParams(search).get('project_id');
  if (requested) {
    if (!projects.some(p => p.id === requested)) throw new Error('项目可能已归档或不存在。可在“管理项目 → 已归档”中恢复，或重新选择项目。');
    return requested;
  }
  return projects.some(p => p.id === stored) ? stored : projects[0]?.id;
}
export function projectLink(path, projectId, options = {}) {
  const params = new URLSearchParams({project_id: projectId, ...options});
  return `${path}?${params}`;
}
export function deliveryStatus(summary) {
  if (!summary.instructions_saved) return '先保存老师要求，分工和交付检查共用这一份要求。';
  if (!summary.latest_run_id) return `已保存要求 · ${summary.material_count}份材料 · 提交前可进行交付检查。`;
  if (summary.stale) return '要求或材料已变化，交付检查结果已过期，需要重新检查。';
  return `本轮检查${summary.item_count}项 · 待复核${summary.unreviewed}项 · 人工标记需补充${summary.needs_work}项 · 本轮补充任务待完成${summary.followups_open}项`;
}
