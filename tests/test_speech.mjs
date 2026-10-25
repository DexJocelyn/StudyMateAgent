// 浏览器事件受控回归；不使用真实麦克风，不宣称语音服务实机验收。
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../frontend/speech.js', import.meta.url), 'utf8');
function fixture({supported = true, throws = false} = {}) {
  let clock = 1000, id = 0;
  const timers = new Map(), instances = [], states = [], texts = [], errors = [];
  class Recognition {
    constructor() { instances.push(this); }
    start() { if (throws) throw new Error('cannot start'); }
    stop() { this.stops = (this.stops || 0) + 1; }
    abort() { this.aborts = (this.aborts || 0) + 1; this.onerror?.({error:'aborted'}); this.onend?.(); }
  }
  const context = vm.createContext({window: {SpeechRecognition: supported ? Recognition : null,
    speechSynthesis: {cancel() {}}, addEventListener() {}},
    Date: {now: () => clock},
    setTimeout: (fn, delay) => { const key = ++id; timers.set(key, {fn, delay, repeat:false}); return key; },
    setInterval: (fn, delay) => { const key = ++id; timers.set(key, {fn, delay, repeat:true}); return key; },
    clearTimeout: key => timers.delete(key), clearInterval: key => timers.delete(key)});
  vm.runInContext(source.replace('export function createSpeech', 'function createSpeech'), context);
  const speech = context.createSpeech({onTranscript: text => texts.push(text), onError: text => errors.push(text),
    onState: state => states.push({...state})});
  return {speech, states, texts, errors, timers, instances,
    current: () => states.at(-1), rec: () => instances.at(-1),
    advance(ms) { clock += ms; },
    fire(delay) { const entry = [...timers].find(([,timer]) => timer.delay === delay); assert.ok(entry); const [key,timer] = entry; if (!timer.repeat) timers.delete(key); timer.fn(); },
    result(items) { const results = items.map(([text, isFinal]) => Object.assign([{transcript:text}], {isFinal})); this.rec().onresult({results}); }};
}

test('真实 start 事件才显示聆听，临时文本可见，最终文本不重复追加', () => {
  const f = fixture(); f.speech.start(); assert.equal(f.current().phase, 'starting');
  f.rec().onstart(); assert.equal(f.current().phase, 'listening');
  assert.equal(f.rec().continuous, true); assert.equal(f.rec().interimResults, true);
  f.result([['给我',false]]); assert.equal(f.current().interim, '给我'); assert.deepEqual(f.texts, []);
  f.result([['给我安排任务',true]]); f.result([['给我安排任务',true],['明天',false]]);
  assert.deepEqual(f.texts, ['给我安排任务']); assert.equal(f.current().interim, '明天');
  f.speech.stop(); assert.equal(f.current().phase, 'stopping');
  f.result([['给我安排任务',true],['明天完成',true]]); f.rec().onend();
  assert.equal(f.current().phase, 'finished'); assert.deepEqual(f.texts, ['给我安排任务','明天完成']);
  assert.equal(f.timers.size, 0);
});

test('暂停保留文字并冻结计时，继续用新识别段，停止后可结束', () => {
  const f = fixture(); f.speech.start(); f.rec().onstart(); f.advance(3000);
  f.result([['第一段',true]]); f.speech.pause(); const previous = f.rec(); previous.onend();
  assert.equal(f.current().phase, 'paused'); assert.equal(f.current().elapsedSeconds, 3);
  f.advance(15000); f.speech.start(); assert.notEqual(f.rec(), previous); f.rec().onstart();
  f.advance(2000); f.fire(1000); assert.equal(f.current().elapsedSeconds, 5);
  f.result([['第二段',true]]); f.speech.pause(); f.rec().onend(); f.speech.stop();
  assert.equal(f.current().phase, 'finished'); assert.deepEqual(f.texts, ['第一段','第二段']);
});

test('浏览器没有 end 回调时也能结束并保留临时文字', () => {
  const f = fixture(); f.speech.start(); f.rec().onstart(); f.result([['未确认文字',false]]);
  const rec = f.rec(); f.speech.stop(); f.fire(2500);
  assert.equal(f.current().phase, 'finished'); assert.equal(rec.aborts, 1);
  assert.deepEqual(f.texts, ['未确认文字']); assert.equal(f.timers.size, 0);
});

test('切换成员取消后，旧识别结果不能进入新输入框', () => {
  const f = fixture(); f.speech.start(); const old = f.rec(); old.onstart();
  f.result([['旧成员临时文字',false]]); f.speech.cancel();
  f.speech.start(); f.rec().onstart();
  old.onresult({results:[Object.assign([{transcript:'延迟旧文字'}],{isFinal:true})]}); old.onend();
  assert.equal(f.current().phase, 'listening'); assert.deepEqual(f.texts, []);
  f.speech.cancel(); assert.equal(f.timers.size, 0);
});

test('启动中就停止，不会等到晚到的 start 又恢复聆听', () => {
  const f = fixture(); f.speech.start(); const rec = f.rec(); f.speech.stop(); rec.onstart();
  assert.equal(f.current().phase, 'finished'); assert.equal(f.timers.size, 0);
  assert.deepEqual(f.errors, []);
});

test('计时达到上限后主动停止；自然结束和权限失败都有终态', () => {
  const f = fixture(); f.speech.start(); f.rec().onstart(); f.advance(60000); f.fire(1000);
  assert.equal(f.current().phase, 'stopping'); f.rec().onend();
  assert.equal(f.current().reason, 'limit'); assert.equal(f.current().elapsedSeconds, 60);
  f.speech.start(); f.rec().onstart(); f.rec().onend(); assert.equal(f.current().reason,'browser-ended');
  f.speech.start(); f.rec().onerror({error:'not-allowed'}); f.rec().onend();
  assert.equal(f.current().phase, 'error'); assert.match(f.errors.at(-1), /权限被拒绝/); assert.equal(f.timers.size, 0);
});

test('不支持、启动异常和启动超时都能恢复可重试状态', () => {
  const unsupported = fixture({supported:false}); unsupported.speech.start(); assert.equal(unsupported.current().phase,'error');
  const failed = fixture({throws:true}); failed.speech.start(); assert.equal(failed.current().phase,'error'); assert.equal(failed.timers.size, 0);
  const slow = fixture(); slow.speech.start(); slow.fire(15000); assert.equal(slow.current().reason,'start-timeout'); assert.equal(slow.timers.size, 0);
});
