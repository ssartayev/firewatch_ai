/* =====================================================================
   SENSE AI — floor plan renderer.

   Draws the building graph the server is reasoning over. Nothing here
   invents geometry or state: every rectangle, route and dot comes from
   /api/building and the live state snapshot, so the picture and the plan
   cannot drift apart.

   Layers, back to front:
     shell -> spaces -> smoke -> fire -> devices -> routes -> people -> labels
   ===================================================================== */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const VB = { w: 1000, h: 560 };

  const el = (name, attrs, parent) => {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) {
      if (attrs[k] === null || attrs[k] === undefined) continue;
      n.setAttribute(k, attrs[k]);
    }
    if (parent) parent.appendChild(n);
    return n;
  };

  const FILL = {
    room:      '#0c1219',
    corridor:  '#0a1017',
    stair:     '#101823',
    exit:      '#0b1712'
  };
  const STROKE = {
    room:      '#1f2c39',
    corridor:  '#1a2632',
    stair:     '#2a3b4d',
    exit:      '#1f5140'
  };

  const CORRIDOR_MID = 278;   // centre line of the corridor spine

  // A route is drawn the way it would be walked: out of the room, along the
  // corridor, and into the core. Each node contributes one point, and an
  // elbow is inserted wherever two consecutive points are not aligned.
  function routePoints(path, nodes) {
    const pts = [];
    (path || []).forEach(id => {
      const n = nodes[id]; if (!n) return;
      pts.push(n.kind === 'corridor' ? [n.cx, CORRIDOR_MID] : [n.cx, n.cy]);
      pts[pts.length - 1].kind = n.kind;
    });
    if (pts.length < 2) return pts;

    const out = [pts[0]];
    for (let i = 1; i < pts.length; i++) {
      const p = pts[i - 1], q = pts[i];
      const dx = Math.abs(p[0] - q[0]), dy = Math.abs(p[1] - q[1]);
      if (dx > 3 && dy > 3) {
        // Leaving a room means stepping into the corridor first; leaving a
        // corridor means walking along it before turning off.
        out.push(p.kind === 'corridor' ? [q[0], p[1]] : [p[0], q[1]]);
      }
      out.push(q);
    }
    return out;
  }

  // Deterministic scatter so people do not jitter between frames.
  function seat(node, i, total) {
    const cols = Math.max(1, Math.ceil(Math.sqrt(total * (node.w / Math.max(node.h, 1)))));
    const rows = Math.max(1, Math.ceil(total / cols));
    const c = i % cols, r = Math.floor(i / cols);
    const px = node.x + node.w * ((c + 0.5) / cols);
    const py = node.y + node.h * ((r + 0.65) / (rows + 0.35));
    return [px, py];
  }

  class FloorPlan {
    constructor(host, building, opts) {
      this.host = typeof host === 'string' ? document.querySelector(host) : host;
      this.b = building;
      this.opts = Object.assign({
        people: true, devices: true, routes: true, scan: true,
        labels: true, mode: 'operations', onPick: null
      }, opts || {});
      this.selected = null;
      this._build();
    }

    _build() {
      this.host.innerHTML = '';
      const svg = el('svg', {
        viewBox: `0 0 ${VB.w} ${VB.h}`,
        preserveAspectRatio: 'xMidYMid meet',
        role: 'img',
        'aria-label': 'Live floor plan of the modelled floor'
      }, this.host);
      this.svg = svg;

      const defs = el('defs', {}, svg);

      const smoke = el('radialGradient', { id: 'fp-smoke' }, defs);
      el('stop', { offset: '0%',   'stop-color': '#9fb0bd', 'stop-opacity': '.85' }, smoke);
      el('stop', { offset: '100%', 'stop-color': '#6b7885', 'stop-opacity': '0' }, smoke);

      const fire = el('radialGradient', { id: 'fp-fire' }, defs);
      el('stop', { offset: '0%',   'stop-color': '#ffd36b', 'stop-opacity': '.95' }, fire);
      el('stop', { offset: '45%',  'stop-color': '#f0562c', 'stop-opacity': '.72' }, fire);
      el('stop', { offset: '100%', 'stop-color': '#8a1f10', 'stop-opacity': '0' }, fire);

      const soft = el('filter', { id: 'fp-soft', x: '-25%', y: '-25%', width: '150%', height: '150%' }, defs);
      el('feGaussianBlur', { stdDeviation: '5' }, soft);

      const blur = el('filter', { id: 'fp-blur', x: '-40%', y: '-40%', width: '180%', height: '180%' }, defs);
      el('feGaussianBlur', { stdDeviation: '9' }, blur);

      const glow = el('filter', { id: 'fp-glow', x: '-60%', y: '-60%', width: '220%', height: '220%' }, defs);
      el('feGaussianBlur', { stdDeviation: '3.4', result: 'b' }, glow);
      const m = el('feMerge', {}, glow);
      el('feMergeNode', { in: 'b' }, m);
      el('feMergeNode', { in: 'SourceGraphic' }, m);

      const hatch = el('pattern', {
        id: 'fp-core', width: 7, height: 7, patternUnits: 'userSpaceOnUse',
        patternTransform: 'rotate(45)'
      }, defs);
      el('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: '#22d3ee', 'stroke-opacity': '.13', 'stroke-width': 2 }, hatch);

      // Layers
      this.L = {};
      ['shell', 'spaces', 'smoke', 'fire', 'devices', 'routes', 'people', 'labels', 'fx']
        .forEach(k => { this.L[k] = el('g', { class: 'fp-' + k }, svg); });

      this._shell();
      this._spaces();
      if (this.opts.scan) {
        el('rect', {
          class: 'scanline', x: 40, y: 40, width: 920, height: 26,
          fill: 'url(#fp-smoke)', opacity: '.4', 'pointer-events': 'none'
        }, this.L.fx);
      }
    }

    _shell() {
      const g = this.L.shell;
      el('rect', { x: 0, y: 0, width: VB.w, height: VB.h, fill: '#05070a' }, g);
      for (let x = 40; x <= 960; x += 40) {
        el('line', { x1: x, y1: 40, x2: x, y2: 520, stroke: '#7da5c3', 'stroke-opacity': '.04' }, g);
      }
      for (let y = 40; y <= 520; y += 40) {
        el('line', { x1: 40, y1: y, x2: 960, y2: y, stroke: '#7da5c3', 'stroke-opacity': '.04' }, g);
      }
      el('rect', {
        x: 40, y: 40, width: 920, height: 480, fill: 'none',
        stroke: '#2b3a49', 'stroke-width': 1.6
      }, g);
      el('text', {
        x: 48, y: 33, fill: '#5f7080', 'font-size': 12.5, 'letter-spacing': '.02em'
      }, g).textContent = 'Level 3 — simplified floor plan';
      el('text', {
        x: 952, y: 538, fill: '#3d4a57', 'font-size': 11, 'text-anchor': 'end',
        'letter-spacing': '.02em'
      }, g).textContent = 'Tower B · drawing not to scale';
    }

    _spaces() {
      this.rects = {};
      this.smokeEls = {};
      const nodes = this.b.nodes;
      Object.keys(nodes).forEach(id => {
        const n = nodes[id];
        const g = el('g', { class: 'fp-node', 'data-id': id, style: 'cursor:pointer' }, this.L.spaces);
        const r = el('rect', {
          x: n.x, y: n.y, width: n.w, height: n.h, rx: 2,
          fill: FILL[n.kind] || FILL.room,
          stroke: STROKE[n.kind] || STROKE.room, 'stroke-width': 1
        }, g);
        if (n.kind === 'stair') {
          el('rect', { x: n.x, y: n.y, width: n.w, height: n.h, rx: 2, fill: 'url(#fp-core)' }, g);
          // stair treads
          const step = n.h / 9;
          for (let i = 1; i < 9; i++) {
            el('line', {
              x1: n.x + 5, y1: n.y + i * step, x2: n.x + n.w - 5, y2: n.y + i * step,
              stroke: '#22d3ee', 'stroke-opacity': '.15'
            }, g);
          }
        }
        if (n.kind === 'exit') {
          el('rect', { x: n.x, y: n.y, width: n.w, height: n.h, rx: 2, fill: '#34d399', 'fill-opacity': '.07' }, g);
        }
        this.rects[id] = r;
        g.addEventListener('click', () => {
          this.selected = id;
          if (this.opts.onPick) this.opts.onPick(id, n);
          this._select();
        });

        // smoke overlay, one per space, opacity driven by state
        const s = el('rect', {
          x: n.x + 1, y: n.y + 1, width: Math.max(n.w - 2, 1), height: Math.max(n.h - 2, 1),
          fill: '#aebac6', opacity: 0, 'pointer-events': 'none', rx: 2
        }, this.L.smoke);
        this.smokeEls[id] = s;
      });

      // vertical shaft connectors from each stair down to its discharge
      [['STAIR-A', 'EXIT-A'], ['STAIR-B', 'EXIT-B'], ['STAIR-C', 'EXIT-C']].forEach(([a, b]) => {
        const na = this.b.nodes[a], nb = this.b.nodes[b];
        if (!na || !nb) return;
        el('line', {
          x1: na.cx, y1: na.y + na.h, x2: nb.cx, y2: nb.y,
          stroke: '#2a3b4d', 'stroke-width': 1, 'stroke-dasharray': '3 4'
        }, this.L.shell);
      });

      if (this.opts.labels) this._labels();
    }

    _labels() {
      const g = this.L.labels;
      this.caps = {};
      Object.keys(this.b.nodes).forEach(id => {
        const n = this.b.nodes[id];
        if (n.kind === 'corridor') {
          if (id === 'C1') {
            el('text', {
              x: n.x + 9, y: n.y + 15, fill: '#46535f',
              'font-size': 11, 'letter-spacing': '.04em', 'pointer-events': 'none'
            }, g).textContent = 'Corridor';
          }
          return;
        }
        const t = el('text', {
          x: n.x + 7, y: n.y + 16, fill: '#93a4b2', 'font-size': 12,
          'letter-spacing': '.01em', 'pointer-events': 'none'
        }, g);
        t.textContent = n.kind === 'exit' ? n.label.replace('EXIT ', '')
          : n.kind === 'room' ? 'Room ' + n.label : n.label;
        if (n.kind === 'exit') {
          t.setAttribute('x', n.cx);
          t.setAttribute('y', n.y + 25);
          t.setAttribute('text-anchor', 'middle');
          t.setAttribute('font-size', 13);
          t.setAttribute('fill', '#34d399');
          el('text', {
            x: n.cx, y: n.y - 5, 'text-anchor': 'middle', fill: '#3f8d72',
            'font-size': 10.5, 'letter-spacing': '.03em', 'pointer-events': 'none'
          }, g).textContent = 'Way out';
        }
        if (n.kind === 'stair') {
          t.setAttribute('x', n.cx);
          t.setAttribute('y', n.y + 17);
          t.setAttribute('text-anchor', 'middle');
          t.setAttribute('font-size', 13);
          t.setAttribute('fill', '#22d3ee');
        }
        // occupancy caption, updated live
        const c = el('text', {
          x: n.kind === 'stair' ? n.cx : n.x + 7,
          y: n.kind === 'stair' ? n.y + 33 : n.y + 31,
          'text-anchor': n.kind === 'stair' ? 'middle' : 'start',
          fill: '#5f7080', 'font-size': 11, 'pointer-events': 'none'
        }, g);
        this.caps[id] = c;
      });
    }

    _select() {
      Object.keys(this.rects).forEach(id => {
        this.rects[id].setAttribute('stroke-width', id === this.selected ? 2 : 1);
        if (id === this.selected) this.rects[id].setAttribute('stroke', '#22d3ee');
        else this.rects[id].setAttribute('stroke', STROKE[this.b.nodes[id].kind] || STROKE.room);
      });
    }

    // ---------------------------------------------------------------- draw
    render(state) {
      if (!state) return;
      this.state = state;
      this._drawHazard(state);
      if (this.opts.devices) this._drawDevices(state);
      if (this.opts.routes) this._drawRoutes(state);
      if (this.opts.people) this._drawPeople(state);
      this._drawCaptions(state);
    }

    _drawHazard(state) {
      const hz = state.hazard || {};
      const smoke = hz.smoke || {}, fire = hz.fire || {};
      const blocked = new Set(hz.blocked || []);
      const impassable = new Set(hz.impassable || []);

      Object.keys(this.smokeEls).forEach(id => {
        const v = smoke[id] || 0;
        this.smokeEls[id].setAttribute('opacity', Math.min(v * 0.66, 0.66).toFixed(3));
      });

      Object.keys(this.rects).forEach(id => {
        if (id === this.selected) return;
        const r = this.rects[id];
        if (fire[id] > 0.02) { r.setAttribute('stroke', '#f04444'); r.setAttribute('stroke-width', 1.8); }
        else if (impassable.has(id) || blocked.has(id)) { r.setAttribute('stroke', '#f04444'); r.setAttribute('stroke-width', 1.4); }
        else if ((smoke[id] || 0) > 0.22) { r.setAttribute('stroke', '#f5a524'); r.setAttribute('stroke-width', 1.2); }
        else { r.setAttribute('stroke', STROKE[this.b.nodes[id].kind] || STROKE.room); r.setAttribute('stroke-width', 1); }
      });

      this.L.fire.innerHTML = '';
      Object.keys(fire).forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        const rad = 26 + 46 * Math.min(fire[id], 1);
        el('circle', {
          class: 'fire-glow', cx: n.cx, cy: n.cy, r: rad,
          fill: 'url(#fp-fire)', filter: 'url(#fp-blur)', 'pointer-events': 'none'
        }, this.L.fire);
        el('path', {
          class: 'fire-glow',
          d: `M${n.cx} ${n.cy - 13} l7 9 -3.5 0 5 8 -8.5 5 -8.5 -5 5 -8 -3.5 0 z`,
          fill: '#ffb347', 'pointer-events': 'none'
        }, this.L.fire);
      });

      // blocked markers
      (hz.blocked || []).forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        el('g', { 'pointer-events': 'none' }, this.L.fire);
        el('line', { x1: n.x + 8, y1: n.y + 8, x2: n.x + n.w - 8, y2: n.y + n.h - 8, stroke: '#f04444', 'stroke-width': 1.6, 'stroke-opacity': .7 }, this.L.fire);
        el('line', { x1: n.x + n.w - 8, y1: n.y + 8, x2: n.x + 8, y2: n.y + n.h - 8, stroke: '#f04444', 'stroke-width': 1.6, 'stroke-opacity': .7 }, this.L.fire);
      });
    }

    _drawDevices(state) {
      if (this._devicesDrawn) { this._updateDevices(state); return; }
      this._devicesDrawn = true;
      this.devEls = {};
      const off = {};
      (this.b.devices || []).forEach(d => {
        if (d.future) return;
        if (d.kind !== 'camera' && d.kind !== 'presence' && d.kind !== 'smoke') return;
        const n = this.b.nodes[d.node]; if (!n) return;
        const k = d.node + d.kind;
        if (off[k]) return; off[k] = 1;
        const slot = { camera: 0, presence: 1, smoke: 2 }[d.kind];
        const cx = n.x + n.w - 9 - slot * 11, cy = n.y + 9;
        let e;
        if (d.kind === 'camera') {
          e = el('path', { d: `M${cx - 4} ${cy - 3} h6 l3 -2 v10 l-3 -2 h-6 z`, fill: '#2b3a49', stroke: '#3f5567', 'stroke-width': .7 }, this.L.devices);
        } else if (d.kind === 'presence') {
          e = el('path', { d: `M${cx - 4} ${cy + 3} a5 5 0 0 1 8 0 M${cx - 2} ${cy + 3} a2.5 2.5 0 0 1 4 0`, fill: 'none', stroke: '#3f5567', 'stroke-width': 1.1 }, this.L.devices);
        } else {
          e = el('circle', { cx: cx, cy: cy + 1, r: 3, fill: 'none', stroke: '#3f5567', 'stroke-width': 1 }, this.L.devices);
        }
        e.setAttribute('pointer-events', 'none');
        this.devEls[d.node + '|' + d.kind] = e;
      });
      this._updateDevices(state);
    }

    _updateDevices(state) {
      const occ = state.occupancy || {};
      const smoke = (state.hazard || {}).smoke || {};
      const offline = new Set(state.camera_offline || []);
      Object.keys(this.devEls || {}).forEach(key => {
        const [node, kind] = key.split('|');
        const e = this.devEls[key];
        let colour = '#3f5567';
        if (kind === 'camera') {
          if (offline.has(node)) colour = '#f04444';
          else {
            const f = occ[node];
            const cam = f && f.sources ? f.sources.find(s => s.source === 'camera') : null;
            if (cam) colour = cam.status === 'blind' ? '#f04444'
              : cam.status === 'degraded' ? '#f5a524' : '#22d3ee';
          }
        } else if (kind === 'presence') {
          const f = occ[node];
          colour = f && f.primary === 'presence' ? '#34d399' : '#3f5567';
        } else if (kind === 'smoke') {
          colour = (smoke[node] || 0) > 0.18 ? '#f04444' : '#3f5567';
        }
        e.setAttribute('stroke', colour);
        if (e.tagName === 'path' && kind === 'camera') e.setAttribute('fill', colour === '#3f5567' ? '#2b3a49' : colour);
      });
    }

    _drawRoutes(state) {
      const g = this.L.routes; g.innerHTML = '';
      const plan = state.plan || {};
      const routes = (plan.routes || []).filter(r => r.people > 0);
      if (!routes.length) return;

      const maxPeople = Math.max.apply(null, routes.map(r => r.people));
      const byStair = { 'STAIR-A': '#22d3ee', 'STAIR-B': '#8b93f8', 'STAIR-C': '#34d399' };

      routes.forEach(r => {
        const pts = routePoints(r.path, this.b.nodes);
        if (pts.length < 2) return;

        const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
        const width = 1.3 + 3.4 * (r.people / Math.max(maxPeople, 1));
        const colour = byStair[r.stair] || '#22d3ee';

        el('path', { d: d, fill: 'none', stroke: colour, 'stroke-opacity': .13, 'stroke-width': width + 4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'pointer-events': 'none' }, g);
        el('path', {
          class: 'route-line', d: d, fill: 'none', stroke: colour,
          'stroke-width': width, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
          'stroke-dasharray': '11 6', filter: 'url(#fp-glow)', 'pointer-events': 'none'
        }, g);

        // direction chevron at the midpoint of the last leg
        const a = pts[pts.length - 2], b = pts[pts.length - 1];
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
        const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
        el('path', {
          d: 'M-4 -4 L3 0 L-4 4 Z', fill: colour, 'fill-opacity': .85,
          transform: `translate(${mx.toFixed(1)} ${my.toFixed(1)}) rotate(${ang.toFixed(1)})`,
          'pointer-events': 'none'
        }, g);
      });
    }

    _drawPeople(state) {
      const g = this.L.people; g.innerHTML = '';
      const truth = state.truth || {};
      const occ = state.occupancy || {};
      const refuge = state.refuge || {};

      Object.keys(truth).forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        const count = Math.round(truth[id]);
        if (count <= 0) return;
        const f = occ[id];
        const uncertain = f ? f.confidence < 0.8 : false;
        const isRefuge = (refuge[id] || 0) > 0.4;
        const capped = Math.min(count, 26);

        for (let i = 0; i < capped; i++) {
          const [px, py] = seat(n, i, capped);
          const isStranded = isRefuge && i >= capped - Math.round(refuge[id]);
          el('circle', {
            cx: px.toFixed(1), cy: py.toFixed(1), r: isStranded ? 3.4 : 2.6,
            fill: isStranded ? '#f5a524' : (uncertain ? '#f5a524' : '#dbe7f0'),
            'fill-opacity': uncertain && !isStranded ? .55 : .92,
            stroke: isStranded ? '#f5a524' : 'none',
            'stroke-width': isStranded ? 1.2 : 0,
            'stroke-opacity': .5,
            'pointer-events': 'none'
          }, g);
        }
        if (count > 26) {
          el('text', {
            x: n.x + n.w - 7, y: n.y + n.h - 24, 'text-anchor': 'end', fill: '#8697a6',
            'font-size': 11, 'pointer-events': 'none'
          }, g).textContent = '+' + (count - 26) + ' more';
        }
      });
    }

    _drawCaptions(state) {
      if (!this.caps) return;
      const occ = state.occupancy || {};
      const stairs = state.stairs || {};
      Object.keys(this.caps).forEach(id => {
        const c = this.caps[id];
        const n = this.b.nodes[id];
        if (n.kind === 'stair') {
          const s = stairs[id];
          if (!s) { c.textContent = ''; return; }
          c.textContent = s.queue >= 1 ? `${Math.round(s.queue)} waiting` : '';
          c.setAttribute('fill', s.utilisation > 1 ? '#f04444' : s.utilisation > 0.75 ? '#f5a524' : '#4f5d6b');
          return;
        }
        if (n.kind === 'exit') { c.textContent = ''; return; }
        const f = occ[id];
        if (!f || f.high <= 0 || (f.low === 0 && f.high <= 1)) { c.textContent = ''; return; }
        c.textContent = f.display + (f.high === 1 ? ' person' : ' people')
          + (f.confidence < 0.7 ? ' (unsure)' : '');
        c.setAttribute('fill', f.confidence < 0.7 ? '#f5a524' : f.is_range ? '#8697a6' : '#5f7080');
      });
    }

    highlight(ids, colour) {
      this.L.fx.querySelectorAll('.fp-hl').forEach(n => n.remove());
      (ids || []).forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        el('rect', {
          class: 'fp-hl route-line', x: n.x - 2, y: n.y - 2,
          width: n.w + 4, height: n.h + 4, rx: 3,
          fill: 'none', stroke: colour || '#22d3ee', 'stroke-width': 1.6,
          'stroke-dasharray': '6 4', 'pointer-events': 'none'
        }, this.L.fx);
      });
    }

    drawPath(path, colour, label) {
      const pts = routePoints(path, this.b.nodes);
      if (pts.length < 2) return;
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
      el('path', { class: 'fp-hl', d: d, fill: 'none', stroke: colour, 'stroke-opacity': .16, 'stroke-width': 8, 'stroke-linecap': 'round', 'pointer-events': 'none' }, this.L.fx);
      el('path', { class: 'fp-hl route-line', d: d, fill: 'none', stroke: colour, 'stroke-width': 2.4, 'stroke-dasharray': '10 6', 'stroke-linecap': 'round', filter: 'url(#fp-glow)', 'pointer-events': 'none' }, this.L.fx);
      if (label) {
        const p = pts[Math.floor(pts.length / 2)];
        const t = el('text', { class: 'fp-hl', x: p[0], y: p[1] - 12, 'text-anchor': 'middle', fill: colour, 'font-size': 11.5, 'pointer-events': 'none' }, this.L.fx);
        t.textContent = label;
      }
    }

    clearOverlay() { this.L.fx.querySelectorAll('.fp-hl').forEach(n => n.remove()); }
  }

  global.FloorPlan = FloorPlan;
})(window);
