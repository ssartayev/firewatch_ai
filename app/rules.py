"""
Правила пожаробезопасности + состояние/дебаунс.

Ключевые идеи (анти-FP + осмысленный алерт):
  1. Огонь/дым считаются валидными, только если центр детекции ВНУТРИ зоны.
  2. Событие срабатывает, лишь когда огонь/дым держится >= persistence_frames
     кадров ПОДРЯД — одиночный ложный кадр (искра, блик) не создаёт алерт.
  3. Повторный алерт по той же зоне — не чаще alert_cooldown_sec (дебаунс).
  4. Для валидного события проверяются видимые условия наряда:
       - есть ли огнетушитель в зоне/рядом;
       - есть ли человек-наблюдающий в зоне.
     Если соответствующая модель не настроена — условие = None («не проверяется»).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .detectors import (
    Detection,
    EXTINGUISHER_LABEL,
    FIRE_LABELS,
    PERSON_LABEL,
)
from .zones import Zone


@dataclass
class FireEvent:
    """Готовое к алерту событие пожароопасности."""
    zone_id: str
    event_type: str                       # 'fire' | 'smoke'
    confidence: float
    extinguisher_present: Optional[bool]  # None = проверка не настроена
    observer_present: Optional[bool]
    detections: list[Detection] = field(default_factory=list)  # для отрисовки снапшота


@dataclass
class _ZoneState:
    streak: int = 0                       # сколько кадров подряд горит
    best_conf: float = 0.0
    best_type: str = "fire"
    last_alert_ts: float = 0.0


class RuleEngine:
    """Хранит состояние по зонам и решает, когда слать алерт."""

    def __init__(
        self,
        *,
        persistence_frames: int,
        alert_cooldown_sec: int,
        near_zone_margin: float,
        person_check_enabled: bool,
        extinguisher_check_enabled: bool,
    ):
        self.persistence_frames = max(1, int(persistence_frames))
        self.cooldown = float(alert_cooldown_sec)
        self.margin = float(near_zone_margin)
        self.person_check = person_check_enabled
        self.ext_check = extinguisher_check_enabled
        self._state: dict[str, _ZoneState] = {}

    def _st(self, zone_id: str) -> _ZoneState:
        return self._state.setdefault(zone_id, _ZoneState())

    def process(
        self,
        detections: list[Detection],
        zones: list[Zone],
        frame_wh: tuple[int, int],
        now: Optional[float] = None,
    ) -> tuple[list[FireEvent], dict[str, dict]]:
        """
        Обработать один кадр.
        Возвращает (список событий для алерта, статус зон для дашборда).
        """
        now = time.time() if now is None else now
        w, h = frame_wh
        events: list[FireEvent] = []
        status: dict[str, dict] = {}

        # заранее разложим детекции по категориям (в нормализованных центрах)
        fires = [(d, d.norm_center(w, h)) for d in detections if d.label in FIRE_LABELS]
        persons = [(d, d.norm_center(w, h)) for d in detections if d.label == PERSON_LABEL]
        exts = [(d, d.norm_center(w, h)) for d in detections if d.label == EXTINGUISHER_LABEL]

        for zone in zones:
            st = self._st(zone.id)

            # --- огонь/дым внутри зоны ---
            in_zone = [(d, c) for (d, c) in fires if zone.contains_norm(*c)]
            if in_zone:
                # берём самую уверенную детекцию; fire приоритетнее smoke
                best = max(in_zone, key=lambda dc: (dc[0].label == "fire", dc[0].confidence))[0]
                st.streak += 1
                st.best_conf = best.confidence
                st.best_type = best.label
            else:
                st.streak = 0
                st.best_conf = 0.0

            persistent = st.streak >= self.persistence_frames

            # --- условия наряда ---
            observer_present = self._observer(zone, persons) if self.person_check else None
            extinguisher_present = self._extinguisher(zone, exts) if self.ext_check else None

            # --- решение об алерте (валидно + дебаунс) ---
            fire_now = bool(in_zone)
            alert = False
            if persistent and (now - st.last_alert_ts >= self.cooldown):
                alert = True
                st.last_alert_ts = now
                events.append(FireEvent(
                    zone_id=zone.id,
                    event_type=st.best_type,
                    confidence=st.best_conf,
                    extinguisher_present=extinguisher_present,
                    observer_present=observer_present,
                    detections=[d for (d, _) in in_zone] + [d for (d, _) in persons] +
                               [d for (d, _) in exts],
                ))

            status[zone.id] = {
                "fire_active": fire_now,
                "event_type": st.best_type if fire_now else None,
                "streak": st.streak,
                "persistent": persistent,
                "confidence": round(st.best_conf, 3) if fire_now else 0.0,
                "observer_present": observer_present,
                "extinguisher_present": extinguisher_present,
                "alerted_now": alert,
                "seconds_since_last_alert": (
                    round(now - st.last_alert_ts, 1) if st.last_alert_ts else None
                ),
            }

        return events, status

    # ------------------------------------------------------------------
    def _observer(self, zone: Zone, persons) -> bool:
        return any(zone.contains_norm(*c) for (_, c) in persons)

    def _extinguisher(self, zone: Zone, exts) -> bool:
        return any(zone.near_norm(*c, self.margin) for (_, c) in exts)
