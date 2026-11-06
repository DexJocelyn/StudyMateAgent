import test from 'node:test';
import assert from 'node:assert/strict';
import {selectProject,projectLink,deliveryStatus} from '../frontend/project-context.js';
const projects=[{id:'project_a'},{id:'project_b'}];
test('跨模块链接明确指定项目，优先于设备上次选择',()=>{
  assert.equal(selectProject(projects,'?project_id=project_b','project_a'),'project_b');
  assert.equal(selectProject(projects,'','project_b'),'project_b');
  assert.throws(()=>selectProject(projects,'?project_id=missing','project_a'));
});
test('工作台与交付环节携带同一项目，支持返回并定位补充任务',()=>{
  const delivery=new URL(projectLink('/review','project_b',{step:'results'}),'http://localhost');
  assert.equal(delivery.searchParams.get('project_id'),'project_b');
  assert.equal(delivery.searchParams.get('step'),'results');
  const back=new URL(projectLink('/','project_b',{task_id:'task_1'}),'http://localhost');
  assert.equal(back.searchParams.get('task_id'),'task_1');
  assert.equal(selectProject(projects,back.search,'project_a'),'project_b');
});
test('交付状态区分未录入、过期、人工需补充和未完成任务，不冒充合格率',()=>{
  assert.match(deliveryStatus({instructions_saved:false}),/保存/);
  assert.match(deliveryStatus({instructions_saved:true,stale:true,latest_run_id:'x'}),/过期/);
  const text=deliveryStatus({instructions_saved:true,latest_run_id:'x',stale:false,item_count:5,unreviewed:2,needs_work:1,followups_open:0});
  assert.match(text,/需补充1项/);assert.match(text,/待完成0项/);assert.ok(!text.includes('合格'));
});
