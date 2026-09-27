// Runs in a named Chromium isolated world. Site JavaScript cannot call this binding or
// synthesize trusted key events. The ordinary page bridge never accepts patient gestures.
(() => {
  if (window.top !== window) return;
  let longClenchMs = 2500, pressed = false, holdTimer, longSent = false;
  const send = (event, extra={}) => clenchTrustedInput(JSON.stringify({event,...extra}));
  let helpActive = false;
  globalThis.clenchInputSettings = s => { helpActive = s.help != null; longClenchMs = s.longClenchMs || 2500; globalThis.clenchControls?.(s); };

  addEventListener('keydown', e => {
    if (e.code === 'Escape' && globalThis.clenchCalibrating && !helpActive) return;
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

})();
