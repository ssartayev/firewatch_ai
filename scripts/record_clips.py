"""
Record the pitch clips by driving the real product in a real browser.

Nothing here is mocked up. Chrome is launched with its debugging port open,
pointed at the running server, driven through the same buttons a person would
press, and screenshotted frame by frame. The frames are then encoded to MP4.
So every clip in the deck is the software actually running.

    python scripts/record_clips.py            # all clips
    python scripts/record_clips.py hero fire  # just those

Requires: the app running on :8010, and the Chrome + ffmpeg binaries that
Playwright already downloaded (no pip installs).
"""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "deck" / "clips"
APP = "http://127.0.0.1:8010"

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
VIEW = (1600, 900)
FPS = 12


# --------------------------------------------------------------------------
# tiny CDP client
# --------------------------------------------------------------------------
class Chrome:
    def __init__(self) -> None:
        self.proc = None
        self.ws = None
        self._id = 0

    def launch(self) -> None:
        profile = Path("/tmp/sense-rec-profile")
        shutil.rmtree(profile, ignore_errors=True)
        self.proc = subprocess.Popen([
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--mute-audio", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required",
            "--force-device-scale-factor=1",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={PORT}",
            f"--window-size={VIEW[0]},{VIEW[1]}",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            time.sleep(0.4)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                return
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError("Chrome did not open its debugging port")

    async def connect(self) -> None:
        raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list").read()
        targets = [t for t in json.loads(raw) if t["type"] == "page"]
        url = targets[0]["webSocketDebuggerUrl"]
        self.ws = await websockets.connect(url, max_size=64 * 1024 * 1024)
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("DOM.enable")

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method,
                                       "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") != mid:
                continue          # an event, or another command's reply
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result", {})

    async def goto(self, path: str, settle: float = 2.6) -> None:
        await self.send("Page.navigate", {"url": APP + path})
        await asyncio.sleep(settle)

    async def js(self, expression: str) -> None:
        await self.send("Runtime.evaluate",
                        {"expression": expression, "awaitPromise": True})

    async def shot(self) -> bytes:
        r = await self.send("Page.captureScreenshot",
                            {"format": "jpeg", "quality": 92,
                             "captureBeyondViewport": False})
        return base64.b64decode(r["data"])

    async def upload(self, selector: str, file_path: str) -> None:
        doc = await self.send("DOM.getDocument")
        node = await self.send("DOM.querySelector",
                               {"nodeId": doc["root"]["nodeId"],
                                "selector": selector})
        await self.send("DOM.setFileInputFiles",
                        {"files": [file_path], "nodeId": node["nodeId"]})

    def close(self) -> None:
        if self.proc:
            self.proc.terminate()


def api(action: str, **kw) -> None:
    body = json.dumps({"action": action, **kw}).encode()
    req = urllib.request.Request(APP + "/api/control", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def sim_time() -> float:
    with urllib.request.urlopen(APP + "/api/state", timeout=10) as r:
        return json.load(r)["t"]


async def wait_until(t: float, timeout: float = 90) -> None:
    start = time.time()
    while sim_time() < t and time.time() - start < timeout:
        await asyncio.sleep(0.25)


# --------------------------------------------------------------------------
# capture + encode
# --------------------------------------------------------------------------
async def capture(chrome: Chrome, name: str, seconds: float,
                  crop: tuple[int, int, int, int] | None = None) -> Path:
    """
    Record with Chrome's screencast, which pushes frames as they are painted.

    Taking screenshots in a loop only managed about six frames a second, which
    makes animated routes stutter. The screencast keeps up with the page.
    """
    import cv2
    import numpy as np

    frames: list[bytes] = []
    started = time.time()

    await chrome.send("Page.startScreencast", {
        "format": "jpeg", "quality": 80,
        "maxWidth": VIEW[0], "maxHeight": VIEW[1], "everyNthFrame": 1,
    })
    try:
        while time.time() - started < seconds:
            try:
                raw = await asyncio.wait_for(chrome.ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("method") != "Page.screencastFrame":
                continue
            frames.append(base64.b64decode(msg["params"]["data"]))
            await chrome.send("Page.screencastFrameAck",
                              {"sessionId": msg["params"]["sessionId"]})
    finally:
        await chrome.send("Page.stopScreencast")

    elapsed = time.time() - started
    if not frames:
        raise RuntimeError(f"{name}: no frames captured")
    fps = max(len(frames) / max(elapsed, 0.01), 1.0)

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name}.mp4"

    first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    if crop:
        cx, cy, cw, ch = crop
        w, h = cw, ch
    # Even dimensions are required by H.264.
    tw = 1280
    th = int(round(h * tw / w / 2)) * 2

    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"avc1"), fps, (tw, th))
    if not writer.isOpened():
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (tw, th))
    for buf in frames:
        img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        if crop:
            cx, cy, cw, ch = crop
            img = img[cy:cy + ch, cx:cx + cw]
        writer.write(cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA))
    writer.release()

    kb = out.stat().st_size / 1024
    print(f"  {name:22} {len(frames):4d} frames  {fps:4.1f} fps  {elapsed:4.1f}s  {kb:6.0f} KB")
    return out


