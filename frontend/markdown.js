// Markdown只用于助手回复；先过滤HTML，再构建有限的链接和表格布局。
import {marked} from './vendor/marked.esm.js';
import DOMPurify from './vendor/purify.es.mjs';

export function renderMarkdown(text) {
  const original = String(text ?? '');
  const fallback = () => document.createTextNode(original);
  if (!DOMPurify.isSupported) return fallback();
  try {
    const html = marked.parse(original.replace(/^[\u200B-\u200F\uFEFF]+/, ''), {gfm: true, breaks: true, async: false});
    const fragment = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','s',
        'ul','ol','li','blockquote','pre','code','hr','table','thead','tbody','tr','th','td','a'],
      ALLOWED_ATTR: ['href','title','start','align'],
      ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, RETURN_DOM_FRAGMENT: true,
    });
    for (const link of fragment.querySelectorAll('a')) {
      let href = link.getAttribute('href') || '';
      // GFM裸链接可能吞入后面的中文句子；保留句子，仅将真实URL设为链接。
      const label = link.textContent;
      try {
        if (/^https?:\/\//.test(label) && decodeURI(href) === label) {
          const boundary = label.search(/[，。；！？、（）「」【】]/);
          if (boundary !== -1) {
            href = label.slice(0, boundary); link.textContent = href;
            link.after(document.createTextNode(label.slice(boundary)));
          }
        }
      } catch { /* 无法解码时保持原文，下面继续验证链接 */ }
      let url;
      try { url = new URL(href); } catch { /* 只接受绝对网页链接 */ }
      if (!url || !['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
        link.replaceWith(...link.childNodes); continue;
      }
      link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.referrerPolicy = 'no-referrer';
    }
    for (const table of fragment.querySelectorAll('table')) {
      const scroll = document.createElement('div'); scroll.className = 'markdown-table-scroll';
      scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', '回复中的表格');
      table.replaceWith(scroll); scroll.append(table);
    }
    return fragment;
  } catch {
    return fallback();
  }
}
