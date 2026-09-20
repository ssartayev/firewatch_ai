"""
Export a static build of the product that can be hosted anywhere.

The live app is FastAPI with a stateful simulation and a WebSocket, which no
static host and no serverless platform will run: torch alone is 498 MB against
Vercel's 250 MB function limit, and serverless functions hold no state and
speak no WebSocket.

So instead of shipping the engine, this ships a recording of it. The whole
incident is run once here, every snapshot the server would have pushed is
written to a JSON file, and a small shim replays those frames into exactly the
same front end. Every page renders from real engine output; what you lose is
the ability to intervene mid-run, which the build says plainly on screen.

    python scripts/export_static.py [outdir]
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else BASE / "web")
EVERY = 2          # keep every Nth tick (0.5 s each) -> one frame per second


def record() -> list[dict]:
    from app.simulation import Simulation

    sim = Simulation()
    sim.estimate()
    sim.make_plan("baseline plan for the building at rest")

    frames = [{"t": 0.0, "state": sim.snapshot(), "responder": sim.responder_intel()}]
    sim.start_incident()
    n = 0
    while sim.mode != "cleared" and sim.t < 400:
        sim.tick()
        n += 1
        if n % EVERY == 0:
            frames.append({"t": round(sim.t, 1), "state": sim.snapshot(),
                           "responder": sim.responder_intel()})
    frames.append({"t": round(sim.t, 1), "state": sim.snapshot(),
                   "responder": sim.responder_intel()})
    return frames


SHIM = r"""
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
"""


def main() -> int:
    from jinja2 import Environment, FileSystemLoader
    from app.building import load_building
    from app.vision import ENGINE
    from app.main import SCENARIOS

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)

    print("  recording the incident…")
    frames = record()
    (OUT / "data" / "states.json").write_text(json.dumps({"frames": frames}, separators=(",", ":")))
    mb = (OUT / "data" / "states.json").stat().st_size / 1024 / 1024
    print(f"  {len(frames)} frames, {mb:.1f} MB (served gzipped)")

    (OUT / "data" / "vision-demo.json").write_text(
        json.dumps(ENGINE.simulate(reason="recorded demonstration")))
    (OUT / "data" / "scenarios.json").write_text(
        json.dumps([{"id": k, "label": v} for k, v in SCENARIOS.items()]))

    env = Environment(loader=FileSystemLoader(str(BASE / "templates")))
    building = load_building().as_dict()
    vision = ENGINE.status()
    pages = {"index.html": "live", "occupancy.html": "occupancy",
             "evacuation.html": "evacuation", "command.html": "command",
             "twin.html": "twin", "technology.html": "technology"}
    names = {"index.html": "index.html", "occupancy.html": "occupancy.html",
             "evacuation.html": "evacuation.html", "command.html": "command.html",
             "twin.html": "twin.html", "technology.html": "technology.html"}

    for tpl, active in pages.items():
        html = env.get_template(tpl).render(active=active, building=building,
                                            vision=vision, asset_v="static")
        html = html.replace('<script>SENSE.boot();</script>',
                            '<script src="/static/js/static-mode.js"></script>\n'
                            '<script>SENSE.boot();</script>')
        # static hosting serves extensionless paths; keep the nav working
        for href, target in [('href="/"', 'href="/index.html"'),
                             ('href="/occupancy"', 'href="/occupancy.html"'),
                             ('href="/evacuation"', 'href="/evacuation.html"'),
                             ('href="/command"', 'href="/command.html"'),
                             ('href="/twin"', 'href="/twin.html"'),
                             ('href="/technology"', 'href="/technology.html"')]:
            html = html.replace(href, target)
        (OUT / names[tpl]).write_text(html)

    shutil.copytree(BASE / "static", OUT / "static")
    (OUT / "static" / "js" / "static-mode.js").write_text(SHIM)

    deck = OUT / "deck"; deck.mkdir()
    for f in ["SENSE-AI-pitch.html", "SENSE-AI-pitch.pdf",
              "SPEAKER-SCRIPT.pdf", "WEBSITE-MANUAL.html", "WEBSITE-MANUAL.pdf"]:
        src = BASE / "deck" / f
        if src.exists():
            shutil.copy(src, deck / f)

    (OUT / "vercel.json").write_text(json.dumps({
        "cleanUrls": True,
        "headers": [{"source": "/data/(.*)",
                     "headers": [{"key": "Cache-Control", "value": "public, max-age=3600"}]}],
    }, indent=2))

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"  {OUT.name}/ built — {total/1024/1024:.1f} MB total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
