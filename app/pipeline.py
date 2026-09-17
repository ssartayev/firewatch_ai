"""
Video processing pipeline: ingest → inference → logic → alert → snapshot/DB.

Runs on its own thread (started by FastAPI on startup). It exposes:
  - the latest processed frame (JPEG) for the dashboard MJPEG stream;
  - a clean unannotated frame (background for the zone editor UI);
  - the current zone and detector status.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np

from .alerts import Alerter
from .config import Config
from .db import EventStore
from .detectors import (
    Detection,
    DetectorSet,
    EXTINGUISHER_LABEL,
    PERSON_LABEL,
)
from .rules import FireEvent, RuleEngine
from .zones import Zone, load_zones

log = logging.getLogger("firewatch.pipeline")

MAX_WIDTH = 960  # wider frames are downscaled (speed + stable annotation)

# Box colours (BGR)
_COLORS = {
    "fire": (0, 0, 255),
    "smoke": (150, 150, 150),
    PERSON_LABEL: (0, 200, 0),
    EXTINGUISHER_LABEL: (255, 140, 0),
}
_ZONE_COLOR = (255, 255, 0)
_ZONE_ALERT_COLOR = (0, 0, 255)


class Pipeline:
    def __init__(self, cfg: Config, store: EventStore):
        self.cfg = cfg
        self.store = store
        self.detectors = DetectorSet(cfg.models, cfg.detection.conf_threshold)
        self.rules = RuleEngine(
            persistence_frames=cfg.detection.persistence_frames,
            alert_cooldown_sec=cfg.detection.alert_cooldown_sec,
            near_zone_margin=cfg.detection.near_zone_margin,
            person_check_enabled=self.detectors.person_check_enabled,
            extinguisher_check_enabled=self.detectors.extinguisher_check_enabled,
        )
        self.alerter = Alerter(cfg)
        self.zones: list[Zone] = load_zones(cfg.zones)

        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None    # processed frame (annotated)
        self._clean_frame: Optional[np.ndarray] = None  # clean frame (for the zone editor)
        self._status: dict[str, dict] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frames = 0
        self._events_total = 0

    # ------------------------------------------------------------------
    # Lifecycle control
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="firewatch-pipeline")
        self._thread.start()
        log.info("Pipeline started. Source: %s", self.cfg.video_source)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def reload_zones(self) -> None:
        """Reload zones from the config (after editing them in the UI)."""
        self.zones = load_zones(self.cfg.zones)
        log.info("Zones reloaded: %s", [z.id for z in self.zones])

    # ------------------------------------------------------------------
    # Dashboard access (thread-safe)
    # ------------------------------------------------------------------
    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def get_clean_jpeg(self) -> Optional[bytes]:
        with self._lock:
            frame = None if self._clean_frame is None else self._clean_frame.copy()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes() if ok else None

    def status(self) -> dict:
        with self._lock:
            zones = dict(self._status)
        return {
            "running": self._running,
            "source": self.cfg.video_source,
            "frames_processed": self._frames,
            "events_total": self._events_total,
            "detectors": self.detectors.status(),
            "checks": {
                "fire": self.detectors.fire_check_enabled,
                "person": self.detectors.person_check_enabled,
                "extinguisher": self.detectors.extinguisher_check_enabled,
            },
            "telegram_ready": self.cfg.telegram_ready,
            "zones": zones,
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _open_capture(self) -> tuple[cv2.VideoCapture, bool, float]:
        src_raw = self.cfg.video_source
        if str(src_raw).isdigit():          # webcam index
            idx, is_file = int(src_raw), False
            # on macOS use the native AVFoundation backend explicitly
            if sys.platform == "darwin":
                cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
            else:
                cap = cv2.VideoCapture(idx)
        elif str(src_raw).lower().startswith(("rtsp://", "http://", "https://")):
            is_file = False
            cap = cv2.VideoCapture(src_raw)
        else:                               # local file
            is_file = True
            cap = cv2.VideoCapture(str(self.cfg.path(src_raw)))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if not (1.0 <= src_fps <= 120.0):
            src_fps = 25.0
        return cap, is_file, src_fps

    def _run(self) -> None:
        cap, is_file, src_fps = self._open_capture()
        if not cap.isOpened():
            log.error("Could not open video source: %s", self.cfg.video_source)
            if str(self.cfg.video_source).isdigit():
                log.error("This looks like a webcam. On macOS grant Camera permission "
                          "to your terminal: System Settings → Privacy & Security → Camera. "
                          "Run the server from your own terminal, not a background process.")
            self._running = False
            return

        target_interval = 1.0 / max(1, self.cfg.target_fps)
        src_interval = 1.0 / src_fps
        last_proc = 0.0

        while self._running:
            t0 = time.time()
            if not cap.grab():
                # end of file, or the stream dropped
                if is_file and self.cfg.loop_video:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                if is_file:
                    log.info("Video file ended — stopping the pipeline")
                    break
                # RTSP: try to reopen
                log.warning("Stream dropped, reconnecting…")
                cap.release()
                time.sleep(1.0)
                cap, is_file, src_fps = self._open_capture()
                continue

            now = time.time()
            if now - last_proc >= target_interval:
                last_proc = now
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    self._process_frame(frame)

            # for files, pace to the source FPS for a realtime feel; not for RTSP
            if is_file:
                time.sleep(max(0.0, src_interval - (time.time() - t0)))

        cap.release()
        self._running = False

    # ------------------------------------------------------------------
    def _process_frame(self, frame: np.ndarray) -> None:
        frame = self._resize(frame)
        h, w = frame.shape[:2]

        detections = self.detectors.run(frame)
        events, status = self.rules.process(detections, self.zones, (w, h))

        annotated = self._draw(frame, detections, status)

        # store the clean frame and the processed JPEG
        ok, buf = cv2.imencode(".jpg", annotated)
        with self._lock:
            self._clean_frame = frame.copy()
            if ok:
                self._latest_jpeg = buf.tobytes()
            self._status = status
            self._frames += 1

        # events → snapshot + DB + alert
        for ev in events:
            self._handle_event(ev, annotated)

    def _handle_event(self, ev: FireEvent, annotated: np.ndarray) -> None:
        ts_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snap_rel, snap_abs = self.alerter.save_snapshot(annotated, ev)
        self.store.insert_event(
            zone_id=ev.zone_id,
            event_type=ev.event_type,
            confidence=ev.confidence,
            extinguisher_present=ev.extinguisher_present,
            observer_present=ev.observer_present,
            snapshot_path=snap_rel,
            ts=ts_iso,
        )
        self.alerter.dispatch(ev, ts_iso, snap_abs)
        self._events_total += 1
        log.info("EVENT: zone=%s type=%s conf=%.2f extinguisher=%s observer=%s",
                 ev.zone_id, ev.event_type, ev.confidence,
                 ev.extinguisher_present, ev.observer_present)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    @staticmethod
    def _resize(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w > MAX_WIDTH:
            scale = MAX_WIDTH / w
            frame = cv2.resize(frame, (MAX_WIDTH, int(h * scale)))
        return frame

    def _draw(self, frame: np.ndarray, detections: list[Detection],
              status: dict[str, dict]) -> np.ndarray:
        img = frame.copy()
        h, w = img.shape[:2]

        # zones
        for zone in self.zones:
            st = status.get(zone.id, {})
            alert = st.get("persistent")
            color = _ZONE_ALERT_COLOR if alert else _ZONE_COLOR
            pts = zone.pixel_polygon(w, h).reshape((-1, 1, 2))
            cv2.polylines(img, [pts], isClosed=True, color=color, thickness=3)
            x0, y0 = zone.pixel_polygon(w, h)[0]
            cv2.putText(img, f"ZONE {zone.id}", (int(x0), max(20, int(y0) - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # detections
        for d in detections:
            x1, y1, x2, y2 = d.bbox
            color = _COLORS.get(d.label, (200, 200, 200))
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(img, f"{d.label} {d.confidence:.2f}", (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # top bar with the timestamp + per-zone status line
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.rectangle(img, (0, 0), (w, 26), (0, 0, 0), -1)
        cv2.putText(img, f"FireWatch  {now_str}", (8, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        y = 48
        for zid, st in status.items():
            if st.get("fire_active"):
                etype = str(st.get("event_type")).upper()
                state = "PERSISTENT" if st.get("persistent") else f"streak {st.get('streak')}"
                ext = _short(st.get("extinguisher_present"))
                obs = _short(st.get("observer_present"))
                txt = f"ZONE {zid}: {etype} {state} | ext:{ext} obs:{obs}"
                cv2.putText(img, txt, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (0, 0, 255) if st.get("persistent") else (0, 200, 255), 2)
                y += 24
        return img


def _short(v: Optional[bool]) -> str:
    return "YES" if v is True else ("NO" if v is False else "?")
