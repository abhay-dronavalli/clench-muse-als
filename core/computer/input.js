// Runs in a named Chromium isolated world. Site JavaScript cannot call this binding or
// synthesize trusted key events. The ordinary page bridge never accepts patient gestures.
(() => {
  if (window.top !== window) return;
  let longClenchMs = 2500, pressed = false, holdTimer, longSent = false;
  const send = (event, extra={}) => clenchTrustedInput(JSON.stringify({event,...extra}));
  globalThis.clenchInputSettings = s => { longClenchMs = s.longClenchMs || 2500; state=s; paint(); };
  let state={}, ui;

  addEventListener('keydown', e => {
    if (!e.isTrusted || !['Space','KeyB','Escape','Backspace','Backquote'].includes(e.code)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.repeat) return;
    if (e.code === 'Backquote') { send('DEV_TOGGLE'); return; }
    if (e.code !== 'Space') { send('DOUBLE_BLINK'); return; }
    pressed = true; longSent = false;
    holdTimer = setTimeout(() => { longSent = true; send('LONG_CLENCH'); }, longClenchMs);
  }, true);
  addEventListener('keyup', e => {
    if (!e.isTrusted || e.code !== 'Space') return;
    e.preventDefault(); e.stopImmediatePropagation(); clearTimeout(holdTimer);
    if (pressed && !longSent) send('CLENCH');
    pressed = false;
  }, true);
  addEventListener('blur', () => { clearTimeout(holdTimer); pressed = false; });

  const build=()=>{
    const host=document.createElement('div');host.id='clench-dev';
    Object.assign(host.style,{position:'fixed',inset:'0',zIndex:'2147483647',pointerEvents:'none'});
    const root=host.attachShadow({mode:'closed'});document.documentElement.append(host);
    const style=document.createElement('style');style.textContent=`
      :host{all:initial;color-scheme:dark} *{box-sizing:border-box}
      button,select,input{font:inherit} button,select{color:#f3fafb;background:#254858;border:1px solid #8da5af;border-radius:7px;padding:8px;cursor:pointer}
      .pill{position:fixed;right:12px;top:12px;pointer-events:auto;font:16px 'Segoe UI',sans-serif}
      .panel{position:fixed;right:12px;top:58px;width:min(430px,calc(100vw - 24px));max-height:calc(100vh - 74px);overflow:auto;pointer-events:auto;
        padding:18px;border:2px solid #91aeb9;border-radius:14px;background:#142b38;color:#f4fafb;font:16px/1.45 'Segoe UI',sans-serif;box-shadow:0 10px 40px #0009}
      h2{font-size:24px;margin:0 0 8px} .row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:12px 0} .actions{display:flex;flex-wrap:wrap;gap:8px}
      input[type=range]{width:180px} .status{padding:10px;background:#081e2b;border-radius:8px} small{display:block;color:#c3d6df;margin-top:12px}
    `;root.append(style);
    const el=(tag,text,parent)=>{const e=document.createElement(tag);if(text)e.textContent=text;parent?.append(e);return e;};
    const button=(text,parent,fn)=>{const e=el('button',text,parent);e.type='button';e.addEventListener('click',event=>{if(event.isTrusted)fn();});return e;};
    const pill=button('Dev (`)',root,()=>send('DEV_TOGGLE'));pill.className='pill';
    const panel=el('section','',root);panel.className='panel';panel.setAttribute('aria-label','Clench developer panel');
    el('h2','Computer controls',panel);const status=el('div','',panel);status.className='status';
    const actions=el('div','',panel);actions.className='actions';
    button('Clench',actions,()=>send('CLENCH'));button('Back',actions,()=>send('DOUBLE_BLINK'));
    button('Help (hold)',actions,()=>send('LONG_CLENCH'));button('Home / Exit',actions,()=>send('RESET'));
    const controls={};
    const setting=(key,label,type,options)=>{
      const row=el('label','',panel);row.className='row';const caption=el('span',label,row);
      const input=el(type==='select'?'select':'input','',row);controls[key]={input,caption,label};
      if(type==='select')for(const [value,text] of options){const option=el('option',text,input);option.value=value;}
      else {input.type=type;if(type==='range'){[input.min,input.max,input.step]=options;}}
      input.addEventListener('change',e=>{if(!e.isTrusted)return;let value=type==='checkbox'?input.checked:type==='range'?Number(input.value):input.value;send('SETTINGS',{patch:{[key]:value}});});
    };
    setting('pointing_mode','Pointing','select',[['auto','Auto'],['scan','Scan'],['webcam','Webcam / head'],['gaze','Gaze / eye tracker'],['headtilt','Head tilt (scan fallback)']]);
    setting('scan_ms','Scan speed','range',['300','3000','100']);
    setting('lang','Language','select',[['en','English'],['es','Español']]);
    setting('speak_picks','Speak picks','checkbox');setting('learning','Learning','checkbox');
    setting('muse_enabled','Muse input','checkbox');
    setting('long_clench_ms','Help hold','range',['1000','5000','100']);
    setting('tile_switch_margin','Sticky edges','range',['0','0.2','0.01']);
    el('small','Gaze needs a connected eye tracker in the board. Webcam follows head turns. Keep the board open on this display for tracking and audio. Calibrate head range in the board Dev panel.',panel);
    button('Close (`)',panel,()=>send('DEV_TOGGLE'));
    ui={panel,status,controls};paint();
  };
  const paint=()=>{
    if(!ui)return;
    ui.panel.hidden=!state.devOpen || state.help!=null;
    const descriptions={tracking:'Tracking',no_tracker:'No eye tracker connected',lost:'Tracking lost',camera_error:'Camera unavailable: check the board',starting:'Waiting for tracker',off:'Tracking off'};
    ui.status.textContent=`Highlight: ${state.pointer||'scan'} | ${descriptions[state.trackingStatus]||'Waiting for board'}${state.faceOk?' | Person seen':''}`;
    for(const [key,{input,caption,label}] of Object.entries(ui.controls)){
      const value=state.settings?.[key];if(value==null)continue;
      if(input.type==='checkbox')input.checked=!!value;else if(input!==input.getRootNode().activeElement)input.value=String(value);
      caption.textContent=label+(input.type==='range'?` (${key==='tile_switch_margin'?Math.round(value*100)+'%':value+' ms'})`:'');
    }
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build,{once:true});else build();
})();
