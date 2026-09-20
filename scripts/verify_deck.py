"""Step through the built presenter slide by slide and report what is wrong."""
from __future__ import annotations
import asyncio, base64, json, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9555
FILE = Path(sys.argv[1]).resolve()
OUT = Path("/tmp/deck-verify"); OUT.mkdir(exist_ok=True)

CHECK = r"""
(() => {
  const s = [...document.querySelectorAll('section')].find(x => x.classList.contains('on'));
  if (!s) return JSON.stringify({err:'no active slide'});
  const vids = [...s.querySelectorAll('video')].map(v => ({
    playing: !v.paused && v.currentTime > 0, ready: v.readyState, dur: Math.round(v.duration||0),
    w: v.videoWidth, h: v.videoHeight}));
  const TEXT=['H1','H2','H3','P','LI','TD','TH'];
  const boxes=[...s.querySelectorAll('*')].filter(n=>TEXT.includes(n.tagName)&&n.textContent.trim().length>1)
    .map(n=>{const r=n.getBoundingClientRect();return{t:n.textContent.trim().slice(0,40),x:r.left,y:r.top,w:r.width,h:r.height,
      fs:parseFloat(getComputedStyle(n).fontSize)};});
  const ov=[];
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];
    const ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x), oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
    if(ox>4&&oy>4)ov.push(a.t+' / '+b.t);}
  const sr = s.getBoundingClientRect();
  const off = boxes.filter(b=>b.y+b.h>sr.bottom+2||b.x+b.w>sr.right+2).map(b=>b.t);
  const tiny = boxes.filter(b=>b.fs<23.5*(sr.width/1920)).map(b=>b.t);
  const font = getComputedStyle(s.querySelector('h1,h2,p')||s).fontFamily;
  return JSON.stringify({id:s.id, vids, ov, off, tiny, font, n:boxes.length});
})()
"""

class C:
    def __init__(s): s.p=None; s.ws=None; s.i=0
    def launch(s):
        pr=Path("/tmp/verify-profile"); shutil.rmtree(pr,ignore_errors=True)
        s.p=subprocess.Popen([CHROME,"--headless=new","--disable-gpu","--hide-scrollbars","--mute-audio",
            "--autoplay-policy=no-user-gesture-required","--no-first-run",f"--user-data-dir={pr}",
            f"--remote-debugging-port={PORT}","--window-size=1600,900","about:blank"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(60):
            time.sleep(.4)
            try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version",timeout=1); return
            except Exception: pass
        raise RuntimeError("chrome")
    async def conn(s):
        t=[x for x in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list").read()) if x["type"]=="page"][0]
        s.ws=await websockets.connect(t["webSocketDebuggerUrl"],max_size=200*1024*1024)
        await s.send("Page.enable"); await s.send("Runtime.enable")
    async def send(s,m,p=None):
        s.i+=1; mid=s.i
        await s.ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
        while True:
            r=json.loads(await s.ws.recv())
            if r.get("id")!=mid: continue
            if "error" in r: raise RuntimeError(f"{m}: {r['error']}")
            return r.get("result",{})
    async def js(s,e):
        r=await s.send("Runtime.evaluate",{"expression":e,"returnByValue":True})
        return r["result"].get("value")
    def close(s): s.p and s.p.terminate()

async def main():
    c=C(); c.launch(); await c.conn()
    await c.send("Page.navigate",{"url":FILE.as_uri()})
    await asyncio.sleep(5)
    n=await c.js("document.querySelectorAll('section').length")
    print(f"  {n} slides in {FILE.name}")
    bad=0
    try:
        for k in range(n):
            if k: await c.js(f"show({k})")
            await asyncio.sleep(1.6)
            d=json.loads(await c.js(CHECK))
            shot=await c.send("Page.captureScreenshot",{"format":"png"})
            (OUT/f"{k:02d}-{d['id']}.png").write_bytes(base64.b64decode(shot["data"]))
            msgs=[]
            for v in d["vids"]:
                if not v["playing"]: msgs.append(f"VIDEO NOT PLAYING (ready={v['ready']} {v['w']}x{v['h']})")
                elif v["w"]==0: msgs.append("VIDEO NO DIMENSIONS")
            if d["ov"]: msgs += ["OVERLAP "+o for o in d["ov"][:3]]
            if d["off"]: msgs += ["OFFCANVAS "+o for o in d["off"][:3]]
            if "Space Grotesk" not in d["font"] and "Plex" not in d["font"]:
                msgs.append("FONT FALLBACK: "+d["font"][:40])
            bad += len(msgs)
            print(f"  {k+1:2d}. {d['id']:12} {len(d['vids'])}v {d['n']:2d}txt  " +
                  ("ok" if not msgs else "\n      " + "\n      ".join(msgs)))
    finally: c.close()
    print(f"\n  {bad} problem(s)")
    return 0
if __name__=="__main__": sys.exit(asyncio.run(main()))
