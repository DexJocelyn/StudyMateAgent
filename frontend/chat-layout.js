// Layout controls never recreate the chat, so drafts and in-flight replies stay intact.
export function setupChatLayout() {
  const grid = document.querySelector('.work-grid');
  const chat = document.querySelector('.chat-panel');
  const toggle = document.getElementById('expand-chat');
  const divider = document.getElementById('chat-divider');
  const input = document.getElementById('message');
  let share = 60, expanded = false, pointer = null;
  let background = [];
  try { const saved = Number(localStorage.getItem('studymate-chat-width')); if (saved >= 40 && saved <= 70) share = saved; } catch {}

  function setShare(value, persist = false) {
    const width = grid.clientWidth - divider.offsetWidth;
    const min = width > 0 ? Math.max(40, 340 / width * 100) : 40;
    const max = width > 0 ? Math.min(70, (width - 320) / width * 100) : 70;
    const shown = min <= max ? Math.min(max, Math.max(min, value)) : 60;
    grid.style.setProperty('--chat-fr', `${shown / (100 - shown)}fr`);
    divider.setAttribute('aria-valuenow', String(Math.round(shown)));
    divider.setAttribute('aria-valuetext', `对话区占 ${Math.round(shown)}%`);
    if (persist) {
      share = Math.min(70, Math.max(40, shown));
      try { localStorage.setItem('studymate-chat-width', String(share)); } catch {}
    }
  }

  function resizeInput() {
    const bottom = input.scrollTop;
    input.style.height = 'auto';
    const style = getComputedStyle(input);
    const limit = parseFloat(style.maxHeight) || 200;
    input.style.height = `${Math.min(input.scrollHeight, limit)}px`;
    input.style.overflowY = input.scrollHeight > limit ? 'auto' : 'hidden';
    input.scrollTop = bottom;
  }

  function setExpanded(value) {
    if (value === expanded) return;
    expanded = value;
    document.body.classList.toggle('chat-expanded', expanded);
    toggle.textContent = expanded ? '收起对话' : '展开对话';
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.title = expanded ? '返回任务与对话布局（Esc）' : '放大对话，暂时收起任务面板';
    if (expanded) {
      background = [...document.querySelector('main').children].filter(e => e !== grid).map(e => [e, e.inert]);
      background.forEach(([e]) => { e.inert = true; });
      chat.setAttribute('role', 'dialog');
      chat.setAttribute('aria-modal', 'true');
    } else {
      background.forEach(([e, inert]) => { e.inert = inert; });
      background = [];
      chat.removeAttribute('role'); chat.removeAttribute('aria-modal');
      setShare(share);
      toggle.focus({preventScroll: true});
    }
    resizeInput();
  }
  toggle.onclick = () => setExpanded(!expanded);
  document.addEventListener('keydown', e => {
    if (!expanded) return;
    if (e.key === 'Escape') { e.preventDefault(); setExpanded(false); }
    if (e.key === 'Tab') {
      const controls = [...chat.querySelectorAll('button, textarea, a[href], input, select, summary, [tabindex="0"]')]
        .filter(e => !e.disabled && e.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  });
  divider.onpointerdown = e => {
    if (e.button !== 0) return;
    pointer = e.pointerId; divider.setPointerCapture(pointer);
    document.body.classList.add('resizing-chat'); e.preventDefault(); divider.focus();
  };
  divider.onpointermove = e => {
    if (pointer !== e.pointerId) return;
    const rect = grid.getBoundingClientRect();
    setShare((e.clientX - rect.left - divider.offsetWidth / 2) / (rect.width - divider.offsetWidth) * 100, true);
    resizeInput();
  };
  const release = () => { pointer = null; document.body.classList.remove('resizing-chat'); };
  divider.onlostpointercapture = release;
  divider.onpointerup = e => { if (pointer === e.pointerId) divider.releasePointerCapture(pointer); release(); };
  divider.onpointercancel = release;
  divider.ondblclick = () => { setShare(60, true); resizeInput(); };
  divider.onkeydown = e => {
    const next = {ArrowLeft: share - 2, ArrowRight: share + 2, Home: 40, End: 70}[e.key];
    if (next === undefined) return;
    e.preventDefault(); setShare(next, true); resizeInput();
  };
  input.addEventListener('input', resizeInput);
  let lastWidth = 0;
  const observer = new ResizeObserver(() => {
    if (input.clientWidth === lastWidth) return;
    lastWidth = input.clientWidth; resizeInput();
  });
  observer.observe(input);
  window.addEventListener('resize', () => { if (!expanded) setShare(share); resizeInput(); });
  setShare(share); resizeInput();
  return {resizeInput, collapse: () => setExpanded(false)};
}
