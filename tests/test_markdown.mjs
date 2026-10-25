// 开发机测试：设置STUDYMATE_TEST_JSDOM为已安装jsdom的包目录；生产页面不依赖Node。
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {JSDOM} = require(process.env.STUDYMATE_TEST_JSDOM || 'jsdom');
const dom = new JSDOM('<!doctype html><body></body>', {url:'http://localhost/'});
globalThis.window = dom.window; globalThis.document = dom.window.document;
const {renderMarkdown} = await import('../frontend/markdown.js');
function render(text) { const element=document.createElement('div'); element.append(renderMarkdown(text)); return element; }

test('中文标题、加粗、列表和嵌套列表呈现为结构，不泄漏Markdown标记', () => {
  const content=render('## 小组分工\n\n先明确**负责人**。\n\n1. 分析需求\n2. 撰写报告\n   - 检查引用\n   - 核对格式');
  assert.equal(content.querySelector('h2').textContent,'小组分工');
  assert.equal(content.querySelector('strong').textContent,'负责人');
  assert.equal(content.querySelectorAll('ol>li').length,2);
  assert.equal(content.querySelectorAll('ol ul>li').length,2);
  assert.ok(!content.textContent.includes('##') && !content.textContent.includes('**'));
});

test('表格可滚动且代码块保留源码字符', () => {
  const content=render('| 成员 | 任务 |\n| --- | --- |\n| 甲 | 资料整理 |\n\n```python\nprint("**原样代码** # 注释")\n```');
  assert.equal(content.querySelectorAll('table th').length,2);
  assert.equal(content.querySelector('tbody td').textContent,'甲');
  assert.equal(content.querySelector('.markdown-table-scroll').getAttribute('role'),'region');
  assert.equal(content.querySelector('pre code').textContent,'print("**原样代码** # 注释")\n');
});

test('引用、段落、行内代码和真实井号保留含义', () => {
  const content=render('> 先确认，再保存。\n\n使用 `requirements.lock`。\n普通 C# 文字、2*3 不是标题。');
  assert.equal(content.querySelector('blockquote').textContent.trim(),'先确认，再保存。');
  assert.equal(content.querySelector('p code').textContent,'requirements.lock');
  assert.ok(content.textContent.includes('C#') && content.textContent.includes('2*3'));
  assert.ok(content.querySelector('br'));
});

test('网页引用链接可点击并隔离新窗口', () => {
  const content=render('[官方文档](https://docs.python.org/3/library/venv.html "文档")');
  const link=content.querySelector('a');
  assert.equal(link.textContent,'官方文档'); assert.equal(link.protocol,'https:');
  assert.equal(link.target,'_blank'); assert.equal(link.rel,'noopener noreferrer');
  assert.equal(link.referrerPolicy,'no-referrer'); assert.equal(link.title,'文档');
});

test('脚本、事件属性、表单、图片与DOM覆盖属性均被过滤', () => {
  const content=render('<script>alert(1)</script><img src="https://tracker.invalid/pixel" onerror="alert(1)">'+
    '<svg onload="alert(1)"></svg><form id="chat-form"><input name="message"></form>'+
    '<p onclick="alert(1)" style="display:none" id="message">正常文字</p>');
  assert.equal(content.querySelector('script,img,svg,form,input,[onclick],[onerror],[onload],[style],[id]'),null);
  assert.ok(content.textContent.includes('正常文字'));
});

test('javascript、data、凭据URL及相对链接不能执行或导航', () => {
  const content=render('[甲](javascript:alert%281%29) [乙](data:text/html,test) '+
    '[丙](https://user:password@example.com/) [丁](/api/delete) [安全](https://example.com/)');
  const links=content.querySelectorAll('a');
  assert.equal(links.length,1); assert.equal(links[0].textContent,'安全');
  assert.ok(content.textContent.includes('甲') && content.textContent.includes('丁'));
});

test('不完整标记不会使回复丢失或执行HTML', () => {
  const content=render('尚未结束的 **文字\n\n```html\n<img src=x onerror=alert(1)>');
  assert.ok(content.textContent.includes('尚未结束的'));
  assert.ok(content.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(content.querySelector('img'),null);
});

test('裸URL不吞入中文标点后的句子，显式中文路径链接仍正常', () => {
  const content=render('官网 https://www.python.org/downloads/，其最新版本在这里。\n\n[中文资料](https://example.com/中文指南)');
  const links=content.querySelectorAll('a');
  assert.equal(links[0].href,'https://www.python.org/downloads/');
  assert.equal(links[0].textContent,'https://www.python.org/downloads/');
  assert.ok(content.textContent.includes('，其最新版本在这里。'));
  assert.equal(decodeURI(links[1].href),'https://example.com/中文指南');
});
