import {selectProject, projectLink} from './project-context.js?v=20261101';
const $=id=>document.getElementById(id);
const state={project:'',workspace:null,projects:[],busy:false,step:'instructions',health:null,taskIndex:null,resultFilter:'all'};
function node(tag,cls='',text=''){const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});let d;try{d=await r.json();}catch{throw Error('服务未返回有效结果，请检查连接。');}if(!r.ok)throw Error(d.error?.message||d.detail?.[0]?.msg||'请求未成功，请重试。');return d;}
const path=s=>`/api/review/${encodeURIComponent(state.project)}${s}`;
function message(text,error=false){$('error').hidden=!error;$('status').hidden=error||!text;$(error?'error':'status').textContent=text;document.querySelectorAll('.dialog-error').forEach(e=>e.remove());const dialog=document.querySelector('dialog[open]');if(error&&dialog){const e=node('p','dialog-error hint',text);e.setAttribute('role','alert');dialog.querySelector('form').prepend(e);}}
function setBusy(b){state.busy=b;document.querySelectorAll('button,input,textarea,select').forEach(e=>{if(b){e.dataset.wasDisabled=e.disabled?'1':'0';e.disabled=true;}else if('wasDisabled' in e.dataset){e.disabled=e.dataset.wasDisabled==='1';delete e.dataset.wasDisabled;}});if(!b){$('analyze').disabled=!state.workspace?.instructions||!state.workspace?.materials.length||state.health?.mode!=='llm'||!state.health?.model_configured;}}
async function action(fn,label='正在保存…'){if(state.busy)return;setBusy(true);message(label);try{await fn();message('');}catch(e){message(e.message,true);}finally{setBusy(false);}}
function step(s){state.step=s;document.querySelector('.intro').hidden=s==='results';try{history.replaceState(null,'',projectLink('/review',state.project,{step:s}));}catch{}for(const x of ['instructions','materials','results'])$(x+'-panel').hidden=x!==s;document.querySelectorAll('[data-step]').forEach(b=>b.classList.toggle('active',b.dataset.step===s));}
function render(){const w=state.workspace;if(!w)return;syncWorkspaceLinks();$('instructions').value=w.instructions;$('material-count').textContent=w.materials.length;$('material-total').textContent=`已保存 ${w.materials.length} 份，共 ${w.materials.reduce((n,m)=>n+m.content.length,0)} 字`;$('instruction-state').textContent=`已保存版本 v${w.version}`;$('stale').hidden=!w.stale;renderMaterials();renderResults();}
function renderMaterials(){const box=$('materials-list');box.replaceChildren();for(const m of state.workspace.materials){const div=node('div','material'),head=node('div','material-head'),label=node('b','',m.name),remove=node('button','text-button','移除材料');remove.type='button';remove.onclick=()=>{if(!confirm(`移除“${m.name}”？已有分析将保留但标记过期，请保留原文件以便重新添加。`))return;action(async()=>{state.workspace=await api(path('/materials/'+encodeURIComponent(m.id)),'DELETE');render();});};head.append(label,remove);const details=node('details'),summary=node('summary','',`查看已保存正文 · ${m.content.length}字`);details.append(summary,node('pre','',m.content));div.append(head,details);box.append(div);}}
function source(label,quote){const e=node('div','source');e.append(node('small','',label),document.createTextNode(quote));return e;}
const resultLabels={missing:'待补充',uncertain:'待核实',evidence_found:'有文字依据',confirmed:'已人工确认'};
function resultKind(item){
  if(item.human_status==='confirmed')return 'confirmed';
  if(item.human_status==='needs_work')return 'missing';
  return item.status;
}
function hasUnsavedReviews(){
  return [...document.querySelectorAll('.review-item')].some(card=>{
    const item=state.workspace?.latest_run?.items[Number(card.dataset.itemIndex)];
    return item&&(card.querySelector('select').value!==item.human_status||card.querySelector('textarea').value!==item.human_note);
  });
}
function renderResults(){
  const w=state.workspace,r=w.latest_run,box=$('results-list');
  // Keep open details and edits in other items when one review is saved.
  const previous=new Map();
  if(r&&box.dataset.runId===r.id)for(const card of box.querySelectorAll('.review-item')){
    const select=card.querySelector('select'),note=card.querySelector('textarea');
    previous.set(Number(card.dataset.itemIndex),{open:card.querySelector('details').open,
      dirty:select.value!==card.dataset.savedStatus||note.value!==card.dataset.savedNote,
      status:select.value,note:note.value});
  }
  box.replaceChildren();$('summary').replaceChildren();$('export').hidden=!r;
  $('run-info').textContent=r?`${r.created_at.slice(0,16).replace('T',' ')} · 材料版本 ${r.source_version}`:'保存要求和材料后，即可开始检查。';
  if(!r){box.dataset.runId='';$('analyze').textContent='开始检查';return;}
  if(box.dataset.runId!==r.id)state.resultFilter='all';
  box.dataset.runId=r.id;$('analyze').textContent=w.stale?'重新检查材料':'重新检查';
  const counts={missing:0,uncertain:0,evidence_found:0,confirmed:0};
  r.items.forEach(item=>counts[resultKind(item)]++);
  const overview=node('div','result-overview');
  overview.append(node('p','result-total',`共检查 ${r.items.length} 项要求`));
  const needs=counts.missing+counts.uncertain;
  overview.append(node('p','result-next',needs?`先处理 ${counts.missing} 项待补充、${counts.uncertain} 项待核实。`:'当前没有待补充或待核实项；请核对文字依据。'));
  overview.append(node('p','result-caution','有文字依据不等于作业合格，仍需人工判断。'));
  $('summary').append(overview);
  const filters=node('div','result-filters');filters.setAttribute('role','group');filters.setAttribute('aria-label','筛选检查结果');
  for(const [kind,label] of [['all','全部'],...Object.entries(resultLabels)]){
    const count=kind==='all'?r.items.length:counts[kind];
    if(kind==='confirmed'&&!count)continue;
    const button=node('button','result-filter',`${label} ${count}`);button.type='button';button.dataset.filter=kind;
    button.setAttribute('aria-pressed',String(state.resultFilter===kind));
    button.onclick=()=>{if(state.busy||!canLeave())return;state.resultFilter=kind;message('');renderResults();};
    filters.append(button);
  }
  $('summary').append(filters);
  const progress=node('div','result-meta');
  progress.append(node('span','subtle',`已人工复核 ${r.items.filter(x=>x.human_status!=='unreviewed').length}/${r.items.length} · 待处理项优先显示`));
  const events=node('details','run-details'),eventSummary=node('summary','','检查记录');events.append(eventSummary);
  r.events.forEach(x=>events.append(node('p','subtle',`${x.label} · 成功`)));progress.append(events);$('summary').append(progress);
  const order={missing:0,uncertain:1,evidence_found:2,confirmed:3};
  const visible=r.items.map((item,i)=>({item,i,kind:resultKind(item)}))
    .filter(x=>state.resultFilter==='all'||x.kind===state.resultFilter)
    .sort((a,b)=>order[a.kind]-order[b.kind]||a.i-b.i);
  if(!visible.length)box.append(node('p','result-empty','没有这类检查项。'));
  for(const {item,i,kind} of visible){
    const old=previous.get(i),card=node('article','card review-item');
    card.dataset.itemIndex=i;card.dataset.savedStatus=item.human_status;card.dataset.savedNote=item.human_note;
    const details=node('details','review-details');details.open=old?.open||false;
    const summary=node('summary','review-toggle'),heading=node('div','review-heading');
    heading.append(node('h3','',item.title),node('span','badge '+kind,resultLabels[kind]));
    const preview=item.human_status==='needs_work'?(item.human_note||item.suggestion||'已人工确认需要补充。'):
      kind==='confirmed'?(item.human_note||'已保存人工确认结论。'):
      kind==='missing'?(item.suggestion||'当前提供的材料中未找到对应内容。'):
      kind==='uncertain'?'现有文字还不能确认，请展开核对。':'已找到相关原文，展开查看依据。';
    const previewText=node('p','review-preview',preview);
    const affordance=node('span','review-affordance');
    affordance.append(node('span','when-closed','查看依据与复核'),node('span','when-open','收起详情'));
    const link=r.task_links?.find(t=>t.item_index===i);
    if(link)affordance.prepend(node('span','task-created','已创建补充任务 · '));
    summary.append(heading,previewText,affordance);details.append(summary);
    const content=node('div','review-content');
    content.append(node('h4','','要求与依据'),source(`老师要求 · 第${item.requirement_line}行`,item.requirement_quote));
    if(item.evidence.length)for(const e of item.evidence)content.append(source(`材料《${e.material_name}》· 第${e.line}行`,e.quote));
    else content.append(node('p','evidence-empty','当前提供的材料中没有可定位的引文。'));
    content.append(node('p','review-explanation',item.explanation));
    if(item.suggestion)content.append(node('p','review-suggestion','建议：'+item.suggestion));
    const human=node('div','human'),caption=node('label','','人工复核'),select=node('select');
    select.id=`human-${i}`;select.setAttribute('aria-label',`第${i+1}项人工复核`);
    for(const [v,t] of [['unreviewed','未复核'],['confirmed','人工确认'],['needs_work','需补充']]){const o=node('option','',t);o.value=v;select.append(o);}
    select.value=old?.dirty?old.status:item.human_status;caption.htmlFor=select.id;
    const note=node('textarea','human-note');note.rows=2;note.maxLength=1000;note.value=old?.dirty?old.note:item.human_note;
    note.placeholder='补充说明（选填）';note.setAttribute('aria-label',`第${i+1}项复核备注`);
    const save=node('button','secondary','保存复核');
    save.onclick=()=>action(async()=>{state.workspace=await api(path('/human'),'PATCH',{run_id:r.id,index:i,status:select.value,note:note.value});render();});
    const controls=node('div','human-controls');controls.append(select,save);human.append(caption,controls,note);
    if(link){
      const a=node('a','saved','查看已创建的补充任务 ↗');a.href=projectLink('/',state.project,{task_id:link.task_id});
      a.onclick=e=>{if(!canLeave())e.preventDefault();};human.append(a);
    }else if(item.human_status==='needs_work'){
      const task=node('button','primary','生成补充任务');task.onclick=()=>{if(canLeave())openTask(i);};task.disabled=w.stale;human.append(task);
    }
    select.disabled=note.disabled=save.disabled=w.stale;
    content.append(human);details.append(content);card.append(details);box.append(card);
  }
}
async function loadProject(id){state.project=id;state.workspace=await api(path(''));try{localStorage.setItem('studymate-review-project',id);localStorage.setItem('studymate-project',id);}catch{}render();const requested=new URLSearchParams(location.search).get('step');step(['instructions','materials','results'].includes(requested)?requested:state.workspace.latest_run?'results':state.workspace.instructions?'materials':'instructions');}
async function importFile(input,kind){const f=input.files[0];if(!f)return;await action(async()=>{if(f.size>512*1024)throw Error('文件最大512KB，请提取相关内容后再上传。');const bytes=new Uint8Array(await f.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);state.workspace=await api(path(kind==='instructions'?'/instructions/upload':'/upload'),'POST',{name:f.name,content_base64:btoa(binary)});render();if(kind==='instructions')step('materials');},'正在读取并保存文件正文…');input.value='';}
document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{if(!state.busy)step(b.dataset.step);});
$('instructions').oninput=()=>{$('instruction-state').textContent='要求有未保存的修改，请先保存';};
$('save-instructions').onclick=()=>action(async()=>{state.workspace=await api(path('/instructions'),'PUT',{text:$('instructions').value});render();step('materials');});
$('material-form').onsubmit=e=>{e.preventDefault();action(async()=>{state.workspace=await api(path('/materials'),'POST',{name:$('material-name').value,content:$('material-text').value});$('material-form').reset();render();});};
$('instruction-file').onchange=()=>importFile($('instruction-file'),'instructions');$('material-file').onchange=()=>importFile($('material-file'),'material');
$('to-results').onclick=()=>{step('results');if(!state.workspace.materials.length)message('请至少添加一份实际材料。',true);};
$('project').onchange=()=>{const id=$('project').value;if(!canLeave()){$('project').value=state.project;return;}action(async()=>{history.replaceState(null,'',projectLink('/review',id));await loadProject(id);},'正在加载项目交付材料…');};
$('analyze').onclick=()=>action(async()=>{if(hasUnsavedReviews())throw Error('有尚未保存的复核记录，请先保存后重新检查。');if($('material-name').value.trim()||$('material-text').value.trim())throw Error('有尚未保存的材料，请先保存或清空输入。');if($('instructions').value.trim()!==state.workspace.instructions)throw Error('老师要求有未保存修改，请先回到第一步保存。');const begin=Date.now(),timer=setInterval(()=>message(`后端已收到自查请求，正在等待模型与引文校验 · ${Math.floor((Date.now()-begin)/1000)}秒。请勿重复点击。`),1000);try{state.workspace=await api(path('/analyze'),'POST',{request_id:crypto.randomUUID()});render();}finally{clearInterval(timer);}},'后端开始读取材料并调用真实模型…');
$('export').onclick=()=>action(async()=>{const r=await api(path('/export'),'POST',{run_id:state.workspace.latest_run.id});const a=node('a');a.href=r.url;a.download=r.filename;a.click();},'正在生成自查报告…');
function canLeave() {
  const w=state.workspace;
  if(state.busy){message('请等待当前保存或检查完成，再返回工作台。',true);return false;}
  const changed=w&&($('instructions').value.trim()!==w.instructions||$('material-name').value.trim()||$('material-text').value.trim()||
    hasUnsavedReviews());
  if(changed){message('有尚未保存的要求、材料或复核记录，请先保存后返回工作台。',true);return false;}
  return true;
}
function syncWorkspaceLinks(){
  const href=projectLink('/',state.project);
  $('workspace-link').href=$('brand-workspace').href=href;
}
for(const id of ['workspace-link','brand-workspace'])$(id).onclick=e=>{if(!canLeave())e.preventDefault();};
$('new-project').onclick=()=>{
  if(!canLeave())return;
  if(!state.workspace.instructions){step('instructions');message('先保存老师要求，再把它用于项目分工。',true);return;}
  location.assign(projectLink('/',state.project,{prepare_from_requirements:'1'}));
};
async function refreshProjects(preferred){state.projects=await api('/api/projects');$('project').replaceChildren();state.projects.forEach(p=>{const o=node('option','',p.name);o.value=p.id;$('project').append(o);});const id=selectProject(state.projects,location.search,preferred);$('project').value=id;await loadProject(id);}
async function openTask(i){await action(async()=>{const members=await api(`/api/projects/${encodeURIComponent(state.project)}/members`);$('task-owner').replaceChildren();const empty=node('option','','请选择负责人');empty.value='';$('task-owner').append(empty);members.forEach(m=>{const o=node('option','',m.name);o.value=m.id;$('task-owner').append(o);});state.taskIndex=i;$('task-title').textContent=state.workspace.latest_run.items[i].title;$('task-deadline').value='';$('task-dialog').showModal();});}
$('cancel-task').onclick=()=>$('task-dialog').close();$('task-form').onsubmit=e=>{e.preventDefault();action(async()=>{const result=await api(path('/tasks'),'POST',{run_id:state.workspace.latest_run.id,index:state.taskIndex,assignee_id:$('task-owner').value,deadline:$('task-deadline').value+':00+08:00',estimated_minutes:Number($('task-minutes').value)});if(!result.ok)throw Error('未确认任务创建成功');state.workspace=await api(path(''));render();$('task-dialog').close();});};
(async()=>{try{state.health=await api('/api/health');$('model-status').textContent=state.health.mode==='llm'&&state.health.model_configured?`真实模型：${state.health.model_name} · 分析会消耗额度`:'真实模型尚未就绪；可保存材料，自查不会用模拟结果替代。';let preferred;try{preferred=localStorage.getItem('studymate-project');}catch{}await refreshProjects(preferred);setBusy(false);}catch(e){message(e.message,true);}})();
