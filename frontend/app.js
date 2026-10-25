import {createSpeech} from './speech.js?v=20261022-voice';
import {renderMarkdown} from './markdown.js?v=20261024-format';

const $ = id => document.getElementById(id);
const state = {project: 'project_demo', projects: [], inputs: {}, projectCreateRequest: null, member: null, actorReady: false, session: null, plan: null, draft: null, tab: 'tasks', status: 'all', busy: false, voicePhase: 'idle', retry: null};
const voiceIsOpen = () => ['starting', 'listening', 'pausing', 'paused', 'stopping'].includes(state.voicePhase);
const statusNames = {todo: '待开始', doing: '进行中', paused: '已暂停', done: '已完成'};
const node = (tag, className, text) => { const e = document.createElement(tag); if (className) e.className = className; if (text != null) e.textContent = text; return e; };
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5500); }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || (typeof body.detail === 'string' ? body.detail : `请求失败 HTTP ${response.status}`));
  return body;
}
const post = (path, body) => api(path, {method: 'POST', body: JSON.stringify(body)});
async function streamChat(body, onProgress) {
  const response = await fetch('/api/chat/stream', {method: 'POST',
    headers: {'Content-Type': 'application/json', Accept: 'application/x-ndjson'}, body: JSON.stringify(body)});
  if (!response.ok) {
    const failure = await response.json();
    throw new Error(failure.error?.message || `请求失败 HTTP ${response.status}`);
  }
  if (!response.body) throw new Error('浏览器未能接收实时进度，请刷新后重试。');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '', result = null;
  const consume = line => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === 'progress') onProgress(event);
    else if (event.type === 'result') result = event.data;
    else if (event.type === 'error') throw new Error(event.error?.message || '未取得最终结果，请核对任务状态。');
  };
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) { buffer += decoder.decode(); break; }
      buffer += decoder.decode(value, {stream: true});
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) { consume(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1); }
    }
    if (buffer.trim()) consume(buffer);
  } finally { reader.releaseLock(); }
  if (!result) throw new Error('连接已断开，尚未收到最终结果。请先查看任务状态，再重试。');
  return result;
}
const dateTime = value => new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
const assigneeName = id => state.plan?.members.find(m => m.id === id)?.name || id;
const memberLabel = id => assigneeName(id).split(' · ')[0];

const actorKey = () => `${state.project}:${state.member || 'public'}`;
const sessionKey = () => state.member ? `studymate-session:${state.project}:${state.member}` : `studymate-session:${state.project}`;
const currentName = () => state.plan?.members.find(m => m.id === state.member)?.name || '未指定成员';
function renderIdentity() {
  if (!state.actorReady) {
    let saved; try { saved = localStorage.getItem(`studymate-member:${state.project}`); } catch {}
    state.member = saved === '' ? null : state.plan.members.some(m => m.id === saved) ? saved : state.plan.members.length === 1 ? state.plan.members[0].id : null;
    state.actorReady = true;
  } else if (state.member && !state.plan.members.some(m => m.id === state.member)) {
    state.member = null; state.session = null; state.draft = null; state.retry = null;
    $('chat-stream').replaceChildren(); welcome(); $('retry').hidden = true;
  }
  $('current-member').replaceChildren(new Option('未指定成员（旧公共对话）', ''), ...state.plan.members.map(m => new Option(m.name, m.id)));
  $('current-member').value = state.member || '';
  $('chat-title').textContent = state.member ? `对话 · ${memberLabel(state.member)}` : '公共对话历史';
  $('identity-note').textContent = state.member ? '按成员分别保存对话 · 本机视角切换，尚未接入账号登录' : '请选择你的姓名再聊天；旧公共对话仍可查看';
  $('message').placeholder = state.member ? `以 ${currentName()} 的身份与课伴对话` : '先在上方选择当前聊天成员';
  $('send').disabled = state.busy || !state.member || voiceIsOpen();
  $('new-session').disabled = state.busy || !state.member;
}
async function switchMember(memberId) {
  if (state.busy || (memberId && !state.plan.members.some(m => m.id === memberId))) return;
  state.inputs[actorKey()] = $('message').value;
  setBusy(true); speech.cancel();
  state.member = memberId || null; state.session = null; state.draft = null; state.retry = null;
  state.status = 'all';
  document.querySelectorAll('[data-status]').forEach(b => b.classList.toggle('selected', b.dataset.status === 'all'));
  $('retry').hidden = true; $('draft-container').replaceChildren();
  $('chat-stream').replaceChildren(node('p', 'muted', '正在切换成员对话…'));
  $('message').value = state.inputs[actorKey()] || '';
  $('member-filter').value = state.member || '';
  try { localStorage.setItem(`studymate-member:${state.project}`, state.member || ''); } catch {}
  renderIdentity(); renderBoard();
  try { await restoreSession(); } catch (e) { toast(e.message); }
  finally { setBusy(false); }
}
$('current-member').onchange = () => switchMember($('current-member').value);

