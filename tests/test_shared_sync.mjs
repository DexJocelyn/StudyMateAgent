import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {JSDOM}=require(process.env.STUDYMATE_TEST_JSDOM || 'jsdom');

test('实际前端自动同步保留输入，编辑时暂缓，切换项目后丢弃迟到的旧项目数据',async()=>{
  const html=await readFile(new URL('../frontend/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost/?project_id=project_a',pretendToBeVisual:true});
  for(const key of ['window','document','location','history','localStorage','sessionStorage','Option']) globalThis[key]=key==='window'?dom.window:dom.window[key];
  globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
  globalThis.ResizeObserver=class{observe(){}};
  dom.window.HTMLElement.prototype.scrollIntoView=function(){};
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  dom.window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  let poll, deferA=false, deliverA;
  const interval=globalThis.setInterval;
  const timeout=globalThis.setTimeout, toastTimers=[];
  globalThis.setTimeout=(fn,delay,...args)=>{const timer=timeout(fn,delay,...args);if(delay===5500)toastTimers.push(timer);return timer;};
  globalThis.setInterval=(fn,delay)=>{if(delay===3000)poll=fn;return 1;};
  const makePlan=id=>({project:{id,name:id},version:0,exported_at:'2026-11-05T10:00:00+08:00',
    members:[{id:'member_'+id,name:id==='project_a'?'同学甲':'同学乙'}],schedule:[],preferences:[],
    tasks:[{id:'task_'+id,title:id==='project_a'?'甲项目原任务':'乙项目任务',assignee_id:'member_'+id,deadline:'2030-11-08T20:00:00+08:00',estimated_minutes:30,status:'todo',description:'测试说明'}]});
  const plans={project_a:makePlan('project_a'),project_b:makePlan('project_b')};
  const projects=[{id:'project_a',name:'甲项目',archived:false,member_count:2,task_count:2},{id:'project_b',name:'乙项目',archived:false,member_count:1,task_count:1}];
  plans.project_a.members.push({id:'member_other',name:'其他同学'});
  plans.project_a.tasks.push({...plans.project_a.tasks[0],id:'task_other',assignee_id:'member_other',title:'其他同学的任务'});
  localStorage.setItem('studymate-member:project_a','member_project_a');
  const actions=[];
  const response=data=>new Response(JSON.stringify(data),{status:200,headers:{'content-type':'application/json'}});
  const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{
    const path=new URL(url,'http://localhost').pathname;
    if(path==='/api/health')return response({project_id:'project_a',mode:'demo'});
    if(path==='/api/projects')return response(projects.filter(p=>url.includes('include_archived=true')||!p.archived));
    const projectPatch=path.match(/^\/api\/projects\/(project_[ab])$/);
    if(projectPatch && options.method==='PATCH'){
      const p=projects.find(p=>p.id===projectPatch[1]),patch=JSON.parse(options.body);Object.assign(p,patch);
      if(patch.name)plans[p.id].project.name=patch.name;
      return response(p);
    }
    const match=path.match(/^\/api\/projects\/(project_[ab])\/plan$/);
    if(match){
      const snapshot=structuredClone(plans[match[1]]);
      if(match[1]==='project_a' && deferA){deferA=false;return new Promise(resolve=>{deliverA=()=>resolve(response(snapshot));});}
      return response(snapshot);
    }
    if(path.includes('/summary'))return response({instructions_saved:false});
    if(path==='/api/sessions/latest')return response({session_id:null});
    if(path==='/api/sessions')return response({session_id:'session_'+JSON.parse(options.body).project_id});
    if(path==='/api/chat/stream'){
      const body=JSON.parse(options.body);actions.push(body.action);
      return new Response(JSON.stringify({type:'result',data:{ok:true,reply:'草案待确认',tool_events:[],error:null,draft:{id:'draft_test',type:'schedule',data:{assignee_id:'member_project_a',expected_version:0,
        blocks:[{task_id:'task_project_a',assignee_id:'member_project_a',start_at:'2030-11-05T19:00:00+08:00',end_at:'2030-11-05T19:30:00+08:00'}]},assumptions:[]}}})+'\n');
    }
    throw new Error('未预期的请求：'+url);
  };
  async function until(check){for(let i=0;i<80;i++){if(check())return;await new Promise(resolve=>setTimeout(resolve,2));}throw new Error('前端状态未就绪');}
  try{
    await import('../frontend/app.js?shared-sync-test');
    await until(()=>document.getElementById('chat-title').textContent==='对话 · 同学甲' && !document.getElementById('send').disabled);
    const input=document.getElementById('message');input.value='尚未发送的个人草稿';input.focus();
    assert.ok(!document.getElementById('board-body').textContent.includes('其他同学的任务'));
    document.getElementById('view-team').click();
    assert.match(document.getElementById('board-body').textContent,/其他同学的任务/);
    document.getElementById('project-schedule').click();
    assert.equal(document.getElementById('member-filter').value,'member_project_a');
    const generate=[...document.querySelectorAll('.schedule-toolbar button')].find(e=>e.textContent==='生成我的日程草案');
    assert.ok(generate);generate.click();
    await until(()=>actions.length===1 && !document.getElementById('send').disabled);
    assert.deepEqual(actions[0],{type:'generate_schedule',scope:'mine'});
    assert.match(document.getElementById('draft-container').textContent,/我的日程草案/);
    assert.equal(input.value,'尚未发送的个人草稿');
    document.querySelector('[data-tab="tasks"]').click();input.focus();
    plans.project_a.tasks[0].title='另一端修改后的甲任务';
    await poll();
    assert.match(document.getElementById('board-body').textContent,/另一端修改后的甲任务/);
    assert.equal(input.value,'尚未发送的个人草稿');
    const dialog=document.getElementById('prefs-dialog');dialog.setAttribute('open','');
    plans.project_a.tasks[0].title='编辑期间的远端变化';await poll();
    assert.ok(!document.getElementById('board-body').textContent.includes('编辑期间的远端变化'));
    dialog.removeAttribute('open');await poll();
    assert.match(document.getElementById('board-body').textContent,/编辑期间的远端变化/);
    deferA=true;const pending=poll();await until(()=>Boolean(deliverA));
    const selector=document.getElementById('project-select');selector.value='project_b';selector.dispatchEvent(new dom.window.Event('change'));
    await until(()=>document.getElementById('chat-title').textContent==='对话 · 同学乙' && !document.getElementById('send').disabled);
    deliverA();await pending;
    assert.match(document.getElementById('board-body').textContent,/乙项目任务/);
    assert.ok(!document.getElementById('board-body').textContent.includes('甲任务'));
    document.getElementById('manage-projects').click();
    await until(()=>document.querySelectorAll('.managed-project').length===2);
    const button=(row,text)=>[...row.querySelectorAll('button')].find(b=>b.textContent===text);
    let row=document.querySelector('[data-project-id="project_a"]');button(row,'重命名').click();
    const form=row.querySelector('form');form.querySelector('input').value='甲项目正式名称';
    form.dispatchEvent(new dom.window.Event('submit',{cancelable:true}));
    await until(()=>!document.getElementById('project-select').disabled && document.getElementById('project-select').textContent.includes('甲项目正式名称'));
    row=document.querySelector('[data-project-id="project_a"]');button(row,'归档').click();
    await until(()=>!document.getElementById('project-select').disabled && ![...document.getElementById('project-select').options].some(o=>o.value==='project_a'));
    const filter=document.getElementById('project-archive-filter');filter.value='archived';filter.dispatchEvent(new dom.window.Event('change'));
    row=document.querySelector('[data-project-id="project_a"]');assert.ok(row);button(row,'恢复到列表').click();
    await until(()=>!document.getElementById('project-select').disabled && [...document.getElementById('project-select').options].some(o=>o.value==='project_a'));
    row=document.querySelector('[data-project-id="project_b"]');button(row,'归档').click();
    await until(()=>!document.getElementById('project-select').disabled && document.getElementById('project-select').value==='project_a');
    assert.ok(!document.getElementById('project-manager').open);
    assert.equal(plans.project_a.tasks[0].title,'编辑期间的远端变化');
  }finally{toastTimers.forEach(clearTimeout);globalThis.setTimeout=timeout;globalThis.setInterval=interval;globalThis.fetch=originalFetch;dom.window.close();}
});
