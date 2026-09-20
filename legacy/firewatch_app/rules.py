"""
Fire-safety rules, state tracking and debouncing.

Key ideas (false-positive suppression + meaningful alerts):
  1. Fire/smoke counts only if the detection centre is INSIDE a zone.
  2. An event fires only after fire/smoke persists for >= persistence_frames
     CONSECUTIVE frames — a single bad frame (spark, glare) raises no alert.
  3. Repeat alerts for the same zone are throttled to alert_cooldown_sec.
  4. For a valid event, the visible permit conditions are checked:
       - is a fire extinguisher in or near the zone?
       - is a human observer inside the zone?
     If the matching model is not configured, the condition is None
     ("not checked") rather than silently passing.
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
    """A fire-hazard event ready to be alerted on."""
    zone_id: str
    event_type: str                       # 'fire' | 'smoke'
    confidence: float
    extinguisher_present: Optional[bool]  # None = check not configured
    observer_present: Optional[bool]
    detections: list[Detection] = field(default_factory=list)  # for drawing the snapshot


@dataclass
class _ZoneState:
    streak: int = 0                       # consecutive frames with fire
    best_conf: float = 0.0
    best_type: str = "fire"
    last_alert_ts: float = 0.0


class RuleEngine:
    """Tracks per-zone state and decides when to send an alert."""

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
        Process a single frame.
        Returns (events to alert on, per-zone status for the dashboard).
        """
        now = time.time() if now is None else now
        w, h = frame_wh
        events: list[FireEvent] = []
        status: dict[str, dict] = {}

        # bucket detections by category up front (normalised centres)
        fires = [(d, d.norm_center(w, h)) for d in detections if d.label in FIRE_LABELS]
        persons = [(d, d.norm_center(w, h)) for d in detections if d.label == PERSON_LABEL]
        exts = [(d, d.norm_center(w, h)) for d in detections if d.label == EXTINGUISHER_LABEL]

        for zone in zones:
            st = self._st(zone.id)

            # --- fire/smoke inside the zone ---
            in_zone = [(d, c) for (d, c) in fires if zone.contains_norm(*c)]
            if in_zone:
                # take the most confident detection; fire outranks smoke
                best = max(in_zone, key=lambda dc: (dc[0].label == "fire", dc[0].confidence))[0]
                st.streak += 1
                st.best_conf = best.confidence
                st.best_type = best.label
            else:
                st.streak = 0
                st.best_conf = 0.0

            persistent = st.streak >= self.persistence_frames

            # --- permit conditions ---
            observer_present = self._observer(zone, persons) if self.person_check else None
            extinguisher_present = self._extinguisher(zone, exts) if self.ext_check else None

            # --- alert decision (valid + debounced) ---
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
