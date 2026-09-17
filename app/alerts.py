"""
Alerting: proof snapshot + Telegram + webhook.

- The snapshot (a frame with boxes and the zone outline already drawn) is
  saved to snapshots/ and its path is written to the database.
- The message is sent to Telegram (sendPhoto with a caption) if the token
  and chat_id are set in .env.
- If webhook_url is set, the event is also POSTed as JSON.

Network delivery runs on a separate thread so the pipeline never blocks.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import requests

from .config import Config
from .rules import FireEvent

log = logging.getLogger("firewatch.alerts")

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "snapshots"


def _cond(v: Optional[bool]) -> str:
    if v is True:
        return "present ✅"
    if v is False:
        return "NOT present ❌"
    return "not checked —"


class Alerter:
    """Sends alerts and saves snapshots."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def save_snapshot(self, annotated_frame, event: FireEvent) -> tuple[str, Path]:
        """
        Save the proof frame. Returns (relative_path, absolute_path).
        The relative path ('snapshots/...') is stored in the DB and served by the dashboard.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        fname = f"{ts}_zone{event.zone_id}_{event.event_type}.jpg"
        abs_path = SNAPSHOTS_DIR / fname
        cv2.imwrite(str(abs_path), annotated_frame)
        return f"snapshots/{fname}", abs_path

    # ------------------------------------------------------------------
    def build_message(self, event: FireEvent, ts_iso: str,
                      permit: Optional[str] = None) -> str:
        when = ts_iso.replace("T", " ")
        type_label = "FIRE" if event.event_type == "fire" else "SMOKE"
        lines = [
            "🔥 FireWatch — ALERT (hot work)",
            f"Zone: {event.zone_id}",
            f"Time: {when}",
            f"Type: {type_label} (confidence {event.confidence:.2f})",
            f"Extinguisher in zone: {_cond(event.extinguisher_present)}",
            f"Observer in zone: {_cond(event.observer_present)}",
            f"Work permit: {permit or '—'}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def dispatch(self, event: FireEvent, ts_iso: str, snapshot_abs: Path,
                 permit: Optional[str] = None) -> None:
        """Dispatch the alert (in the background so the pipeline is not blocked)."""
        text = self.build_message(event, ts_iso, permit)
        payload = {
            "zone_id": event.zone_id,
            "event_type": event.event_type,
            "confidence": round(event.confidence, 3),
            "extinguisher_present": event.extinguisher_present,
            "observer_present": event.observer_present,
            "permit_number": permit,
            "timestamp": ts_iso,
            "snapshot": str(snapshot_abs.name),
        }
        t = threading.Thread(
            target=self._send_all, args=(text, snapshot_abs, payload), daemon=True
        )
        t.start()

    def _send_all(self, text: str, snapshot_abs: Path, payload: dict) -> None:
        try:
            self._send_telegram(text, snapshot_abs)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: send failed: %s", e)
        try:
            self._send_webhook(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("Webhook: send failed: %s", e)

    # ------------------------------------------------------------------
    def _send_telegram(self, text: str, photo_path: Path) -> None:
        if not self.cfg.telegram_ready:
            log.info("Telegram not configured (missing token/chat_id) — skipping send")
            return
        url = f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendPhoto"
        with open(photo_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": self.cfg.telegram_chat_id, "caption": text},
                files={"photo": f},
                timeout=15,
            )
        if resp.status_code == 200:
            log.info("Telegram: alert sent")
        else:
            log.warning("Telegram: status %s, response %s", resp.status_code, resp.text[:200])

    def _send_webhook(self, payload: dict) -> None:
        url = self.cfg.alerts.webhook_url
        if not url:
            return
        resp = requests.post(url, json=payload, timeout=15)
        log.info("Webhook: status %s", resp.status_code)
