"""
SENSE AI — FastAPI application.

The simulation runs in a background task on the server, not in the browser.
Every connected client watches the same building state, which is the point:
the floor plan, the occupancy table and the responder view are three windows
onto one model, and they cannot disagree.

Pages
  /              live floor plan and the full emergency simulation
  /occupancy     occupancy intelligence and the video-analysis panel
  /evacuation    interactive routing and scenario controls
  /command       firefighter decision-support view
  /twin          building digital twin and device inspector
  /technology    architecture, model positioning and limitations

API
  GET  /api/building          static digital-twin structure
  GET  /api/state             current simulation snapshot
  GET  /api/responder         responder decision support
  GET  /api/vision/status     which detector backend is live
  POST /api/vision/analyse    run the detector over an uploaded clip
  POST /api/control           start / reset / speed / scenario / interventions
  WS   /ws                    state stream, ~5 updates per second
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .simulation import Simulation
from .vision import ENGINE

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sense.main")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Real seconds between pushes. The simulation advances `speed` simulated
# seconds per real second, so the incident plays at a watchable pace without
# changing any of the physics.
PUSH_INTERVAL = 0.2


class Hub:
    """Fan-out of simulation state to every connected client."""

    def __init__(self) -> None:
        self.sim = Simulation()
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.sim.estimate()
        self.sim.make_plan("baseline plan for the building at rest")

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_text(json.dumps({"type": "state", "data": self.sim.snapshot()}))

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self) -> None:
        if not self.clients:
            return
        payload = json.dumps({"type": "state", "data": self.sim.snapshot()})
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def loop(self) -> None:
        """Advance the simulation in real time and push the result."""
        carry = 0.0
        while True:
            await asyncio.sleep(PUSH_INTERVAL)
            async with self.lock:
                if self.sim.running:
                    carry += PUSH_INTERVAL * self.sim.speed
                    from .simulation import TICK_SECONDS
                    while carry >= TICK_SECONDS:
                        carry -= TICK_SECONDS
                        self.sim.tick()
                else:
                    carry = 0.0
            await self.broadcast()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


hub = Hub()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    hub.start()
    log.info("SENSE AI ready — http://127.0.0.1:8010/")
    try:
        yield
    finally:
        await hub.stop()


app = FastAPI(title="SENSE AI", version="0.3.0-mvp", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def asset_version() -> str:
    """
    A fingerprint of the current CSS and JS, recomputed on every request.

    Without this the browser keeps its cached copy of the stylesheet and the
    scripts and never asks whether they changed — which means an edit can be
    live on the server and invisible in the window, and the person demoing is
    left staring at a bug that was fixed an hour ago. Appending this to the
    asset URLs makes a changed file a different URL, so there is nothing to
    reuse. It is recomputed per request because this is a demo that gets
    edited while it is running.
    """
    latest = 0.0
    for f in (BASE_DIR / "static").rglob("*"):
        if f.suffix in {".css", ".js"}:
            try:
                latest = max(latest, f.stat().st_mtime)
            except OSError:
                pass
    return str(int(latest))


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    """Let the browser cache assets, but never without checking first."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


def page(request: Request, name: str, active: str, **extra) -> HTMLResponse:
    ctx = {"active": active, "building": hub.sim.building.as_dict(),
           "asset_v": asset_version()}
    ctx.update(extra)
    return TEMPLATES.TemplateResponse(request, name, ctx)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return page(request, "index.html", "live")


@app.get("/occupancy", response_class=HTMLResponse)
async def occupancy(request: Request):
    return page(request, "occupancy.html", "occupancy", vision=ENGINE.status())


@app.get("/evacuation", response_class=HTMLResponse)
async def evacuation(request: Request):
    return page(request, "evacuation.html", "evacuation")


@app.get("/command", response_class=HTMLResponse)
async def command(request: Request):
    return page(request, "command.html", "command")


@app.get("/twin", response_class=HTMLResponse)
async def twin(request: Request):
    return page(request, "twin.html", "twin")


@app.get("/technology", response_class=HTMLResponse)
async def technology(request: Request):
    return page(request, "technology.html", "technology", vision=ENGINE.status())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/building")
async def api_building():
    return JSONResponse(hub.sim.building.as_dict())


@app.get("/api/state")
async def api_state():
    return JSONResponse(hub.sim.snapshot())


@app.get("/api/responder")
async def api_responder():
    return JSONResponse(hub.sim.responder_intel())


@app.get("/api/vision/status")
async def api_vision_status():
    return JSONResponse(ENGINE.status())


@app.post("/api/vision/analyse")
async def api_vision_analyse(clip: UploadFile = File(...)):
    """Run the configured detector over an uploaded clip."""
    suffix = Path(clip.filename or "clip.mp4").suffix or ".mp4"
    if suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="Expected a video file")
    tmp = Path(tempfile.mkdtemp()) / f"upload{suffix}"
    try:
        with tmp.open("wb") as fh:
            shutil.copyfileobj(clip.file, fh)
        result = await asyncio.to_thread(ENGINE.analyse_video, tmp)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return JSONResponse(result)


@app.post("/api/vision/simulate")
async def api_vision_simulate():
    return JSONResponse(ENGINE.simulate(reason="requested demonstration sequence"))


