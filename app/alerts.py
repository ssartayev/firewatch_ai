"""
Алертинг: снапшот-доказательство + Telegram + webhook.

- Снапшот (кадр с уже отрисованными боксами и контуром зоны) сохраняется в
  snapshots/ и его путь пишется в БД.
- Сообщение уходит в Telegram (sendPhoto с подписью), если в .env заданы
  токен и chat_id.
- Если задан webhook_url — дублируем событие POST-запросом (JSON).

Отправка по сети выполняется в отдельном потоке, чтобы не тормозить пайплайн.
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
        return "найден ✅"
    if v is False:
        return "НЕ найден ❌"
    return "не проверяется —"


class Alerter:
    """Отправка алертов и сохранение снапшотов."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def save_snapshot(self, annotated_frame, event: FireEvent) -> tuple[str, Path]:
        """
        Сохранить кадр-доказательство. Возвращает (относительный_путь, абсолютный_путь).
        Относительный путь ('snapshots/...') пишется в БД и отдаётся дашбордом.
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
        type_ru = "ОГОНЬ" if event.event_type == "fire" else "ДЫМ"
        lines = [
            "🔥 FireWatch — ТРЕВОГА (огневые работы)",
            f"Зона: {event.zone_id}",
            f"Время: {when}",
            f"Тип: {type_ru} (уверенность {event.confidence:.2f})",
            f"Огнетушитель в зоне: {_cond(event.extinguisher_present)}",
            f"Наблюдающий в зоне: {_cond(event.observer_present)}",
            f"Наряд-допуск: {permit or '—'}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def dispatch(self, event: FireEvent, ts_iso: str, snapshot_abs: Path,
                 permit: Optional[str] = None) -> None:
        """Разослать алерт (в фоне, чтобы не блокировать пайплайн)."""
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
            log.warning("Telegram: ошибка отправки: %s", e)
        try:
            self._send_webhook(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("Webhook: ошибка отправки: %s", e)

    # ------------------------------------------------------------------
    def _send_telegram(self, text: str, photo_path: Path) -> None:
        if not self.cfg.telegram_ready:
            log.info("Telegram не настроен (нет токена/chat_id) — пропускаю отправку")
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
            log.info("Telegram: алерт отправлен")
        else:
            log.warning("Telegram: код %s, ответ %s", resp.status_code, resp.text[:200])

    def _send_webhook(self, payload: dict) -> None:
        url = self.cfg.alerts.webhook_url
        if not url:
            return
        resp = requests.post(url, json=payload, timeout=15)
        log.info("Webhook: код %s", resp.status_code)
