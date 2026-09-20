/* =====================================================================
   SENSE AI — isometric building view for the responder screen.

   A 2:1 axonometric projection of the same node rectangles the planner
   uses, stacked as storeys. Only the modelled floor carries real state;
   the others are drawn as plates with their aggregate population, which
   is exactly as much as the model actually knows about them. Pretending
   otherwise on a responder screen would be the wrong kind of polish.
   ===================================================================== */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const el = (n, a, p) => {
    const e = document.createElementNS(NS, n);
    for (const k in a) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    if (p) p.appendChild(e);
    return e;
  };

  // 2:1 isometric. Plan (x, y) -> screen, with a per-storey lift.
  const SX = 0.52, SY = 0.27, LIFT = 74;
  // Offsets chosen so the whole stack — five storeys plus the ground plane —
  // lands inside the viewBox with room for the floor labels on the left.
  const OX = 276, OY = 306;
  function iso(x, y, storey) {
    return [
      (x - y) * SX + OX,
      (x + y) * SY - storey * LIFT + OY
    ];
  }

  function facePath(n, storey, h) {
    const p = [
      iso(n.x, n.y, storey), iso(n.x + n.w, n.y, storey),
      iso(n.x + n.w, n.y + n.h, storey), iso(n.x, n.y + n.h, storey)
    ];
    return 'M' + p.map(q => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(' L') + ' Z';
  }

  class Isometric {
    constructor(host, building, opts) {
      this.host = typeof host === 'string' ? document.querySelector(host) : host;
      this.b = building;
      this.opts = Object.assign({ onPick: null }, opts || {});
      this.selected = null;
      this._build();
    }

    _build() {
      this.host.innerHTML = '';
      const svg = el('svg', {
        viewBox: '0 0 800 800', preserveAspectRatio: 'xMidYMid meet',
        role: 'img', 'aria-label': 'Isometric model of the building'
      }, this.host);
      this.svg = svg;
      const defs = el('defs', {}, svg);

      const g1 = el('linearGradient', { id: 'iso-plate', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el('stop', { offset: '0%', 'stop-color': '#16202b' }, g1);
      el('stop', { offset: '100%', 'stop-color': '#0b1118' }, g1);

      const gf = el('radialGradient', { id: 'iso-fire' }, defs);
      el('stop', { offset: '0%', 'stop-color': '#ffcc66', 'stop-opacity': '.95' }, gf);
      el('stop', { offset: '100%', 'stop-color': '#c0301a', 'stop-opacity': '0' }, gf);

      const blur = el('filter', { id: 'iso-blur', x: '-60%', y: '-60%', width: '220%', height: '220%' }, defs);
      el('feGaussianBlur', { stdDeviation: '10' }, blur);
      const glow = el('filter', { id: 'iso-glow', x: '-60%', y: '-60%', width: '220%', height: '220%' }, defs);
      el('feGaussianBlur', { stdDeviation: '3', result: 'b' }, glow);
      const mg = el('feMerge', {}, glow);
      el('feMergeNode', { in: 'b' }, mg);
      el('feMergeNode', { in: 'SourceGraphic' }, mg);

      this.L = {};
      ['ground', 'plates', 'floor', 'hazard', 'people', 'routes', 'labels'].forEach(k => {
        this.L[k] = el('g', { class: 'iso-' + k }, svg);
      });

      this._ground();
      this._plates();
      this._floor();
    }

    // Grade, with the three discharge points. Without it the approach routes
    // appeared to end in mid-air, which is exactly the kind of detail a
    // responder screen cannot afford to get wrong.
    _ground() {
      const shell = { x: 20, y: 20, w: 960, h: 520 };
      el('path', {
        d: facePath(shell, -0.62, 0), fill: '#0b1016',
        stroke: '#223040', 'stroke-width': 1.2
      }, this.L.ground);
      const g0 = iso(shell.x, shell.y + shell.h, -0.62);
      el('text', {
        x: g0[0] - 40, y: g0[1] + 16, fill: '#3d4a57', 'font-size': 10.5,
        'pointer-events': 'none'
      }, this.L.ground).textContent = 'Street level';
      ['EXIT-A', 'EXIT-B', 'EXIT-C'].forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        el('path', {
          d: facePath(n, -0.62, 0), fill: '#34d399', 'fill-opacity': .13,
          stroke: '#34d399', 'stroke-opacity': .6, 'stroke-width': 1
        }, this.L.ground);
        const c = iso(n.cx, n.cy, -0.62);
        const t = el('text', {
          x: c[0], y: c[1] + 3, 'text-anchor': 'middle', fill: '#34d399',
          'font-size': 10.5, 'letter-spacing': '.04em', 'pointer-events': 'none'
        }, this.L.ground);
        t.textContent = n.label.replace('EXIT ', 'Exit ');
      });
    }

    // Storeys above and below the modelled one, drawn as plain plates.
    _plates() {
      const shell = { x: 40, y: 40, w: 920, h: 480 };
      const floors = (this.b.floors || []).slice().sort((a, b) => a.floor - b.floor);
      this.plateEls = {};
      floors.forEach(f => {
        const s = f.floor - 1;
        const g = el('g', {}, this.L.plates);
        // slab edge, to give the stack thickness
        const top = iso(shell.x, shell.y + shell.h, s);
        const right = iso(shell.x + shell.w, shell.y + shell.h, s);
        el('path', {
          d: `M${top[0]} ${top[1]} L${right[0]} ${right[1]} L${right[0]} ${right[1] + 9} L${top[0]} ${top[1] + 9} Z`,
          fill: '#070b10', stroke: '#1b2530', 'stroke-width': .8
        }, g);
        el('path', {
          d: facePath(shell, s, 0),
          fill: f.modelled ? 'none' : 'url(#iso-plate)',
          stroke: f.modelled ? '#22d3ee' : '#1b2530',
          'stroke-width': f.modelled ? 1.4 : .9,
          'stroke-opacity': f.modelled ? .55 : 1
        }, g);

        const lbl = iso(shell.x, shell.y, s);
        const t = el('text', {
          x: lbl[0] - 66, y: lbl[1] + 26, fill: f.modelled ? '#22d3ee' : '#4f5d6b',
          'font-size': 11, 'font-family': 'ui-monospace, Menlo, monospace', 'letter-spacing': '.12em'
        }, g);
        t.textContent = 'Level ' + f.floor;
        const c = el('text', {
          x: lbl[0] - 66, y: lbl[1] + 40, fill: '#3d4a57',
          'font-size': 9.5, 'font-family': 'ui-monospace, Menlo, monospace'
        }, g);
        this.plateEls[f.floor] = c;
        c.textContent = f.modelled ? 'mapped in detail' : 'about ' + f.population + ' people';
      });

      // stair cores rising through the stack
      ['STAIR-A', 'STAIR-B', 'STAIR-C'].forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        const bottom = iso(n.cx, n.cy, 0), topp = iso(n.cx, n.cy, 4);
        el('line', {
          x1: bottom[0], y1: bottom[1], x2: topp[0], y2: topp[1],
          stroke: '#22d3ee', 'stroke-opacity': .22, 'stroke-width': 2, 'stroke-dasharray': '5 5'
        }, this.L.plates);
      });
    }

    // The modelled floor, room by room.
    _floor() {
      const s = 2;                              // floor 3 sits at index 2
      this.faces = {}; this.smokeFaces = {};
      Object.keys(this.b.nodes).forEach(id => {
        const n = this.b.nodes[id];
        if (n.floor !== 3) return;
        const g = el('g', { style: 'cursor:pointer', 'data-id': id }, this.L.floor);
        const f = el('path', {
          d: facePath(n, s, 0),
          fill: n.kind === 'stair' ? '#132030' : n.kind === 'corridor' ? '#0a1017' : '#0d141c',
          stroke: n.kind === 'stair' ? '#2a3b4d' : '#1f2c39', 'stroke-width': 1
        }, g);
        this.faces[id] = f;
        const sm = el('path', { d: facePath(n, s, 0), fill: '#aebac6', opacity: 0, 'pointer-events': 'none' }, this.L.hazard);
        this.smokeFaces[id] = sm;
        g.addEventListener('click', () => {
          this.selected = id;
          if (this.opts.onPick) this.opts.onPick(id, n);
        });
        if (n.kind === 'room') {
          const c = iso(n.cx, n.cy, s);
          const t = el('text', {
            x: c[0], y: c[1] + 3, 'text-anchor': 'middle', fill: '#5f7080',
            'font-size': 9.5, 'pointer-events': 'none'
          }, this.L.labels);
          t.textContent = 'Rm ' + n.label;
        }
      });
    }

    render(state) {
      if (!state) return;
      const s = 2;
      const hz = state.hazard || {};
      const smoke = hz.smoke || {}, fire = hz.fire || {};
      const occ = state.occupancy || {};
      const truth = state.truth || {};
      const refuge = state.refuge || {};

      Object.keys(this.smokeFaces).forEach(id => {
        this.smokeFaces[id].setAttribute('opacity', Math.min((smoke[id] || 0) * .62, .62).toFixed(3));
      });
      Object.keys(this.faces).forEach(id => {
        const f = this.faces[id];
        if (fire[id] > .02) { f.setAttribute('stroke', '#f04444'); f.setAttribute('stroke-width', 2); }
        else if ((refuge[id] || 0) > .4) { f.setAttribute('stroke', '#f5a524'); f.setAttribute('stroke-width', 2); }
        else if (id === this.selected) { f.setAttribute('stroke', '#22d3ee'); f.setAttribute('stroke-width', 2); }
        else if ((smoke[id] || 0) > .22) { f.setAttribute('stroke', '#f5a524'); f.setAttribute('stroke-width', 1.2); }
        else { f.setAttribute('stroke', this.b.nodes[id].kind === 'stair' ? '#2a3b4d' : '#1f2c39'); f.setAttribute('stroke-width', 1); }
      });

      this.L.hazard.querySelectorAll('.iso-flame').forEach(n => n.remove());
      Object.keys(fire).forEach(id => {
        const n = this.b.nodes[id]; if (!n) return;
        const c = iso(n.cx, n.cy, s);
        el('circle', {
          class: 'iso-flame fire-glow', cx: c[0], cy: c[1], r: 26 + 38 * Math.min(fire[id], 1),
          fill: 'url(#iso-fire)', filter: 'url(#iso-blur)', 'pointer-events': 'none'
        }, this.L.hazard);
      });

      // occupants
      const pg = this.L.people; pg.innerHTML = '';
      Object.keys(truth).forEach(id => {
        const n = this.b.nodes[id]; if (!n || n.floor !== 3) return;
        const count = Math.round(truth[id]); if (count <= 0) return;
        const f = occ[id];
        const uncertain = f ? f.confidence < .8 : false;
        const isRefuge = (refuge[id] || 0) > .4;
        const capped = Math.min(count, 16);
        for (let i = 0; i < capped; i++) {
          const cols = Math.max(1, Math.ceil(Math.sqrt(capped)));
          const px = n.x + n.w * ((i % cols + .5) / cols);
          const py = n.y + n.h * ((Math.floor(i / cols) + .7) / (Math.ceil(capped / cols) + .4));
          const c = iso(px, py, s);
          el('circle', {
            cx: c[0].toFixed(1), cy: (c[1] - 4).toFixed(1), r: isRefuge ? 3.6 : 2.6,
            fill: isRefuge ? '#f5a524' : uncertain ? '#f5a524' : '#dbe7f0',
            'fill-opacity': isRefuge ? 1 : uncertain ? .6 : .9,
            filter: isRefuge ? 'url(#iso-glow)' : null, 'pointer-events': 'none'
          }, pg);
        }
      });

      // remaining population per unmodelled plate
      (this.b.floors || []).forEach(f => {
        const c = this.plateEls[f.floor];
        if (!c || f.modelled) return;
        c.textContent = 'about ' + f.population + ' people';
      });
    }

    drawRoute(path, colour, label) {
      const s = 2;
      const pts = (path || []).map(id => {
        const n = this.b.nodes[id]; if (!n) return null;
        return iso(n.cx, n.cy, n.kind === 'exit' ? -0.62 : s);
      }).filter(Boolean);
      if (pts.length < 2) return;
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
      el('path', { class: 'iso-rt', d, fill: 'none', stroke: colour, 'stroke-opacity': .16, 'stroke-width': 9, 'stroke-linecap': 'round', 'pointer-events': 'none' }, this.L.routes);
      el('path', { class: 'iso-rt route-line', d, fill: 'none', stroke: colour, 'stroke-width': 2.6, 'stroke-dasharray': '11 6', 'stroke-linecap': 'round', filter: 'url(#iso-glow)', 'pointer-events': 'none' }, this.L.routes);
      if (label) {
        const p = pts[Math.max(0, Math.floor(pts.length / 2) - 1)];
        const t = el('text', {
          class: 'iso-rt', x: p[0], y: p[1] - 13, 'text-anchor': 'middle', fill: colour,
          'font-size': 11, 'letter-spacing': '.02em', 'pointer-events': 'none'
        }, this.L.routes);
        t.textContent = label;
      }
    }

    clearRoutes() { this.L.routes.innerHTML = ''; }
  }

  global.Isometric = Isometric;
})(window);