function welcome() {
  const wrap = node('div', 'welcome');
  wrap.append(node('h3', 'welcome-title', '先输入你要完成的作业'),
    node('p', 'welcome-text', '1. 描述作业，生成任务草案\n2. 确认后保存到右侧任务列表\n3. 设置空闲时间，再安排日程'));
  $('chat-stream').append(wrap);
}
function addMessage(role, text, speaker = currentName()) {
  const wrap = node('article', `message ${role}`);
  const content = node('div', role === 'assistant' ? 'message-content markdown-body' : 'message-content');
  if (role === 'assistant') content.append(renderMarkdown(text));
  else content.textContent = text;
  const label = node('div', 'message-label', role === 'user' ? speaker : '课伴');
  if (role === 'assistant') {
    const speak = node('button', 'text-button', '朗读'); speak.title = '朗读这条回复'; speak.setAttribute('aria-label', '朗读这条回复');
    speak.onclick = () => speech.speak(content.innerText || text); label.append(speak);
  }
  wrap.append(label, content); $('chat-stream').append(wrap); scrollChat();
}
function addEvents(events) {
  if (!events?.length) return;
  const details = node('details', 'events');
  details.append(node('summary', '', events.every(e => e.status === 'success') ? '操作记录（展开查看）' : '操作未全部完成（展开查看）'));
  events.forEach(e => { const row = node('div', `event-row ${e.status === 'error' ? 'event-error' : ''}`); row.append(node('span', 'event-name', `${e.status === 'success' ? '✓' : '×'} ${e.name}`), node('div', '', `${e.transport || 'MCP'} · ${e.summary}`)); details.append(row); });
  $('chat-stream').append(details); scrollChat();
}
function addDownload(download) {
  if (!download || !/^\/api\/downloads\/file_[a-f0-9]+$/.test(download.url)) return;
  const a = node('a', 'download-link', `↓ ${download.filename}  · 下载 Markdown`);
  a.href = download.url + '?project_id=' + encodeURIComponent(state.project); a.download = download.filename;
  $('chat-stream').append(a); scrollChat();
}
function scrollChat() { $('chat-stream').scrollTop = $('chat-stream').scrollHeight; }
function addSources(sources) {
  if (!Array.isArray(sources) || !sources.length) return;
  const box = node('section', 'search-sources'); box.setAttribute('aria-label', '本轮联网来源');
  box.append(node('div', 'source-heading', '本轮联网来源'));
  for (const source of sources.slice(0, 15)) {
    let url; try { url = new URL(source.url); } catch { continue; }
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) continue;
    const row = node('div', 'source-row');
    const link = node('a', '', source.title || url.hostname);
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.referrerPolicy = 'no-referrer';
    row.append(link, node('small', '', url.hostname + (source.retrieved_at ? ` · 检索 ${dateTime(source.retrieved_at)}` : '')));
    box.append(row);
  }
  if (box.children.length > 1) { $('chat-stream').append(box); scrollChat(); }
}