# --------------------------------------------------------------------------
# the clips
# --------------------------------------------------------------------------
HIDE_CHROME = """
  (() => {
    const t = document.querySelector('.topbar'); if (t) t.style.display='none';
    document.querySelectorAll('footer.foot').forEach(f => f.style.display='none');
    document.querySelectorAll('.shell').forEach(s => s.style.padding='14px 18px 0');
    window.scrollTo(0,0);
  })();
"""

# On the live page: drop the headline block and the tab strip, so the frame is
# the sentence, the three numbers and the floor plan — the whole story.
LIVE_FRAME = """
  (() => {
    const kids = document.querySelectorAll('.shell > div');
    if (kids[0]) kids[0].style.display = 'none';          // headline + buttons
    const grid = document.querySelector('.grid.g-main');
    if (grid) grid.style.display = 'none';                 // tabs + timeline
    const plan = document.querySelector('.planwrap');
    if (plan) plan.querySelector('svg').style.maxHeight = '58vh';
  })();
"""


async def clip_hero(c: Chrome) -> None:
    """The building at rest, then fire, detection and the first smoke."""
    api("reset"); api("speed", value=3)
    await c.goto("/")
    await c.js(HIDE_CHROME)
    await c.js(LIVE_FRAME)
    api("start")
    await capture(c, "01-hero", 9.0)


async def clip_redistribute(c: Chrome) -> None:
    """The moment the crowd ignores the plan and the system reallocates."""
    api("reset"); api("speed", value=8)
    await c.goto("/")
    await c.js(HIDE_CHROME)
    await c.js(LIVE_FRAME)
    api("start")
    await wait_until(44)
    api("speed", value=2.2)
    await capture(c, "02-redistribute", 10.0)


async def clip_blind(c: Chrome) -> None:
    """Cameras lose the room to smoke; the radar layer carries the count."""
    api("reset"); api("smoke", value=0)
    await c.goto("/occupancy")
    await c.js(HIDE_CHROME)
    await c.js("""
      document.querySelectorAll('.shell > div')[0].style.display='none';
      const p = document.querySelectorAll('.panel');
      for (const el of p) { const h = el.querySelector('h3');
        if (h && !/People in each room|Where that number/.test(h.textContent)) el.style.display='none'; }
      document.querySelectorAll('.grid.g-main > .stack')[1].style.display='none';
    """)
    await asyncio.sleep(0.8)

    async def ramp():
        for v in range(0, 96, 5):
            api("smoke", value=v / 100)
            await asyncio.sleep(0.42)
    task = asyncio.create_task(ramp())
    await capture(c, "03-blind", 9.0)
    await task
    api("smoke", value=0)


