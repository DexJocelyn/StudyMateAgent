// 识别文字只进入编辑框；暂停会结束本次浏览器识别，继续时新开一段。
export function createSpeech({onTranscript, onError, onState}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null, phase = 'idle', interim = '', reason = '';
  let activeMs = 0, startedAt = null, timer = null, startupTimer = null, settleTimer = null;
  const elapsed = () => activeMs + (startedAt === null ? 0 : Date.now() - startedAt);
  const notify = () => onState({phase, interim, reason, elapsedSeconds: Math.floor(elapsed() / 1000)});
  function clearTimers() {
    clearInterval(timer); clearTimeout(startupTimer); clearTimeout(settleTimer);
    timer = startupTimer = settleTimer = null;
  }
  function freezeClock() { activeMs = elapsed(); startedAt = null; }
  function finish(rec, target, endReason, keepInterim = true) {
    if (rec !== recognition || !rec) return;
    freezeClock(); clearTimers(); recognition = null;
    // stop() 通常会返回最终结果；未返回时保留屏幕上已有的临时文字供校正。
    if (keepInterim && interim.trim()) onTranscript(interim.trim());
    interim = ''; phase = target; reason = endReason; notify();
  }
  function requestEnd(target, endReason) {
    if (!recognition) {
      if (phase === 'paused' && target === 'finished') { phase = target; reason = endReason; notify(); }
      return;
    }
    if (phase === 'stopping' || phase === 'pausing') return;
    const rec = recognition;
    rec.endTarget = target; rec.endReason = endReason;
    const wasStarting = phase === 'starting';
    freezeClock(); clearTimers(); phase = target === 'paused' ? 'pausing' : 'stopping'; notify();
    if (wasStarting) {
      finish(rec, target, endReason);
      try { rec.abort(); } catch {}
      return;
    }
    // 个别浏览器不发 end；等待最后结果后强制结束，避免按钮永远卡住。
    settleTimer = setTimeout(() => {
      finish(rec, target, endReason);
      try { rec.abort(); } catch {}
    }, 2500);
    try { rec.stop(); } catch {
      finish(rec, target, endReason);
      try { rec.abort(); } catch {}
    }
  }
  const stop = () => requestEnd('finished', 'stopped');
  const pause = () => requestEnd('paused', 'paused');
  function cancel() {
    const rec = recognition;
    recognition = null; clearTimers(); startedAt = null; activeMs = 0;
    phase = 'idle'; interim = ''; reason = ''; notify();
    try { rec?.abort(); } catch {}
    window.speechSynthesis?.cancel();
  }
  function start() {
    if (recognition) return;
    if (!Recognition) {
      phase = 'error'; reason = 'unsupported'; notify();
      onError('此浏览器不支持语音识别，请使用文字输入。'); return;
    }
    window.speechSynthesis?.cancel();
    if (phase !== 'paused') activeMs = 0;
    let rec;
    try { rec = new Recognition(); } catch {
      phase = 'error'; reason = 'unavailable'; notify(); onError('无法启动麦克风。'); return;
    }
    recognition = rec; interim = ''; phase = 'starting'; reason = ''; notify();
    const committed = new Set();
    rec.lang = 'zh-CN'; rec.continuous = true; rec.interimResults = true;
    rec.onstart = () => {
      if (recognition !== rec) { try { rec.abort(); } catch {} return; }
      if (phase !== 'starting') return;
      clearTimeout(startupTimer); phase = 'listening'; startedAt = Date.now(); notify();
      timer = setInterval(() => { if (elapsed() >= 60000) requestEnd('finished', 'limit'); else notify(); }, 1000);
    };
    rec.onresult = e => {
      if (recognition !== rec) return;
      const finals = [], pending = [];
      Array.from(e.results).forEach((result, i) => {
        const text = result[0]?.transcript || '';
        if (result.isFinal) {
          if (!committed.has(i)) { committed.add(i); if (text.trim()) finals.push(text.trim()); }
        } else pending.push(text);
      });
      interim = pending.join('');
      if (finals.length) onTranscript(finals.join(''));
      notify();
    };
    rec.onerror = e => {
      if (recognition !== rec) return;
      if (e.error === 'aborted' && rec.endTarget) { finish(rec, rec.endTarget, rec.endReason); return; }
      finish(rec, 'error', e.error);
      try { rec.abort(); } catch {}
      onError(({'not-allowed': '麦克风权限被拒绝，请在浏览器设置中允许后重试。',
        'audio-capture': '未找到可用麦克风，请检查设备连接。', 'no-speech': '没有识别到语音，已停止，请重试。',
        network: '语音识别服务网络不可用，已停止，请使用文字输入。', aborted: '语音输入已取消。'})[e.error] || '语音识别失败，已停止，请重试。');
    };
    rec.onend = () => finish(rec, rec.endTarget || 'finished', rec.endReason || 'browser-ended');
    startupTimer = setTimeout(() => {
      finish(rec, 'error', 'start-timeout');
      try { rec.abort(); } catch {}
      onError('麦克风未能启动，已取消。请检查浏览器权限后重试。');
    }, 15000);
    try { rec.start(); } catch {
      finish(rec, 'error', 'unavailable');
      try { rec.abort(); } catch {}
      onError('无法启动麦克风，请检查权限后重试。');
    }
  }
  function speak(text) {
    if (!window.speechSynthesis) { onError('此浏览器不支持朗读。'); return; }
    if (recognition) { onError('请先停止语音输入，再朗读回复。'); return; }
    window.speechSynthesis.cancel();
    const speech = new SpeechSynthesisUtterance(text);
    speech.lang = 'zh-CN'; speech.rate = 1;
    const voice = window.speechSynthesis.getVoices().find(v => v.lang.startsWith('zh'));
    if (voice) speech.voice = voice;
    window.speechSynthesis.speak(speech);
  }
  window.addEventListener('pagehide', cancel);
  return {supported: !!Recognition, start, stop, pause, cancel, speak, stopSpeaking: () => window.speechSynthesis?.cancel()};
}
