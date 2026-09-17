"""
FastAPI-приложение FireWatch AI: дашборд + API.

Роуты:
  GET  /                     — дашборд: живой поток + статус + редактор зоны
  GET  /video_feed           — MJPEG-стрим обработанного видео (боксы + зоны)
  GET  /frame.jpg            — текущий «чистый» кадр (фон для редактора зон)
  GET  /events               — таблица событий (фильтр по дате/зоне)
  GET  /api/status           — JSON статуса пайплайна и зон
  GET  /api/events           — JSON списка событий
  GET  /api/zones            — текущие зоны (нормализованные полигоны)
  POST /api/zones            — сохранить зоны из UI-редактора
  POST /api/events/{id}/permit — привязать номер наряда к событию
  POST /api/permit/last      — привязать номер наряда к последнему событию
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_config, save_zones
from .db import EventStore
from .pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("firewatch.main")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()

    # если это дефолтное демо-видео и его нет — сгенерируем на лету
    src = cfg.path(cfg.video_source)
    if str(cfg.video_source).endswith("demo_fire.mp4") and not src.exists():
        try:
            from scripts.make_demo_video import generate
            log.info("Генерирую демо-видео: %s", src)
            generate(str(src))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сгенерировать демо-видео: %s", e)

    store = EventStore(BASE_DIR / "data" / "firewatch.db")
    pipeline = Pipeline(cfg, store)
    pipeline.start()

    app.state.cfg = cfg
    app.state.store = store
    app.state.pipeline = pipeline
    log.info("FireWatch готов. Дашборд: http://127.0.0.1:8000/")
    try:
        yield
    finally:
        pipeline.stop()


app = FastAPI(title="FireWatch AI", lifespan=lifespan)

# статика и снапшоты доступны напрямую
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
(BASE_DIR / "snapshots").mkdir(exist_ok=True)
app.mount("/snapshots", StaticFiles(directory=str(BASE_DIR / "snapshots")), name="snapshots")


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "index.html", {"active": "monitor"}
    )


@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    return TEMPLATES.TemplateResponse(request, "guide.html", {"active": "guide"})


@app.get("/video_feed")
async def video_feed(request: Request):
    pipeline: Pipeline = request.app.state.pipeline
    target_fps = max(1, request.app.state.cfg.target_fps)

    def gen():
        boundary = b"--frame\r\n"
        interval = 1.0 / target_fps
        while True:
            jpeg = pipeline.get_jpeg()
            if jpeg is not None:
                yield (boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(interval)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/frame.jpg")
async def frame_jpg(request: Request):
    pipeline: Pipeline = request.app.state.pipeline
    jpeg = pipeline.get_clean_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=503, detail="Кадр ещё не готов")
    return Response(content=jpeg, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# API: статус и зоны
# ---------------------------------------------------------------------------
@app.get("/api/status")
async def api_status(request: Request):
    return JSONResponse(request.app.state.pipeline.status())


@app.get("/api/zones")
async def api_get_zones(request: Request):
    cfg = request.app.state.cfg
    return JSONResponse([{"id": z.id, "polygon": z.polygon} for z in cfg.zones])


@app.post("/api/zones")
async def api_save_zones(request: Request):
    cfg = request.app.state.cfg
    body = await request.json()
    zones = body.get("zones", [])
    # минимальная валидация
    cleaned = []
    for z in zones:
        poly = z.get("polygon", [])
        if len(poly) < 3:
            continue
        cleaned.append({"id": str(z.get("id", "A")), "polygon": poly})
    if not cleaned:
        raise HTTPException(status_code=400, detail="Нужна хотя бы одна зона из >= 3 точек")
    save_zones(cfg, cleaned)
    request.app.state.pipeline.reload_zones()
    return {"ok": True, "zones": cleaned}


# ---------------------------------------------------------------------------
# API: события и наряд
# ---------------------------------------------------------------------------
@app.get("/api/events")
async def api_events(request: Request, zone_id: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     limit: int = 200):
    store: EventStore = request.app.state.store
    rows = store.list_events(zone_id=zone_id, date_from=date_from,
                             date_to=date_to, limit=limit)
    return JSONResponse(rows)


@app.post("/api/events/{event_id}/permit")
async def api_set_permit(request: Request, event_id: int, permit_number: str = Form(...)):
    store: EventStore = request.app.state.store
    if not store.set_permit(event_id, permit_number):
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return {"ok": True, "event_id": event_id, "permit_number": permit_number}


@app.post("/api/permit/last")
async def api_set_permit_last(request: Request, permit_number: str = Form(...)):
    store: EventStore = request.app.state.store
    last = store.get_last_event()
    if not last:
        raise HTTPException(status_code=404, detail="Событий пока нет")
    store.set_permit(int(last["id"]), permit_number)
    return {"ok": True, "event_id": last["id"], "permit_number": permit_number}


# ---------------------------------------------------------------------------
# Страница событий
# ---------------------------------------------------------------------------
@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, zone_id: str | None = None,
                      date_from: str | None = None, date_to: str | None = None):
    store: EventStore = request.app.state.store
    rows = store.list_events(zone_id=zone_id or None,
                             date_from=date_from or None,
                             date_to=date_to or None, limit=500)
    return TEMPLATES.TemplateResponse(
        request, "events.html",
        {"active": "events", "events": rows,
         "filters": {"zone_id": zone_id or "", "date_from": date_from or "",
                     "date_to": date_to or ""}},
    )
