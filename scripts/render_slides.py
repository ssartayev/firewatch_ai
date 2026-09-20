"""
Render deck slides locally and report layout problems.

The slide format is plain inline-styled HTML on a fixed 1920x1080 canvas, so a
headless browser renders it exactly as the deck page will. That makes two
things possible that guessing never did: seeing the slide, and measuring it.

The checker reports, per slide:
  * text boxes whose rectangles overlap (the "text over text" failure)
  * anything past the bottom of the canvas
  * text smaller than the 24px floor

    python scripts/render_slides.py <slides-dir> [out-dir]
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9444
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">')

PAGE = """<!doctype html><html><head><meta charset="utf-8">{fonts}
<style>
  html,body{{margin:0;padding:0;background:#222;}}
  section{{position:relative;width:1920px;height:1080px;overflow:hidden;box-sizing:border-box;}}
  h1,h2,h3,p,ul,ol{{margin:0;}}
  ul,ol{{padding-left:1.2em;}}
  aside{{display:none;}}
  x-shape,x-icon,x-connector{{display:block;}}
</style></head><body>{slide}</body></html>"""

CHECK = r"""
(() => {
  const TEXT = ['H1','H2','H3','P','LI','TD','TH'];
  const nodes = [...document.querySelectorAll('section *')]
    .filter(n => TEXT.includes(n.tagName) && n.textContent.trim().length > 1);
  const boxes = nodes.map(n => {
    const r = n.getBoundingClientRect();
    const cs = getComputedStyle(n);
    return {tag:n.tagName, text:n.textContent.trim().slice(0,46),
            x:r.left, y:r.top, w:r.width, h:r.height,
            size:parseFloat(cs.fontSize)};
  });
  const overlaps = [];
  for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++) {
    const a=boxes[i], b=boxes[j];
    const ox = Math.min(a.x+a.w,b.x+b.w) - Math.max(a.x,b.x);
    const oy = Math.min(a.y+a.h,b.y+b.h) - Math.max(a.y,b.y);
    if (ox > 4 && oy > 4) overlaps.push({a:a.text,b:b.text,ox:Math.round(ox),oy:Math.round(oy)});
  }
  const sec = document.querySelector('section').getBoundingClientRect();
  const overflow = boxes.filter(b => b.y + b.h > sec.height + 2 || b.x + b.w > sec.width + 2)
                        .map(b => ({text:b.text, bottom:Math.round(b.y+b.h), right:Math.round(b.x+b.w)}));
  const tiny = boxes.filter(b => b.size < 23.5).map(b => ({text:b.text, size:b.size}));
  // how much of the canvas height the content actually uses
  const maxY = boxes.length ? Math.max(...boxes.map(b => b.y + b.h)) : 0;
  return JSON.stringify({overlaps, overflow, tiny, fill: Math.round(maxY)});
})()
"""


class Chrome:
    def __init__(self):
        self.proc = None; self.ws = None; self._id = 0

    def launch(self):
        prof = Path("/tmp/sense-render-profile")
        shutil.rmtree(prof, ignore_errors=True)
        self.proc = subprocess.Popen([
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={prof}", f"--remote-debugging-port={PORT}",
            "--window-size=1920,1080", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            time.sleep(0.4)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1); return
            except Exception:
                pass
        raise RuntimeError("Chrome did not start")

    async def connect(self):
        import websockets
        raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list").read()
        t = [x for x in json.loads(raw) if x["type"] == "page"][0]
        self.ws = await websockets.connect(t["webSocketDebuggerUrl"], max_size=64*1024*1024)
        await self.send("Page.enable"); await self.send("Runtime.enable")

    async def send(self, method, params=None):
        self._id += 1; mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") != mid: continue
            if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
            return m.get("result", {})

    def close(self):
        if self.proc: self.proc.terminate()


async def main():
    import asyncio
    src = Path(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/slide-render")
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp/slide-pages"); tmp.mkdir(exist_ok=True)

    c = Chrome(); c.launch(); await c.connect()
    problems = 0
    try:
        for f in sorted(src.glob("*.html")):
            page = tmp / f"{f.stem}.html"
            page.write_text(PAGE.format(fonts=FONTS, slide=f.read_text()))
            await c.send("Page.navigate", {"url": page.resolve().as_uri()})
            await asyncio.sleep(1.5)
            r = await c.send("Runtime.evaluate", {"expression": CHECK, "returnByValue": True})
            data = json.loads(r["result"]["value"])
            shot = await c.send("Page.captureScreenshot",
                                {"format": "png", "clip": {"x":0,"y":0,"width":1920,"height":1080,"scale":1}})
            (out / f"{f.stem}.png").write_bytes(base64.b64decode(shot["data"]))

            bad = []
            if data["overlaps"]:
                bad += [f'OVERLAP "{o["a"]}" x "{o["b"]}" ({o["ox"]}x{o["oy"]}px)' for o in data["overlaps"][:4]]
            if data["overflow"]:
                bad += [f'OVERFLOW "{o["text"]}" bottom={o["bottom"]}' for o in data["overflow"][:4]]
            if data["tiny"]:
                bad += [f'TINY {t["size"]}px "{t["text"]}"' for t in data["tiny"][:3]]
            problems += len(bad)
            status = "ok" if not bad else "\n      " + "\n      ".join(bad)
            print(f"  {f.stem:16} fill {data['fill']:4d}/1080  {status}")
    finally:
        c.close()
    print(f"\n  {problems} problem(s)")
    return 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