function startProgress() {
  const card = node('details', 'progress-card'); card.open = true;
  card.setAttribute('aria-label', '课伴执行进度');
  const summary = node('summary'), indicator = node('span', 'progress-indicator');
  indicator.setAttribute('aria-hidden', 'true');
  const label = node('span', 'progress-label', '正在连接课伴…'), time = node('span', 'progress-time', '0秒');
  label.setAttribute('role', 'status');
  summary.append(indicator, label, time);
  const steps = node('ul', 'progress-steps'), rows = new Map();
  card.append(summary, steps); $('chat-stream').append(card); scrollChat();
  const started = performance.now(); let cached = false, finished = false;
  const timer = setInterval(() => { time.textContent = `${Math.floor((performance.now() - started) / 1000)}秒`; }, 1000);
  const update = event => {
    if (finished) return;
    if (event.kind === 'phase' || event.kind === 'cached') {
      label.textContent = event.label; cached ||= event.kind === 'cached';
    } else if (event.kind === 'tool' && event.step_id) {
      let row = rows.get(event.step_id);
      if (!row) { row = node('li'); rows.set(event.step_id, row); steps.append(row); }
      row.className = `progress-step ${event.status}`;
      const icon = {running: '·', success: '✓', error: '×', warning: '!', unknown: '?'}[event.status] || '·';
      row.textContent = `${icon} ${event.label}${event.summary ? ' · ' + event.summary : '…'}`;
      if (event.status === 'running') label.textContent = `正在${event.label}…`;
    }
    scrollChat();
  };
  const finish = result => {
    if (finished) return;
    clearInterval(timer);
    if (result) {
      result.tool_events.forEach((event, index) => {
        const stepId = `tool-${index}`;
        if (!rows.has(stepId) || rows.get(stepId).classList.contains('running')) {
          update({kind: 'tool', step_id: stepId, label: event.label || event.name, status: event.status, summary: event.summary});
        }
      });
    }
    finished = true;
    time.textContent = `${((performance.now() - started) / 1000).toFixed(1)}秒`;
    card.classList.add('finished'); card.open = !result || !result.ok;
    if (!result) { label.textContent = '结果尚未确认，请查看任务后重试'; card.classList.add('interrupted'); }
    else if (!result.ok) { label.textContent = '处理未全部完成，请查看提示'; card.classList.add('interrupted'); }
    else label.textContent = cached ? '已恢复之前的执行结果' : result.tool_events.length ? `处理完成 · ${result.tool_events.length}次工具执行` : '已收到回复';
  };
  return {update, finish};
}

async function restoreSession() {
  let saved;
  try { saved = localStorage.getItem(sessionKey()) || (!state.member && state.project === 'project_demo' ? localStorage.getItem('studymate-session') : null); } catch {}
  if (saved) {
    try { const ctx = await api(`/api/sessions/${encodeURIComponent(saved)}?project_id=${state.project}${state.member ? '&member_id=' + encodeURIComponent(state.member) : ''}`); state.session = saved; renderHistory(ctx); return; }
    catch { /* 已删除数据库或跨项目的旧 session，重新建立。 */ }
  }
  const latest = await api(`/api/sessions/latest?project_id=${encodeURIComponent(state.project)}${state.member ? '&member_id=' + encodeURIComponent(state.member) : ''}`);
  if (latest.session_id) {
    state.session = latest.session_id;
    try { localStorage.setItem(sessionKey(), state.session); } catch {}
    renderHistory(latest); return;
  }
  await newSession(false);
}
function renderHistory(ctx) {
  $('chat-stream').replaceChildren();
  if (!ctx.history.length) welcome();
  ctx.history.forEach(msg => { if (msg.role === 'events') addEvents(msg.events); else if (msg.role === 'download') addDownload(msg.download); else if (msg.role === 'sources') addSources(msg.sources); else addMessage(msg.role, msg.content, msg.member_name || '未指定成员'); });
  state.draft = ctx.draft; renderDraft();
}
async function newSession(notify = true) {
  if (voiceIsOpen()) speech.cancel();
  const result = await post('/api/sessions', {project_id: state.project, member_id: state.member});
  state.session = result.session_id; state.draft = null;
  try { localStorage.setItem(sessionKey(), state.session); } catch {}
  state.retry = null; $('retry').hidden = true;
  renderHistory({history: [], draft: null});
  if (notify) toast('已开启新对话。项目任务与可用时间继续保留。');
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll('#chat-form button, .quick-actions button, #draft-container button, #new-session, #nav-prefs, #project-select, #current-member, #new-project, .task-actions button, .task-actions select, .empty-state button, #nav-export, #nav-members, .dialog-actions button[type="submit"], .schedule-controls button, #retry').forEach(b => b.disabled = busy);
  $('send').disabled = busy || !state.member || voiceIsOpen(); $('new-session').disabled = busy || !state.member;
  $('send').textContent = busy ? '处理中…' : '发送';
  $('input-note').textContent = busy ? '正在处理，可查看对话中的执行进度' : 'Enter 发送 · Shift + Enter 换行';
}
async function send(message = '', action = null, retryBody = null) {
  if (voiceIsOpen()) { toast('请先点击“停止输入”，检查文字后再发送。'); return false; }
  if (!state.member) { toast('请先在顶部选择当前聊天成员。'); return false; }
  if (state.busy || !state.session || (!message.trim() && !action && !retryBody)) return false;
  const body = retryBody || {project_id: state.project, member_id: state.member, session_id: state.session, request_id: crypto.randomUUID(), message: message.trim(), action};
  if (!retryBody) { document.querySelector('.welcome')?.remove(); addMessage('user', body.message || '采用这份草案'); $('message').value = ''; }
  state.retry = body; $('retry').hidden = true; setBusy(true);
  const progress = startProgress();
  let succeeded = false;
  try {
    const result = await streamChat(body, progress.update);
    progress.finish(result);
    addMessage('assistant', result.reply); addSources(result.sources); addDownload(result.download);
    state.draft = result.draft; state.retry = null; renderDraft();
    // 即使部分成功或业务失败，也重新读取实际状态。
    await refreshPlan();
    if (result.error) toast(result.error.message); succeeded = result.ok;
  } catch (e) { progress.finish(null); toast(e.message); $('retry').hidden = !state.retry; try { await refreshPlan(); } catch {} }
  finally { progress.finish(null); setBusy(false); $('message').focus(); }
  return succeeded;
}