SCENARIOS = {
    "normal": "Normal evacuation — no hazard, baseline allocation",
    "fire_exit_a": "Fire near Exit A — the west route degrades",
    "stair_crowding": "Stair crowding — the upper floors overload one core",
    "camera_failure": "Camera failure under smoke — the visual layer drops out",
    "multi_hazard": "Multiple simultaneous hazards — two compartments involved",
}


@app.post("/api/control")
async def api_control(request: Request):
    body = await request.json()
    action = str(body.get("action", ""))
    sim = hub.sim

    async with hub.lock:
        if action == "start":
            sim.start_incident()
        elif action == "reset":
            sim.reset()
            sim.estimate()
            sim.make_plan("baseline plan for the building at rest")
        elif action == "pause":
            sim.running = False
        elif action == "resume":
            if sim.mode != "cleared":
                sim.running = True
        elif action == "speed":
            sim.speed = max(0.5, min(float(body.get("value", 2.0)), 8.0))
        elif action == "ignite":
            node = str(body.get("node", "R305"))
            sim.hazard.ignite(node, sim.t)
            sim.running = True
            if sim.mode == "normal":
                sim.mode = "incident"
            sim._log("hazard", f"Manual ignition in {node}", severity="critical")
        elif action == "block":
            node = str(body.get("node", "C4"))
            if node in sim.hazard.state.blocked:
                sim.hazard.unblock(node)
                sim._log("hazard", f"{node} reopened")
            else:
                sim.hazard.block(node)
                sim._log("hazard", f"{node} blocked — route unusable", severity="warn")
            sim.estimate()
            sim.make_plan(f"corridor {node} blocked")
        elif action == "smoke":
            sim.manual_smoke = max(0.0, min(float(body.get("value", 0.0)), 1.0))
            sim.estimate()
        elif action == "crowd":
            # Push extra people into one core to see the allocation respond.
            stair = str(body.get("stair", "STAIR-A"))
            sim.observed_flow[stair] = sim.observed_flow.get(stair, 0.0) + 1.4
            sim._log("flow", f"Crowding injected at Stair {stair[-1]}", severity="warn")
            sim.estimate()
            sim.make_plan(f"measured crowding at Stair {stair[-1]}")
        elif action == "camera_fail":
            nodes = body.get("nodes") or ["R305", "C4", "R314"]
            for n in nodes:
                if n in sim.camera_offline:
                    sim.camera_offline.discard(n)
                else:
                    sim.camera_offline.add(n)
            sim.estimate()
            sim._log("sensor", f"Camera state toggled: {', '.join(nodes)}",
                     severity="warn")
        elif action == "replan":
            sim.estimate()
            sim.make_plan("operator requested a replan")
        elif action == "scenario":
            name = str(body.get("name", "normal"))
            if name not in SCENARIOS:
                raise HTTPException(status_code=400, detail="Unknown scenario")
            _apply_scenario(sim, name)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")

    await hub.broadcast()
    return JSONResponse({"ok": True, "action": action, "state": sim.snapshot()})


def _apply_scenario(sim: Simulation, name: str) -> None:
    """Reset and configure one of the named test scenarios."""
    sim.reset()
    sim.estimate()
    sim._log("system", f"Scenario loaded — {SCENARIOS[name]}")

    if name == "normal":
        sim.mode = "clearing"
        sim.alarm_t = sim.t
        from .simulation import PREMOVE, PREMOVE_JITTER
        for room, delay in PREMOVE.items():
            sim.premove[room] = sim.t + delay * 0.5
        sim.script = []
    elif name == "fire_exit_a":
        sim.hazard.ignite("R301", sim.t, intensity=0.4)
        sim.hazard.inject_smoke("C1", 0.45)
        sim.script = []
        sim.mode = "clearing"
        sim.alarm_t = sim.t
    elif name == "stair_crowding":
        sim.observed_flow["STAIR-A"] = 2.1
        sim.script = []
        sim.mode = "clearing"
        sim.alarm_t = sim.t
    elif name == "camera_failure":
        sim.hazard.ignite("R305", sim.t, intensity=0.5)
        sim.hazard.inject_smoke("C4", 0.55)
        sim.camera_offline.update({"R305", "C4", "R314", "R306"})
        sim.script = []
        sim.mode = "clearing"
        sim.alarm_t = sim.t
    elif name == "multi_hazard":
        sim.hazard.ignite("R305", sim.t, intensity=0.45)
        sim.hazard.ignite("R311", sim.t, intensity=0.35)
        sim.hazard.inject_smoke("C4", 0.4)
        sim.hazard.inject_smoke("C1", 0.35)
        sim.script = []
        sim.mode = "clearing"
        sim.alarm_t = sim.t

    if name != "normal":
        from .simulation import PREMOVE
        for room, delay in PREMOVE.items():
            sim.premove[room] = sim.t + delay * 0.4
    sim.estimate()
    sim.make_plan(SCENARIOS[name])
    sim.running = True


@app.get("/api/scenarios")
async def api_scenarios():
    return JSONResponse([{"id": k, "label": v} for k, v in SCENARIOS.items()])


@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:  # noqa: BLE001
        hub.disconnect(ws)