async def clip_upload(c: Chrome, video: str) -> None:
    """The detector running on the user's own footage."""
    api("reset")
    await c.goto("/occupancy")
    await c.js(HIDE_CHROME)
    await c.js("""
      document.querySelectorAll('.shell > div')[0].style.display='none';
      const p = document.querySelectorAll('.panel');
      for (const el of p) { const h = el.querySelector('h3');
        if (h && !/What the camera actually sees/.test(h.textContent)) el.style.display='none'; }
      document.querySelectorAll('.grid.g-main > .stack')[1].style.display='none';
      window.scrollTo(0,0);
    """)
    await c.upload("#clip", video)
    await asyncio.sleep(0.4)
    await c.js("document.getElementById('btn-analyse').click();")
    await asyncio.sleep(5.5)          # let the detector finish
    await c.js("window.scrollTo(0,0);")
    await capture(c, "04-upload", 8.0)


async def clip_responder(c: Chrome) -> None:
    """The arriving crew's view, mid-incident."""
    api("reset"); api("speed", value=8)
    await c.goto("/")
    api("start")
    await wait_until(100)
    api("speed", value=1.4)
    await c.goto("/command", settle=3.0)
    await c.js(HIDE_CHROME)
    await c.js("""
      const kids = document.querySelectorAll('.shell > div');
      if (kids[0]) kids[0].style.display = 'none';
      const sv = document.querySelector('#iso svg'); if (sv) sv.style.maxHeight='62vh';
    """)
    await asyncio.sleep(1.2)
    await capture(c, "05-responder", 8.0)


async def clip_routes(c: Chrome) -> None:
    """Breaking the building on purpose and watching the routes move."""
    api("scenario", name="normal")
    await c.goto("/evacuation")
    await c.js(HIDE_CHROME)
    await c.js("""
      const kids = document.querySelectorAll('.shell > div');
      if (kids[0]) kids[0].style.display = 'none';
      document.querySelectorAll('.tabs, .tabs + .panel').forEach(n => n.style.display='none');
      const sv = document.querySelector('.planwrap svg'); if (sv) sv.style.maxHeight='56vh';
    """)
    await asyncio.sleep(1.0)

    async def script():
        # Crowd one core and set a room alight, so the allocation visibly moves.
        # Fully blocking a corridor strands people, which is true to the model
        # but reads in a pitch as though the product caused it.
        await asyncio.sleep(2.2)
        api("ignite", node="R305")
        await asyncio.sleep(3.4)
        api("crowd", stair="STAIR-A")
        await asyncio.sleep(3.0)
        api("crowd", stair="STAIR-A")
    task = asyncio.create_task(script())
    await capture(c, "06-routes", 11.0)
    await task


async def clip_twin(c: Chrome) -> None:
    """
    The building model, with the device inspector filling in as rooms are
    picked. The tour is injected as a page timer rather than driven from here:
    the screencast owns the websocket while it records, so sending commands
    mid-capture deadlocks it.
    """
    api("reset")
    await c.goto("/twin")
    await c.js(HIDE_CHROME)
    await c.js("""
      const kids = document.querySelectorAll('.shell > div');
      if (kids[0]) kids[0].style.display = 'none';
      const sv = document.querySelector('.planwrap svg'); if (sv) sv.style.maxHeight='52vh';
      const rooms = ['R305','STAIR-C','R314','C4','R301'];
      let i = 0;
      window.__tour = setInterval(() => {
        const el = document.querySelector('[data-id="' + rooms[i % rooms.length] + '"]');
        if (el) el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
        i++;
      }, 1900);
    """)
    await asyncio.sleep(1.2)
    await capture(c, "07-twin", 9.5)
    await c.js("clearInterval(window.__tour)")


CLIPS = {
    "hero": clip_hero,
    "twin": clip_twin,
    "redistribute": clip_redistribute,
    "blind": clip_blind,
    "responder": clip_responder,
    "routes": clip_routes,
}


async def main() -> int:
    video = sys.argv[sys.argv.index("--video") + 1] if "--video" in sys.argv else None
    wanted = [a for a in sys.argv[1:] if not a.startswith("-") and a != video]

    chrome = Chrome()
    chrome.launch()
    await chrome.connect()
    print(f"recording into {OUT.relative_to(BASE)}")
    try:
        for name, fn in CLIPS.items():
            if wanted and name not in wanted:
                continue
            await fn(chrome)
        if video and (not wanted or "upload" in wanted):
            await clip_upload(chrome, video)
    finally:
        chrome.close()
        api("reset")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