async function refreshPlan(preferredMemberName = null) {
  state.plan = await api(`/api/projects/${state.project}/plan`);
  const previousMember = state.member;
  if (preferredMemberName !== null) {
    const self = state.plan.members.find(m => m.name === preferredMemberName);
    if (self) {
      state.member = self.id; state.actorReady = true;
      try { localStorage.setItem(`studymate-member:${state.project}`, self.id); } catch {}
    }
  }
  document.title = `课伴 · ${state.plan.project.name}`;
  const tasks = state.plan.tasks, done = tasks.filter(t => t.status === 'done').length;
  $('stat-all').textContent = tasks.length; $('task-count').textContent = tasks.length;
  $('stat-doing').textContent = tasks.filter(t => t.status === 'doing').length;
  $('stat-done').textContent = done;
  $('stat-hours').textContent = (tasks.filter(t => t.status !== 'done').reduce((n, t) => n + t.estimated_minutes, 0) / 60).toFixed(1).replace('.0', '');
  $('schedule-version').textContent = `日程 v${state.plan.version}`;
  $('sync-status').textContent = `已同步 ${new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false})}`;
  renderIdentity();
  const selected = $('member-filter').value || (!previousMember && state.member ? state.member : '');
  $('member-filter').replaceChildren(new Option('所有成员', ''));
  state.plan.members.forEach(m => $('member-filter').add(new Option(memberLabel(m.id), m.id)));
  $('member-filter').value = state.plan.members.some(m => m.id === selected) ? selected : '';
  renderBoard(); renderDraft();
}
async function loadProjects() {
  state.projects = await api('/api/projects');
  $('project-select').replaceChildren(...state.projects.map(p => new Option(p.name, p.id)));
  $('project-select').value = state.project;
}
async function switchProject(projectId, preferredMemberName = null) {
  if (state.busy || !state.projects.some(p => p.id === projectId)) return false;
  state.inputs[actorKey()] = $('message').value;
  setBusy(true); speech.cancel();
  state.project = projectId; state.member = null; state.actorReady = false; state.session = null; state.draft = null; state.plan = null; state.retry = null;
  state.tab = 'tasks'; state.status = 'all';
  document.querySelectorAll('[data-tab]').forEach(b => { const active = b.dataset.tab === 'tasks'; b.classList.toggle('active', active); b.setAttribute('aria-selected', String(active)); });
  document.querySelectorAll('[data-status]').forEach(b => b.classList.toggle('selected', b.dataset.status === 'all'));
  $('retry').hidden = true; $('chat-stream').replaceChildren(node('p', 'muted', '正在打开项目…')); $('draft-container').replaceChildren(); $('board-body').replaceChildren();
  ['stat-all', 'stat-doing', 'stat-done', 'stat-hours', 'task-count'].forEach(id => $(id).textContent = '—');
  $('sync-status').textContent = '正在加载'; $('schedule-version').textContent = '';
  $('message').value = ''; $('member-filter').value = '';
  try {
    await refreshPlan(preferredMemberName); await restoreSession(); $('message').value = state.inputs[actorKey()] || '';
    try { localStorage.setItem('studymate-project', projectId); } catch {}
    return true;
  } catch (e) {
    toast('项目加载失败，请刷新后重试：' + e.message);
    return false;
  } finally { $('project-select').value = projectId; setBusy(false); }
}
$('project-select').onchange = () => switchProject($('project-select').value);
$('new-project').onclick = () => { if (!state.busy) $('project-dialog').showModal(); };
[$('close-project'), $('cancel-project')].forEach(b => b.onclick = () => $('project-dialog').close());
function projectNameInputs() { return [$('project-self-name'), ...document.querySelectorAll('#project-teammates input')]; }
function updateProjectMembers() {
  const inputs = projectNameInputs(), names = inputs.map(input => input.value.trim());
  inputs.forEach((input, i) => input.setCustomValidity(names[i] && names.filter(name => name === names[i]).length > 1 ? '姓名重复，请用不同昵称区分同名成员。' : input.value && !names[i] ? '请填写姓名或昵称，不能只填空格。' : ''));
  $('project-team-count').textContent = `${inputs.length} 人（含你）`;
  $('project-team-empty').hidden = inputs.length > 1;
}
$('project-self-name').oninput = updateProjectMembers;
$('add-project-member').onclick = () => {
  const empty = [...document.querySelectorAll('#project-teammates input')].find(input => !input.value.trim());
  if (empty) { empty.focus(); return; }
  const row = node('div', 'project-teammate'), label = node('label', 'sr-only', '组员姓名或昵称');
  const input = node('input'); input.id = `project-member-${crypto.randomUUID()}`; input.required = true; input.maxLength = 60; input.placeholder = '组员姓名或昵称'; label.htmlFor = input.id;
  input.oninput = updateProjectMembers;
  const remove = node('button', 'text-button', '移除'); remove.type = 'button';
  remove.onclick = () => { row.remove(); updateProjectMembers(); $('add-project-member').focus(); };
  row.append(label, input, remove); $('project-teammates').append(row); updateProjectMembers(); input.focus();
};
$('project-dialog').addEventListener('cancel', e => { if (state.busy) e.preventDefault(); });
$('project-form').onsubmit = async e => {
  e.preventDefault(); if (state.busy) return;
  updateProjectMembers(); if (!$('project-form').reportValidity()) return;
  const name = $('project-name').value.trim(), members = projectNameInputs().map(input => input.value.trim());
  if (!name) { toast('请填写项目名称。'); $('project-name').focus(); return; }
  const content = JSON.stringify({name, members});
  if (state.projectCreateRequest?.content !== content) state.projectCreateRequest = {content, body: {name, members, request_id: crypto.randomUUID()}};
  setBusy(true); document.querySelectorAll('#project-form input, #project-form button').forEach(input => input.disabled = true);
  try {
    const project = await post('/api/projects', state.projectCreateRequest.body);
    await loadProjects(); state.projectCreateRequest = null;
    $('project-dialog').close(); setBusy(false);
    const opened = await switchProject(project.id, members[0]);
    $('project-form').reset(); $('project-teammates').replaceChildren(); updateProjectMembers();
    if (opened) toast(`项目已创建，现在以「${members[0]}」的身份对话。`);
  } catch (e) { toast(e.message); }
  finally { setBusy(false); document.querySelectorAll('#project-form input, #project-form button').forEach(input => input.disabled = false); }
};
function empty(title, description, buttonText, callback) {
  const wrap = node('div', 'empty-state'); wrap.append(node('h3', '', title), node('p', '', description));
  if (buttonText) { const b = node('button', '', buttonText); b.onclick = callback; b.disabled = state.busy; wrap.append(b); }
  $('board-body').append(wrap);
}
function renderBoard() {
  if (!state.plan) return;
  $('board-body').replaceChildren();
  const member = $('member-filter').value;
  $('status-filters').hidden = state.tab !== 'tasks';
  if (state.tab === 'schedule') { renderSchedule(member); return; }
  const tasks = state.plan.tasks.filter(t => (!member || t.assignee_id === member) && (state.status === 'all' || t.status === state.status));
  if (!tasks.length) {
    empty(state.plan.tasks.length ? '没有符合筛选条件的任务' : '还没有任务',
      state.plan.tasks.length ? '试试切换状态或成员筛选。' : '在左侧告诉课伴作业要求。采用草案后，真实保存的任务会出现在这里。',
      state.plan.tasks.length ? null : '拆解示例作业', () => send('拆解演示作业')); return;
  }
  tasks.forEach(task => {
    const card = node('article', 'task-card');
    const top = node('div', 'task-top'); top.append(node('h3', 'task-title', task.title), node('span', `task-status ${task.status}`, statusNames[task.status])); card.append(top);
    const meta = node('div', 'task-meta');
    const chip = node('span', 'member-chip', `负责人：${memberLabel(task.assignee_id)}`); chip.title = assigneeName(task.assignee_id);
    meta.append(chip, node('span', '', `${task.estimated_minutes} 分钟`), node('span', '', `截止 ${dateTime(task.deadline)}`)); card.append(meta);
    if (task.description) { const details = node('details', 'task-description'); details.append(node('summary', '', '任务说明'), node('p', '', task.description)); card.append(details); }
    const actions = node('div', 'task-actions');
    const addAction = (label, patch) => { const b = node('button', '', label); b.disabled = state.busy; b.onclick = () => send(`将 ${memberLabel(task.assignee_id)} 负责的「${task.title}」${label}`, {type: 'update_task', task_id: task.id, patch}); actions.append(b); };
    if (task.assignee_id === state.member) {
      if (task.status === 'todo') addAction('开始任务', {status: 'doing'});
      if (task.status === 'doing') addAction('暂停任务', {status: 'paused'});
      if (task.status === 'paused') addAction('继续任务', {status: 'doing'});
      if (task.status !== 'done') addAction('标记完成', {status: 'done'}); else addAction('重新打开', {status: 'todo'});
    } else {
      card.append(node('p', 'task-owner-note', '切换到负责人视角后，可更新这项任务的状态。'));
      const view = node('button', '', `切换为${memberLabel(task.assignee_id)}后操作`); view.disabled = state.busy;
      view.onclick = () => switchMember(task.assignee_id); actions.append(view);
    }
    if (state.plan.members.length > 1) {
      const transfer = node('select'); transfer.setAttribute('aria-label', `转交任务：${task.title}`); transfer.add(new Option('转交给…', ''));
      state.plan.members.filter(m => m.id !== task.assignee_id).forEach(m => transfer.add(new Option(memberLabel(m.id), m.id)));
      transfer.disabled = state.busy; transfer.onchange = () => { if (transfer.value) send(`把「${task.title}」转交给 ${memberLabel(transfer.value)}`, {type: 'update_task', task_id: task.id, patch: {assignee_id: transfer.value}}); };
      actions.append(transfer);
    }
    card.title = `任务 ID：${task.id}`; card.append(actions);
    $('board-body').append(card);
  });
}
function renderSchedule(member) {
  const blocks = state.plan.schedule.filter(b => !member || b.assignee_id === member);
  if (!blocks.length) { empty('还没有日程', '先设置成员空闲时间，再点击「安排日程」。检查并采用草案后，日程会显示在这里。', '设置空闲时间', openPreferences); return; }
  let day, group;
  blocks.forEach(b => {
    const key = b.start_at.slice(0, 10);
    if (key !== day) { day = key; group = node('div', 'schedule-group'); group.append(node('h3', 'schedule-day', day)); $('board-body').append(group); }
    const block = node('article', 'schedule-block'), task = state.plan.tasks.find(t => t.id === b.task_id);
    block.append(node('strong', '', task?.title || b.task_id), node('span', '', `${dateTime(b.start_at)} — ${b.end_at.slice(11,16)} · ${memberLabel(b.assignee_id)}`)); group.append(block);
  });
  const controls = node('div', 'schedule-controls'), clear = node('button', '', '清空日程后重新安排');
  clear.disabled = state.busy; clear.onclick = () => send('清空已保存日程，保留任务与空闲时间', {type: 'clear_schedule'}); controls.append(clear); $('board-body').append(controls);
}
function renderDraft() {
  $('draft-container').replaceChildren();
  const draft = state.draft; if (!draft) return;
  const card = node('article', 'draft-card'), header = node('div', 'draft-header');
  header.append(node('strong', '', draft.type === 'tasks' ? '任务草案' : '日程草案'), node('span', '', '待确认，尚未保存')); card.append(header);
  const items = draft.type === 'tasks' ? draft.data : draft.data.blocks;
  items.forEach(item => {
    const row = node('div', 'draft-row');
    const title = draft.type === 'tasks' ? item.title : state.plan?.tasks.find(t => t.id === item.task_id)?.title || item.task_id;
    row.append(node('span', '', title), node('span', '', draft.type === 'tasks' ? `${memberLabel(item.assignee_id)} · ${item.estimated_minutes} 分钟` : `${dateTime(item.start_at)}—${item.end_at.slice(11,16)}`));
    if (draft.type === 'tasks') row.title = `截止 ${dateTime(item.deadline)}`;
    card.append(row);
  });
  const assumptions = [...draft.assumptions];
  if (draft.type === 'tasks' && items.length) assumptions.unshift(`首项截止：${dateTime(items[0].deadline)}；各项截止悬停查看。`);
  card.append(node('div', 'draft-assumptions', assumptions.join(' ')));
  const footer = node('div', 'draft-footer'), apply = node('button', 'button primary', '确认并保存');
  apply.disabled = state.busy; apply.onclick = () => send('采用这份草案', {type: 'apply_draft', draft_id: draft.id});
  footer.append(node('small', '', '确认后加入项目'), apply); card.append(footer); $('draft-container').append(card);
}

