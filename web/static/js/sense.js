/* =====================================================================
   SENSE AI — shared client runtime.

   One WebSocket, one state object, many subscribers. Pages register a
   render callback and never poll; if the socket drops the client falls
   back to /api/state so a demo cannot die on a flaky connection.
   ===================================================================== */
(function (global) {
  'use strict';

  const subs = [];
  let state = null;
  let ws = null;
  let pollTimer = null;
  let reconnectIn = 700;

  function emit() {
    for (const fn of subs) {
      try { fn(state); } catch (e) { console.error('render failed', e); }
    }
  }

  function setState(s) { state = s; emit(); }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    try { ws = new WebSocket(`${proto}://${location.host}/ws`); }
    catch (e) { return startPolling(); }

    ws.onopen = () => {
      reconnectIn = 700;
      stopPolling();
      mark(true);
    };
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'state') setState(msg.data);
    };
    ws.onclose = () => {
      mark(false);
      startPolling();
      setTimeout(connect, reconnectIn);
      reconnectIn = Math.min(reconnectIn * 1.7, 8000);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try { setState(await (await fetch('/api/state')).json()); } catch (e) {}
    }, 900);
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  function mark(live) {
    document.querySelectorAll('[data-conn]').forEach(n => {
      n.classList.toggle('live', !!live);
    });
  }

  async function control(action, extra) {
    const body = Object.assign({ action }, extra || {});
    const res = await fetch('/api/control', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!res.ok) { console.warn('control failed', action, res.status); return null; }
    const out = await res.json();
    if (out.state) setState(out.state);
    return out;
  }

  // ----------------------------------------------------------- formatting
  const fmt = {
    clock(t) {
      const s = Math.max(0, Math.round(t));
      return 'T+' + String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    },
    secs(v) {
      if (v === null || v === undefined || !isFinite(v)) return '—';
      const s = Math.round(v);
      return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
    },
    pct(v) { return Math.round((v || 0) * 100) + '%'; },
    stair(id) { return id ? id.replace('STAIR-', '') : '—'; },
    exit(id) { return id ? id.replace('EXIT-', '') : '—'; },
    n(v, d) { return (v === null || v === undefined) ? '—' : Number(v).toFixed(d === undefined ? 1 : d); },

    // --- plain-English readings ----------------------------------------
    // Engineers read "1.62 persons/second" fine. Nobody else does, and this
    // has to be understood by a fire officer or an investor in one glance.

    // Flow, as people per minute.
    rate(perSecond) {
      if (perSecond === null || perSecond === undefined) return '—';
      return Math.round(perSecond * 60) + '/min';
    },

    // The friendly name of a space: "Room 305 — plant and server room".
    place(id) {
      const n = (window.BUILDING && window.BUILDING.nodes) ? window.BUILDING.nodes[id] : null;
      return n ? (n.human || n.name || id) : id;
    },
    // A short version for tables: "Room 305", "Stair A", "East-central corridor".
    placeShort(id) {
      return fmt.place(id).split(' — ')[0];
    },

    // How busy a route is, said in words rather than as a percentage.
    load(u) {
      if (u > 1)    return { word: 'Overcrowded', cls: 'bad' };
      if (u > 0.75) return { word: 'Very busy',   cls: 'warn' };
      if (u > 0.35) return { word: 'Busy',        cls: '' };
      if (u > 0.02) return { word: 'Flowing',     cls: 'ok' };
      return { word: 'Clear', cls: 'ok' };
    },

    // How much the system trusts a number.
    sure(c) {
      if (c >= 0.9)  return { word: 'Certain', cls: 'ok' };
      if (c >= 0.75) return { word: 'Fairly sure', cls: 'ok' };
      if (c >= 0.55) return { word: 'Not sure', cls: 'warn' };
      return { word: 'Guessing', cls: 'bad' };
    },

    // Smoke, in words.
    smoke(v) {
      if (v > 0.6)  return { word: 'Thick smoke', cls: 'bad' };
      if (v > 0.25) return { word: 'Smoke', cls: 'warn' };
      if (v > 0.05) return { word: 'Some haze', cls: 'warn' };
      return { word: 'Clear', cls: '' };
    }
  };

  function confClass(c) { return c >= 0.85 ? 'ok' : c >= 0.65 ? 'warn' : 'bad'; }
  function utilClass(u) { return u > 1 ? 'bad' : u > 0.75 ? 'warn' : 'ok'; }

  function h(tag, attrs, children) {
    const n = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === 'class') n.className = attrs[k];
      else if (k === 'html') n.innerHTML = attrs[k];
      else if (k === 'text') n.textContent = attrs[k];
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(c => n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return n;
  }

  // --------------------------------------------------------- loop display
  function renderLoop(host, s) {
    if (!host) return;
    const phases = ['SENSE', 'ESTIMATE', 'PLAN', 'GUIDE', 'OBSERVE', 'COMPARE', 'RECALCULATE'];
    const fired = new Set(s.phases_fired || []);
    if (!host._built) {
      host.innerHTML = '';
      phases.forEach(p => host.appendChild(h('span', { class: 'ph', 'data-ph': p, text: p })));
      host._built = true;
    }
    host.querySelectorAll('.ph').forEach(n => {
      const p = n.getAttribute('data-ph');
      n.classList.toggle('on', p === s.phase);
      n.classList.toggle('recent', p !== s.phase && fired.has(p));
    });
  }

  function renderFeed(host, s, limit) {
    if (!host) return;
    const rows = (s.events || []).slice(-(limit || 10)).reverse();
    host.innerHTML = '';
    rows.forEach(e => {
      host.appendChild(h('div', { class: 'row ' + (e.severity || 'info') }, [
        h('div', { class: 'ts', text: fmt.clock(e.t) }),
        h('div', { class: 'tx', html: escape(e.text) + (e.detail ? `<small>${escape(e.detail)}</small>` : '') })
      ]));
    });
  }

  function escape(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  global.SENSE = {
    on(fn) { subs.push(fn); if (state) fn(state); },
    push(s) { setState(s); },          // used by the recorded-replay build
    stop() { if (ws) { try { ws.close(); } catch (e) {} ws = null; } stopPolling(); },
    get state() { return state; },
    control, fmt, h, escape, confClass, utilClass, renderLoop, renderFeed,
    boot() {
      connect();
      fetch('/api/state').then(r => r.json()).then(setState).catch(() => {});
    }
  };
})(window);
