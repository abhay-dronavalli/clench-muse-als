(() => {
  if (window.top !== window || window.__clench) return;
  const ids = new WeakMap();
  let nextId = 0, elements = new Map(), root, host, timer, last = '', state = {};
  const documentId = String(Date.now()) + Math.random().toString(36).slice(2);
  const send = (message) => window.clenchBridge({ ...message, documentId }).catch(() => {});
  const labelOf = (el) => (el.getAttribute('aria-label') || el.title || el.getAttribute('alt') ||
    el.querySelector('img')?.alt || el.innerText || el.getAttribute('placeholder') ||
    el.getAttribute('name') || el.tagName).replace(/\s+/g, ' ').trim();
  const blocked = (label) => (state.blockedWords || []).some(w => label.normalize('NFKD').toLowerCase().includes(w));
  const info = (el) => {
    if (!el.isConnected || el.disabled || el.closest('[inert], [aria-hidden="true"]') ||
        el.getAttribute('aria-disabled') === 'true') return null;
    if (el.checkVisibility && !el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) return null;
    const style = getComputedStyle(el), r = el.getBoundingClientRect();
    if (style.visibility !== 'visible' || Number(style.opacity) === 0 || style.display === 'none' ||
        r.width < 8 || r.height < 8 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return null;
    const x = Math.max(0, r.left), y = Math.max(0, r.top);
    const width = Math.min(innerWidth, r.right) - x, height = Math.min(innerHeight, r.bottom) - y;
    if (width < 8 || height < 8) return null;
    const hit = document.elementFromPoint(x + width / 2, y + height / 2);
    if (!hit || (hit !== el && !el.contains(hit))) return null;
    const label = labelOf(el);
    if (!label || blocked(label)) return null;
    if (!ids.has(el)) ids.set(el, documentId + ':' + (++nextId));
    return { id: ids.get(el), label: label.slice(0, 60), x, y, width, height,
      text: el.matches('input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]), textarea, [contenteditable=true]') };
  };
  const discover = () => {
    timer = null;
    if (!document.documentElement) return;
    if (host && !host.isConnected) document.documentElement.append(host);
    const targets = [], seen = new Set();
    elements = new Map();
    for (let el of document.querySelectorAll('a,button,input,textarea,[role=button],[role=link],[tabindex],[contenteditable=true],ytd-thumbnail,#video-title')) {
      el = el.closest('a,button') || el;
      if (seen.has(el)) continue;
      seen.add(el);
      const target = info(el);
      if (target) { targets.push(target); elements.set(target.id, el); }
    }
    const payload = { targets, height: innerHeight, url: location.href };
    const signature = JSON.stringify(payload);
    if (signature !== last) { last = signature; send({ kind: 'targets', ...payload }); }
  };
  const schedule = () => { if (!timer) timer = setTimeout(discover, 180); };
  const node = (tag, text, css) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (css) Object.assign(el.style, css);
    return el;
  };
  const box = (rect, selected) => node('div', '', { position: 'fixed', left: rect.x + 'px', top: rect.y + 'px',
    width: rect.width + 'px', height: rect.height + 'px', boxSizing: 'border-box',
    border: selected ? '6px solid #ffda60' : '2px solid #63bdb3', borderRadius: '8px' });
  const render = (s) => {
    state = s;
    if (!root) return;
    root.replaceChildren();
    if (s.level === 'targets' || s.level === 'text') {
      const top = s.band * innerHeight / 4, bottom = top + innerHeight / 4;
      root.append(node('div', '', {position:'fixed', inset:'0', background:'#0006', clipPath:
        `polygon(0 0,100% 0,100% ${top}px,0 ${top}px,0 ${bottom}px,100% ${bottom}px,100% 100%,0 100%)`}));
    }
    for (const b of s.bands || []) root.append(box({x:2, y:b * innerHeight / 4 + 2, width:innerWidth - 4,
      height:innerHeight / 4 - 4}, s.level === 'bands' && s.selected === 'band:' + b));
    const target = elements.get(s.selected), rect = target && info(target);
    if (rect && !s.help) root.append(box(rect, true));
    const status = s.help ? `HELP / AYUDA · ${s.help} · Double blink to cancel` :
      'Clench = select · Double blink = back · Hold = help';
    root.append(node('div', status, {position:'fixed', top:'0', left:'0', right:'0', padding:'8px 16px',
      font:'bold 18px system-ui', color:'white', background:s.help ? '#b00020' : '#102d38'}));
    const items = s.items || [];
    const text = s.level === 'text' ? 'Search options coming next · Cancel' :
      items.map(i => (i[0] === s.selected ? '▶ ' : '') + i[1]).join('   ·   ');
    root.append(node('div', [s.message, text].filter(Boolean).join(' — '), {position:'fixed', bottom:'0', left:'0', right:'0',
      padding:'12px 18px', font:'bold 22px system-ui', color:'#ffda60', background:'#102d38'}));
    schedule();
  };
  Object.defineProperty(window, '__clench', { configurable: false, writable: false, value: Object.freeze({
    render,
    discover,
    prepare: (id) => {
      const el = elements.get(id);
      if (!el) return null;
      el.scrollIntoView({block:'nearest', inline:'nearest', behavior:'instant'});
      const target = info(el);
      if (!target) return null;
      // Keep all patient navigation in the one managed tab.
      if (el.matches('a')) el.removeAttribute('target');
      return {...target, label:labelOf(el), href:el.closest('a')?.href || '',
        exit:el.hasAttribute('data-clench-exit')};
    },
  }) });
  const boot = () => {
    host = node('div', '', {position:'fixed', inset:'0', zIndex:'2147483647', pointerEvents:'none'});
    host.id = 'clench-overlay'; root = host.attachShadow({mode:'closed'});
    document.documentElement.append(host);
    new MutationObserver(schedule).observe(document.documentElement, {subtree:true, childList:true, attributes:true, characterData:true});
    addEventListener('scroll', schedule, true); addEventListener('resize', schedule);
    // Detect SPA history changes even when they do not mutate the document.
    setInterval(schedule, 1000);
    discover(); render(state);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true}); else boot();
})();