function localValue(iso) { return iso.slice(0, 16); }
function renderWindows() {
  const id = $('prefs-member').value, windows = state.plan.preferences.find(p => p.assignee_id === id)?.windows || [];
  $('windows-list').replaceChildren(); windows.forEach(w => addWindow(localValue(w.start_at), localValue(w.end_at)));
  if (!windows.length) addWindow();
}
function addWindow(start = '', end = '') {
  const row = node('div', 'window-row'), a = node('input'), b = node('input');
  a.type = b.type = 'datetime-local'; a.required = b.required = true; a.step = b.step = '60';
  a.value = start; b.value = end; a.setAttribute('aria-label', '可用时间开始（北京时间）'); b.setAttribute('aria-label', '可用时间结束（北京时间）');
  const remove = node('button', 'icon-button', '×'); remove.type = 'button'; remove.setAttribute('aria-label', '删除此时段'); remove.onclick = () => row.remove();
  row.append(a, node('span', '', '至'), b, remove); $('windows-list').append(row);
}
function openPreferences() {
  if (!state.plan || state.busy) return;
  $('prefs-member').replaceChildren(); state.plan.members.forEach(m => $('prefs-member').add(new Option(m.name, m.id)));
  if (state.member) $('prefs-member').value = state.member;
  renderWindows(); $('prefs-dialog').showModal();
}
function renderMembers() {
  $('members-count').textContent = `（${state.plan.members.length} 人）`; $('members-list').replaceChildren();
  state.plan.members.forEach(m => {
    const row = node('form', 'member-row'), input = node('input'); input.value = m.name; input.required = true; input.maxLength = 60; input.setAttribute('aria-label', `成员姓名：${m.name}`);
    const save = node('button', 'button secondary', '改名'); save.type = 'submit';
    const remove = node('button', 'text-button', '移除'); remove.type = 'button'; remove.disabled = state.plan.members.length <= 1;
    row.onsubmit = e => { e.preventDefault(); changeMember({operation: 'rename', member_id: m.id, name: input.value.trim()}, `将成员「${m.name}」改名为「${input.value.trim()}」`); };
    remove.onclick = () => changeMember({operation: 'remove', member_id: m.id}, `移除成员「${m.name}」`);
    row.append(input, save, remove); $('members-list').append(row);
  });
}
function openMembers() { if (state.busy || !state.plan) return; renderMembers(); $('members-dialog').showModal(); }
async function changeMember(args, message) {
  if (state.busy) return;
  $('members-dialog').close();
  const ok = await send(message, {type: 'manage_member', ...args});
  if (ok) $('new-member-name').value = '';
  renderMembers(); $('members-dialog').showModal();
}
$('nav-members').onclick = openMembers;
$('close-members').onclick = () => $('members-dialog').close();
$('add-member-form').onsubmit = e => { e.preventDefault(); const name = $('new-member-name').value.trim(); if (name) changeMember({operation: 'add', name}, `添加成员「${name}」`); };
$('prefs-form').onsubmit = async e => {
  e.preventDefault();
  const windows = Array.from($('windows-list').children).map(row => { const inputs = row.querySelectorAll('input'); return {start_at: inputs[0].value + ':00+08:00', end_at: inputs[1].value + ':00+08:00'}; });
  if (windows.some(w => w.start_at >= w.end_at)) { toast('开始时间必须早于结束时间。'); return; }
  const assignee = $('prefs-member').value;
  $('prefs-dialog').close();
  const ok = await send(`保存 ${memberLabel(assignee)} 的 ${windows.length} 个可用时段`, {type: 'set_preferences', assignee_id: assignee, windows});
  if (!ok) $('prefs-dialog').showModal();
};
$('prefs-member').onchange = renderWindows;
$('add-window').onclick = () => addWindow();
[$('close-prefs'), $('cancel-prefs')].forEach(b => b.onclick = () => $('prefs-dialog').close());
$('nav-prefs').onclick = openPreferences;
$('nav-export').onclick = () => send('导出计划', {type: 'export_plan'});
$('new-session').onclick = () => newSession().catch(e => toast(e.message));
$('retry').onclick = () => state.retry && send('', null, state.retry);
$('chat-form').onsubmit = e => { e.preventDefault(); send($('message').value); };
$('message').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('message').value); } };
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => send(b.dataset.prompt));
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; document.querySelectorAll('[data-tab]').forEach(t => { t.classList.toggle('active', t === b); t.setAttribute('aria-selected', String(t === b)); }); renderBoard(); });
document.querySelectorAll('[data-status]').forEach(b => b.onclick = () => { state.status = b.dataset.status; document.querySelectorAll('[data-status]').forEach(t => t.classList.toggle('selected', t === b)); renderBoard(); });
$('member-filter').onchange = renderBoard;
$('refresh').onclick = () => refreshPlan().catch(e => toast(e.message));
const speech = createSpeech({onTranscript: text => { $('message').value = ($('message').value + ' ' + text).trim(); }, onError: toast,
  onState: ({phase, interim, reason, elapsedSeconds}) => {
    state.voicePhase = phase;
    const open = voiceIsOpen(), waiting = ['pausing', 'stopping'].includes(phase);
    const labels = {starting: '正在开启麦克风…', listening: '正在聆听', pausing: '正在暂停…', paused: '已暂停', stopping: '正在停止输入…', finished: '语音输入已结束', error: '语音输入已停止'};
    const hints = {starting: '请查看浏览器的麦克风权限提示；也可点击“停止输入”取消。',
      listening: '说完点“停止输入”；临时停一下点“暂停”。累计聆听60秒会自动结束。',
      pausing: '正在收取最后一段文字，请稍候。', paused: '已停止聆听，文字已保留。点“继续输入”补充，或点“停止输入”结束。',
      stopping: '正在收取最后一段文字，请稍候。',
      finished: reason === 'limit' ? '已达到60秒，自动结束。请检查文字后再发送。' : reason === 'browser-ended' ? '浏览器已结束本次聆听。请检查文字，或再次点击“语音输入”补充。' : '请检查输入框中的文字，再点击“发送”。',
      error: reason === 'unsupported' ? '此浏览器不支持语音识别，请继续打字输入。' : reason === 'not-allowed' ? '未获得麦克风权限，请在浏览器中允许后重试；也可继续打字。' : reason === 'network' ? '语音识别服务网络不可用，文字已保留；可重试或继续打字。' : '聆听已结束，已识别的文字会保留；可重试或继续打字。'};
    $('voice-panel').hidden = phase === 'idle'; $('voice-panel').dataset.phase = phase;
    $('voice-status').textContent = labels[phase] || '语音输入'; $('voice-hint').textContent = hints[phase] || '';
    $('voice-time').textContent = `${String(Math.floor(elapsedSeconds / 60)).padStart(2, '0')}:${String(elapsedSeconds % 60).padStart(2, '0')}`;
    $('voice-preview').hidden = !interim; $('voice-preview').textContent = interim ? `正在识别：${interim}` : '';
    $('mic').textContent = open ? '停止输入' : '语音输入'; $('mic').classList.toggle('recording', open);
    $('mic').setAttribute('aria-label', open ? '停止中文语音输入' : '开始中文语音输入'); $('mic').setAttribute('aria-pressed', String(open));
    $('mic').title = open ? '结束语音输入，保留文字' : '中文语音输入'; $('mic').disabled = state.busy || waiting;
    $('pause-mic').hidden = !open; $('pause-mic').textContent = phase === 'paused' ? '继续输入' : '暂停';
    $('pause-mic').disabled = state.busy || !['paused', 'listening'].includes(phase);
    $('send').disabled = state.busy || !state.member || open;
  }});
