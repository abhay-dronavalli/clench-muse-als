(() => {
  if (window.top !== window || window.__clench) return;
  const ids = new WeakMap();
  let nextId = 0, elements = new Map(), root, host, timer, last = '', state = {};
  let ui, frame;
  // Policy is installed by the browser adapter before any page scripts run.
  // Replaying an empty overlay during navigation must not clear it.
  const blockedWords=Object.freeze([...(state.blockedWords||[])]);
  const documentId = String(Date.now()) + Math.random().toString(36).slice(2);
  const send = message => window.clenchBridge({...message, documentId}).catch(() => {});
  const dockHeight = () => innerWidth < 600 ? 132 : 108;
  const pageBottom = () => innerHeight;
  const cardOf = el => el.closest('ytd-rich-item-renderer,ytd-video-renderer,ytd-grid-video-renderer');
  const sameVideo = (a,b) => {
    try { const x=new URL(a.href),y=new URL(b.href);
      return x.origin===y.origin && x.pathname===y.pathname && x.searchParams.get('v')===y.searchParams.get('v');
    } catch { return false; }
  };
  const labelOf = el => {
    const card = cardOf(el);
    const title = el.matches('ytd-thumbnail,a#thumbnail') ? card?.querySelector('#video-title') : null;
    return (title?.getAttribute('title') || title?.textContent || el.getAttribute('aria-label') ||
      el.title || el.getAttribute('alt') || el.querySelector('img')?.alt || el.innerText ||
      el.getAttribute('placeholder') || el.getAttribute('name') || '').replace(/\s+/g, ' ').trim();
  };
  const blocked = label => blockedWords.some(w => label.normalize('NFKD').toLowerCase().includes(w));
  const actionLabel = el => {
    const title=el.matches('ytd-thumbnail,a#thumbnail') ?
      cardOf(el)?.querySelector('#video-title') : null;
    return [el,title].filter(Boolean).flatMap(e=>[e.getAttribute('aria-label'),e.title,e.getAttribute('alt'),e.innerText,labelOf(e)]).filter(Boolean).join(' ');
  };
  const info = el => {
    // YouTube marks visible thumbnail links aria-hidden to avoid announcing the
    // title twice. Allow that one link; hidden ancestors still exclude it.
    const thumbnail=el.matches('a#thumbnail') && cardOf(el)?.querySelector('#video-title');
    const hidden=el.parentElement?.closest('[aria-hidden="true"]') ||
      (el.getAttribute('aria-hidden')==='true' && !thumbnail);
    if (!el.isConnected || el.disabled || el.closest('[inert]') || hidden ||
        el.getAttribute('aria-disabled') === 'true') return null;
    if (el.checkVisibility && !el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) return null;
    const style = getComputedStyle(el), r = el.getBoundingClientRect();
    if (style.visibility !== 'visible' || Number(style.opacity) === 0 || style.display === 'none' ||
        r.width < 8 || r.height < 8 || r.bottom <= 0 || r.right <= 0 || r.top >= pageBottom() || r.left >= innerWidth) return null;
    const x = Math.max(0, r.left), y = Math.max(0, r.top);
    const width = Math.min(innerWidth, r.right) - x, height = Math.min(pageBottom(), r.bottom) - y;
    if (width < 8 || height < 8) return null;
    const hit = document.elementFromPoint(x + width / 2, y + height / 2);
    if (!hit || (hit !== el && !el.contains(hit))) return null;
    const label = labelOf(el);
    if (!label || blocked(actionLabel(el))) return null;
    if (!ids.has(el)) ids.set(el, documentId + ':' + (++nextId));
    const card=cardOf(el)?.querySelector('a#thumbnail')?.getBoundingClientRect();
    const band_y=card && card.height>=8 ? (Math.max(0,card.top)+Math.min(innerHeight,card.bottom))/2 : null;
    return {id:ids.get(el), label:label.slice(0,60), x,y,width,height,
      band_y,
      text:el.matches('input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable=true]')};
  };
  const discover = () => {
    timer = null;
    if (!document.documentElement) return;
    if (host && !host.isConnected) document.documentElement.append(host);
    const targets = [], seen = new Set(), duplicate = new Set();
    elements = new Map();
    for (let el of document.querySelectorAll('a,button,input,textarea,[role=button],[role=link],[tabindex],[contenteditable=true],ytd-thumbnail,#video-title')) {
      if (el.matches('ytd-thumbnail')) el=el.querySelector('a#thumbnail')||el;
      el = el.closest('a,button,[role=button],[role=link]') || el;
      if (el.matches('#video-title')) {
        const thumbnail=cardOf(el)?.querySelector('a#thumbnail');
        if (thumbnail && sameVideo(thumbnail,el) && info(thumbnail) && !blocked(actionLabel(el))) el=thumbnail;
      }
      if (seen.has(el) || el === host) continue;
      seen.add(el);
      const target = info(el);
      if (!target) continue;
      const signature = [target.label,...['x','y','width','height'].map(k=>Math.round(target[k]))].join('|');
      if (duplicate.has(signature)) continue;
      duplicate.add(signature);
      targets.push(target); elements.set(target.id,el);
    }
    const payload = {targets,height:pageBottom(),url:location.href};
    const signature = JSON.stringify(payload);
    if (signature !== last) {last=signature;send({kind:'targets',...payload});}
    paintSoon();
  };
  const schedule = () => {if (!timer) timer=setTimeout(discover,180);};
  const node = (tag,text,css) => {
    const el=document.createElement(tag);
    if(text) el.textContent=text;
    if(css) Object.assign(el.style,css);
    return el;
  };
  const rectStyle = (el,r) => Object.assign(el.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px'});
  const bounds = rects => {
    if(!rects.length) return null;
    const x=Math.max(3,Math.min(...rects.map(r=>r.x))-5), y=Math.max(3,Math.min(...rects.map(r=>r.y))-5);
    return {x,y,width:Math.max(0,Math.min(innerWidth-3,Math.max(...rects.map(r=>r.x+r.width))+5)-x),
      height:Math.max(0,Math.min(pageBottom()-3,Math.max(...rects.map(r=>r.y+r.height))+5)-y)};
  };
  const buildUI = () => {
    const style=node('style');
    style.textContent=`
      :host { all:initial; color-scheme:dark; }
      * { box-sizing:border-box; }
      .outline { position:fixed; border:4px solid #ffda60; border-radius:10px; box-shadow:0 0 0 2px #142b38; }
      .peer { position:fixed; border:2px solid #6caea8; border-radius:7px; }
      .dock { position:fixed; inset:auto 0 0; height:108px; background:#142b38; color:#f5fafb;
        border-top:2px solid #55717d; padding:10px 20px; font:16px/1.3 'Segoe UI',sans-serif; }
      .line { display:flex; align-items:center; gap:16px; min-width:0; }
      .title { font-size:23px; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; flex:1; }
      .step { color:#bdd0d8; flex:none; font-size:15px; }
      .choices { display:flex; gap:8px; flex:1; min-width:0; }
      .choice { padding:5px 12px; border:1px solid #6c8792; border-radius:7px; white-space:nowrap; }
      .chosen { color:#142b38; background:#ffda60; border-color:#ffda60; font-weight:700; }
      .hint { margin-top:5px; color:#c2d4db; font-size:15px; }
      .panel { position:fixed; width:min(520px,calc(100% - 32px)); left:50%; top:40%; transform:translate(-50%,-50%);
        background:#142b38; color:#f5fafb; border:2px solid #6c8792; border-radius:18px; padding:24px;
        box-shadow:0 12px 60px #0008; font:22px/1.4 'Segoe UI',sans-serif; }
      .panel-title { font-size:28px; font-weight:650; margin-bottom:16px; }
      .option { padding:10px 16px; margin:6px 0; border:2px solid transparent; border-radius:8px; }
      .panel.search { top:45%; max-height:calc(100vh - 150px); overflow:auto; width:min(760px,calc(100% - 32px)); }
      .search .option { padding:7px 12px; margin:2px 0; font-size:20px; }
      .draft { padding:10px 14px; margin-bottom:10px; background:#081e2b; border:1px solid #6c8792;
        border-radius:8px; font:24px/1.4 'Segoe UI',sans-serif; overflow-wrap:anywhere; }
      .keyboard .option-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; }
      .keyboard.letters .option-list { grid-template-columns:repeat(3,minmax(0,1fr)); }
      .keyboard .completion { grid-column:1 / -1; border-color:#6c8792; }
      .keyboard .option { white-space:normal; }
      @media(max-width:450px) { .keyboard .option-list { grid-template-columns:1fr; } }
      .help { position:fixed; top:12px; left:12px; right:12px; border:3px solid white; padding:18px;
        background:#ae1737; color:white; border-radius:12px; text-align:center; font:700 26px/1.4 'Segoe UI',sans-serif; }
      .message { position:fixed; bottom:120px; left:16px; right:16px; padding:12px 18px;
        background:#142b38; color:#ffda60; border:2px solid #ffda60; border-radius:10px; font:20px/1.4 'Segoe UI',sans-serif; }
      @media(max-width:599px) { .dock{height:132px;padding:8px 12px}.line{gap:8px}.title{font-size:20px}
        .choice{padding:5px 7px;font-size:14px}.step{font-size:13px}.hint{font-size:13px}.message{bottom:144px} }
      @media(max-height:600px) { .panel{padding:12px;font-size:18px;top:38%}
        .panel-title{font-size:22px;margin-bottom:6px}.option{padding:6px 12px;margin:2px 0} }
    `;
    const make=(cls,text)=>{const el=node('div',text);el.className=cls;return el;};
    const shade=node('div','',{position:'fixed',inset:'0',background:'#07141d55'});
    const peers=Array.from({length:8},()=>make('peer')), outline=make('outline');
    const dock=make('dock'), line=make('line'), title=make('title'), step=make('step');
    const row=make('line');row.style.marginTop='7px';
    const choices=make('choices'), hint=make('hint','Clench: select  |  Double blink: back  |  Hold: help');
    const chips=Array.from({length:5},()=>make('choice'));
    choices.append(...chips);line.append(title,step);row.append(choices);dock.append(line,row,hint);
    const panel=make('panel'), panelTitle=make('panel-title'), draft=make('draft'), optionList=make('option-list');
    const options=Array.from({length:9},()=>make('option'));
    optionList.append(...options);panel.append(panelTitle,draft,optionList);
    const help=make('help'), message=make('message');
    root.append(style,shade,...peers,outline,dock,panel,message,help);
    ui={shade,peers,outline,dock,title,step,row,chips,panel,panelTitle,draft,options,help,message,hint};
  };
  const paint = () => {
    frame=null;
    if(!ui)return;
    const s=state,items=s.items||[],index=items.findIndex(i=>i[0]===s.selected);
    const picked=items[index]?.[1]||'Finding page controls...';
    const inTargets=s.level==='targets'||s.level==='text';
    const band=inTargets?s.band:Number(String(s.selected).split(':')[1]);
    const group=(s.groups?.[band]||[]).map(id=>elements.get(id)).filter(Boolean).map(info).filter(Boolean);
    const region=bounds(group),rect=elements.has(s.selected)&&info(elements.get(s.selected));
    const active=inTargets?rect&&bounds([rect]):s.level==='bands'?region:null;
    // Move the dock away from bottom controls, including fixed video controls that
    // scrolling cannot reveal. Target discovery always includes the whole viewport.
    const dockTop=active && active.y > dockHeight()+8 && active.y+active.height > innerHeight-dockHeight();
    ui.dock.style.top=dockTop?'0':'auto';ui.dock.style.bottom=dockTop?'auto':'0';
    ui.message.style.top=dockTop?(dockHeight()+12)+'px':'auto';
    ui.message.style.bottom=dockTop?'auto':(dockHeight()+12)+'px';
    ui.outline.style.display=active&&!s.help&&!s.busy?'block':'none';
    if(active)rectStyle(ui.outline,active);
    ui.shade.style.display=inTargets&&region&&!s.help?'block':'none';
    if(region){const{x,y,width:w,height:h}=region;
      ui.shade.style.clipPath=`polygon(0 0,100% 0,100% 100%,0 100%,0 0,${x}px ${y}px,${x}px ${y+h}px,${x+w}px ${y+h}px,${x+w}px ${y}px,${x}px ${y}px)`;}
    ui.peers.forEach((el,i)=>{
      const item=items[i],target=item&&elements.get(item[0]),r=target&&info(target);
      el.style.display=inTargets&&r&&item[0]!==s.selected&&!s.help?'block':'none';
      if(r)rectStyle(el,r);
    });
    ui.title.textContent=s.busy?'Opening selection...':s.level==='bands'?`Choose a group: ${picked}`:picked;
    ui.step.textContent=s.level==='bands'?'Step 1 of 2':s.level==='targets'?`Choice ${index+1} of ${items.length}${s.page?' / Page '+(s.page+1):''}`:'';
    const bandItems=s.level==='bands'?items:[];
    ui.row.style.display=bandItems.length?'flex':'none';
    ui.chips.forEach((el,i)=>{const item=bandItems[i];el.style.display=item?'block':'none';
      el.textContent=item?.[1]||'';el.className='choice'+(item?.[0]===s.selected?' chosen':'');});
    const searching=['search','keyboard'].includes(s.level);
    const keyboard=s.level==='keyboard';
    ui.panel.className='panel'+(searching?' search':'')+(keyboard?' keyboard':'')+(keyboard&&s.keyboardRow!=null?' letters':'');
    ui.panel.style.display=['menu','text','search','keyboard'].includes(s.level)&&!s.help?'block':'none';
    ui.panelTitle.textContent=s.level==='search'?(s.lang==='es'?'Buscar':'Search'):
      keyboard?(s.lang==='es'?'Teclado':'Keyboard'):s.level==='text'?'Search options coming next':'Browser menu';
    ui.draft.style.display=keyboard?'block':'none';
    ui.draft.textContent=s.draft|| (s.lang==='es'?'Escribe tu búsqueda…':'Type your search…');
    ui.hint.textContent=keyboard?(s.lang==='es'?'Elige fila y luego letra. Doble parpadeo: volver. Mantener: ayuda.':
      'Choose a row, then a letter. Double blink: search options. Hold: help.'):'Clench: select  |  Double blink: back  |  Hold: help';
    ui.options.forEach((el,i)=>{const item=items[i];el.style.display=item?'block':'none';
      el.textContent=item?.[1]||'';el.className='option'+(item?.[0]===s.selected?' chosen':'')+(String(item?.[0]).startsWith('complete:')?' completion':'');});
    if(searching) ui.options[index]?.scrollIntoView({block:'nearest'});
    ui.help.style.display=s.help!=null?'block':'none';ui.help.textContent=`Help / Ayuda: ${s.help}     Double blink to cancel`;
    ui.message.style.display=s.message?'block':'none';ui.message.textContent=s.message||'';
  };
  const paintSoon=()=>{if(!frame)frame=requestAnimationFrame(paint);};
  const render=s=>{state=s;paintSoon();};
  Object.defineProperty(window,'__clench',{configurable:false,writable:false,value:Object.freeze({
    render,discover,
    field:id=>{const el=elements.get(id);return el && info(el)?.text ? el : null;},
    prepare:id=>{
      const el=elements.get(id);if(!el)return null;
      el.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
      const target=info(el);if(!target)return null;
      if(el.matches('a'))el.removeAttribute('target');
      return {...target,label:actionLabel(el),href:el.closest('a')?.href||'',exit:el.hasAttribute('data-clench-exit')};
    },
  })});
  const boot=()=>{
    host=node('div','',{position:'fixed',inset:'0',zIndex:'2147483647',pointerEvents:'none'});
    host.id='clench-overlay';root=host.attachShadow({mode:'closed'});
    document.documentElement.append(host);buildUI();
    new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
    addEventListener('scroll',()=>{paintSoon();schedule();},true);
    addEventListener('resize',()=>{paintSoon();schedule();});
    setInterval(schedule,1000);discover();render(state);
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
