
/* Static build: replay a recorded run through the live front end. */
(function () {
  const banner = document.createElement('div');
  banner.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99;' +
    'background:rgba(10,10,11,.94);border-top:1px solid #23262B;color:#8A95A1;' +
    'font:13px -apple-system,Segoe UI,Arial,sans-serif;padding:9px 16px;' +
    'display:flex;gap:14px;align-items:center';
  document.body.appendChild(banner);

  let frames = [], i = 0, timer = null, rate = 2;

  const label = document.createElement('span');
  const scrub = document.createElement('input');
  scrub.type = 'range'; scrub.min = 0; scrub.value = 0;
  scrub.style.cssText = 'flex:1;accent-color:#22D3EE';
  const play = document.createElement('button');
  play.style.cssText = 'background:#131316;color:#F2F4F6;border:1px solid #2C3038;' +
    'border-radius:4px;padding:5px 12px;font-size:13px;cursor:pointer';
  play.textContent = '▶ Play';
  const note = document.createElement('span');
  note.style.cssText = 'color:#5A6470';
  note.textContent = 'Recorded run — the live version lets you break things mid-incident.';
  banner.append(play, label, scrub, note);

  function render() {
    const f = frames[i]; if (!f) return;
    window.__responder = f.responder;
    SENSE.push(f.state);
    scrub.value = i;
    const s = Math.round(f.t);
    label.textContent = 'T+' + String(Math.floor(s / 60)).padStart(2, '0') + ':' +
                        String(s % 60).padStart(2, '0');
  }
  function start() {
    if (timer) return;
    play.textContent = '⏸ Pause';
    timer = setInterval(() => {
      i++;
      if (i >= frames.length) { i = frames.length - 1; stop(); }
      render();
    }, 1000 / rate);
  }
  function stop() { clearInterval(timer); timer = null; play.textContent = '▶ Play'; }
  play.onclick = () => (timer ? stop() : (i >= frames.length - 1 ? (i = 0, render(), start()) : start()));
  scrub.oninput = () => { stop(); i = +scrub.value; render(); };

  // The page's controls cannot change a recording; map what we can, and say
  // so for the rest instead of failing silently.
  SENSE.control = function (action, extra) {
    if (action === 'start' || action === 'resume') start();
    else if (action === 'pause') stop();
    else if (action === 'reset') { stop(); i = 0; render(); }
    else if (action === 'speed') { rate = (extra && extra.value) || 2; if (timer) { stop(); start(); } }
    else {
      note.textContent = 'That control needs the live app — this page is a recording.';
      note.style.color = '#F5A524';
      setTimeout(() => { note.style.color = '#5A6470';
        note.textContent = 'Recorded run — the live version lets you break things mid-incident.'; }, 3200);
    }
    return Promise.resolve(null);
  };

  // Serve the endpoints the pages fetch, from the recording or baked files.
  const realFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    const u = String(url);
    if (u.includes('/api/responder'))
      return Promise.resolve(new Response(JSON.stringify(window.__responder || {}),
        {headers: {'Content-Type': 'application/json'}}));
    if (u.includes('/api/state'))
      return Promise.resolve(new Response(JSON.stringify((frames[i] || {}).state || {}),
        {headers: {'Content-Type': 'application/json'}}));
    if (u.includes('/api/vision/simulate') || u.includes('/api/vision/analyse'))
      return realFetch('/data/vision-demo.json');
    if (u.includes('/api/scenarios')) return realFetch('/data/scenarios.json');
    if (u.includes('/api/control'))
      return Promise.resolve(new Response('{"ok":true}', {headers: {'Content-Type': 'application/json'}}));
    return realFetch(url, opts);
  };

  SENSE.boot = function () {
    realFetch('/data/states.json').then(r => r.json()).then(d => {
      frames = d.frames; scrub.max = frames.length - 1;
      render();
      setTimeout(start, 900);
    });
  };
})();