$('mic').onclick = () => voiceIsOpen() ? speech.stop() : speech.start();
$('pause-mic').onclick = () => state.voicePhase === 'paused' ? speech.start() : speech.pause();
$('stop-speech').onclick = speech.stopSpeaking;
if (!speech.supported) { $('mic').title = '本浏览器不支持语音识别，点击查看说明'; }

async function init() {
  try {
    const health = await api('/api/health'); state.project = health.project_id;
    await loadProjects();
    try { const savedProject = localStorage.getItem('studymate-project'); if (state.projects.some(p => p.id === savedProject)) state.project = savedProject; } catch {}
    $('project-select').value = state.project;
    const demo = health.mode === 'demo';
    $('mode-text').textContent = demo ? '当前为演示模式：支持示例指令；自由描述作业需要先配置模型。' : health.model_configured ? '已连接模型，可以直接描述作业要求。' : '尚未配置模型密钥，请按 README 配置后重启。';
    if (!demo && health.model_configured) $('mode-text').textContent += health.web_search?.available ? ' 联网搜索已配置。' : ' 联网搜索尚未启用。';
    if (!demo && health.model_configured && health.weather?.available) $('mode-text').textContent += ' 天气查询已启用。';
    $('chat-mode').textContent = demo ? '演示模式' : '模型模式';
    if (!demo) { const quick = document.querySelector('[data-prompt="拆解演示作业"]'); quick.textContent = '输入作业要求'; quick.onclick = () => { $('message').value = '请按当前项目成员拆解以下作业（截止日期和时间为：）：\n'; $('message').focus(); }; }
    await refreshPlan(); await restoreSession();
  } catch (e) { $('mode-text').textContent = '连接失败，请检查后端是否启动后刷新。'; toast(e.message); }
}
init();
