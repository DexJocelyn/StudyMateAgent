import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {JSDOM}=require(process.env.STUDYMATE_TEST_JSDOM||'jsdom');

test('结果按待处理优先展示，筛选后保存仍对应原检查项，并保留其他未保存备注',async()=>{
  const html=await readFile(new URL('../frontend/review.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost/review?project_id=project_review&step=results'});
  const keys=['window','document','location','history','localStorage'];
  const originals=new Map(keys.map(k=>[k,globalThis[k]]));
  for(const k of keys)globalThis[k]=k==='window'?dom.window:dom.window[k];
  const originalFetch=globalThis.fetch,requests=[];
  const item=(title,status)=>({title,status,requirement_quote:'报告应当满足这些要求。',requirement_line:1,
    explanation:'仅根据提供的正文核对。',evidence:[],suggestion:'请补充对应材料。',human_status:'unreviewed',human_note:''});
  const w={project_id:'project_review',instructions:'报告应当满足这些要求。',version:2,stale:false,
    materials:[{id:'material_one',name:'报告.md',content:'报告中的测试内容。'}],latest_run:{id:'run_one',source_version:2,
      created_at:'2026-11-05T12:00:00+08:00',events:[],task_links:[],items:[item('目标用户','evidence_found'),item('分工表','missing'),item('公开仓库','uncertain')]}};
  const response=x=>new Response(JSON.stringify(x),{status:200});
  globalThis.fetch=async(url,options={})=>{
    const path=new URL(url,'http://localhost').pathname;
    if(path==='/api/health')return response({mode:'llm',model_configured:true,model_name:'test'});
    if(path==='/api/projects')return response([{id:'project_review',name:'测试项目'}]);
    if(path==='/api/review/project_review/human'){
      const body=JSON.parse(options.body);requests.push(body);
      Object.assign(w.latest_run.items[body.index],{human_status:body.status,human_note:body.note});return response(w);
    }
    if(path==='/api/review/project_review')return response(w);
    throw Error('不应调用额外接口：'+url);
  };
  const until=async(check)=>{for(let n=0;n<60;n++){if(check())return;await new Promise(r=>setTimeout(r,2));}throw Error('未达到预期状态');};
  const row=index=>document.querySelector(`[data-item-index="${index}"]`);
  const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text);
  try{
    await import('../frontend/review.js?ui-test');
    await until(()=>document.querySelectorAll('.review-item').length===3);
    assert.deepEqual([...document.querySelectorAll('.review-item')].map(x=>Number(x.dataset.itemIndex)),[1,2,0]);
    assert.ok([...document.querySelectorAll('.review-details')].every(d=>!d.open));
    button('待核实 1').click();assert.equal(document.querySelectorAll('.review-item').length,1);
    assert.ok(row(2));row(2).querySelector('details').open=true;
    row(2).querySelector('textarea').value='尚未保存的仓库备注';
    button('全部 3').click();assert.equal(document.querySelectorAll('.review-item').length,1);
    assert.match(document.getElementById('error').textContent,/尚未保存/);
    row(2).querySelector('select').value='confirmed';row(2).querySelector('button').click();
    await until(()=>!document.getElementById('analyze').disabled&&requests.length===1);
    assert.equal(requests[0].index,2);assert.equal(requests[0].note,'尚未保存的仓库备注');
    button('全部 3').click();assert.equal(document.querySelectorAll('.review-item').length,3);
    row(0).querySelector('details').open=true;row(0).querySelector('textarea').value='别项未保存的备注';
    row(1).querySelector('details').open=true;row(1).querySelector('select').value='needs_work';
    row(1).querySelector('button').click();
    await until(()=>requests.length===2&&!document.getElementById('analyze').disabled);
    assert.equal(requests[1].index,1);assert.equal(row(0).querySelector('textarea').value,'别项未保存的备注');
    assert.equal(row(0).querySelector('details').open,true);
    const task=[...row(1).querySelectorAll('button')].find(x=>x.textContent==='生成补充任务');
    task.click();assert.match(document.getElementById('error').textContent,/尚未保存/);
    row(0).querySelector('textarea').value='';button('有文字依据 1').click();
    assert.equal(document.querySelectorAll('.review-item').length,1);assert.ok(row(0));
    button('已人工确认 1').click();assert.ok(row(2));
    assert.equal(requests.length,2);
  }finally{globalThis.fetch=originalFetch;for(const [k,v] of originals)globalThis[k]=v;dom.window.close();}
});
