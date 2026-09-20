"""
The visual detection layer.

SENSE AI treats vision as one replaceable input among several, not as the
system. This module wraps whatever detector is configured and returns the same
shape regardless: boxes, a count, and a per-frame confidence that the fusion
layer can weigh against the non-visual sensors.

Two models are wired up:

  * a COCO YOLO detector for the `person` class, used for occupancy,
  * a fire/smoke detector for hazard confirmation.

Both are loaded lazily and both are optional. If Ultralytics is not installed,
or the weights are absent, the module reports `simulated` and returns scripted
results so the rest of the product still runs. That state is surfaced to the
UI rather than hidden, because a demo that silently fakes its inference is
worse than one that says it is doing so.

Any accuracy figure shown anywhere in this project is a self-reported model
card number from the weights' publisher. It is not a certification, it has not
been validated on this building, and it must not be read as evidence of
real-world fire-safety performance.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("sense.vision")

BASE_DIR = Path(__file__).resolve().parent.parent
PERSON_WEIGHTS = BASE_DIR / "models" / "yolov8n.pt"
FIRE_WEIGHTS = BASE_DIR / "models" / "fire.pt"

# Candidate weights for the hazard model. Figures are the publishers' own
# model-card numbers, reproduced for comparison only.
MODEL_CANDIDATES = [
    {
        "id": "Beehzod/yolov26-fire-smoke-detection",
        "role": "fire / smoke",
        "status": "experimental MVP candidate",
        "self_reported": {"mAP50": "0.81", "precision": "0.83", "recall": "0.76"},
        "note": "Self-reported model-card validation results. Not independently "
                "verified, not validated on this building, not a certification.",
    },
    {
        "id": "mfranzon/fire-smoke-yolov8",
        "role": "fire / smoke",
        "status": "currently bundled",
        "self_reported": {"mAP50": "not published", "precision": "not published",
                          "recall": "not published"},
        "note": "Selected during the FireWatch phase after live-flame checks. "
                "No published validation figures.",
    },
    {
        "id": "Ultralytics/yolov8n (COCO)",
        "role": "person",
        "status": "currently bundled",
        "self_reported": {"mAP50-95": "0.373", "precision": "n/a", "recall": "n/a"},
        "note": "Official Ultralytics COCO figure. Person class only is used.",
    },
]


@dataclass
class FrameResult:
    index: int
    timestamp: float
    boxes: list[dict] = field(default_factory=list)
    people: int = 0
    mean_confidence: float = 0.0
    fire: bool = False
    smoke: bool = False
    obscuration: float = 0.0

    def as_dict(self) -> dict:
        return {
            "index": self.index, "timestamp": round(self.timestamp, 2),
            "boxes": self.boxes, "people": self.people,
            "mean_confidence": round(self.mean_confidence, 3),
            "fire": self.fire, "smoke": self.smoke,
            "obscuration": round(self.obscuration, 3),
        }


class VisionEngine:
    def __init__(self) -> None:
        self._person = None
        self._fire = None
        self._tried = False
        self.backend = "unavailable"
        self.detail = "not initialised"

    def _load(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            from ultralytics import YOLO
        except Exception as e:  # noqa: BLE001
            self.backend = "simulated"
            self.detail = f"Ultralytics not importable ({type(e).__name__})"
            log.warning("Vision backend simulated: %s", e)
            return

        loaded = []
        if PERSON_WEIGHTS.exists():
            try:
                self._person = YOLO(str(PERSON_WEIGHTS))
                loaded.append("person")
            except Exception as e:  # noqa: BLE001
                log.warning("person weights failed: %s", e)
        if FIRE_WEIGHTS.exists():
            try:
                self._fire = YOLO(str(FIRE_WEIGHTS))
                loaded.append("fire/smoke")
            except Exception as e:  # noqa: BLE001
                log.warning("fire weights failed: %s", e)

        if loaded:
            self.backend = "yolo"
            self.detail = "Ultralytics YOLO — " + ", ".join(loaded)
        else:
            self.backend = "simulated"
            self.detail = "No usable weights in models/ — scripted results"

    def status(self) -> dict:
        self._load()
        return {
            "backend": self.backend,
            "detail": self.detail,
            "person_weights": PERSON_WEIGHTS.name if PERSON_WEIGHTS.exists() else None,
            "fire_weights": FIRE_WEIGHTS.name if FIRE_WEIGHTS.exists() else None,
            "candidates": MODEL_CANDIDATES,
            "disclaimer": (
                "Experimental detection model. Published metrics are the model "
                "author's self-reported validation results, not certification "
                "and not evidence of real-world fire-safety performance."
            ),
        }

    # -- real inference -----------------------------------------------------

    def analyse_video(self, path: Path, max_frames: int = 40,
                      conf: float = 0.30) -> dict:
        """Sample frames from a clip and report per-frame detections."""
        self._load()
        if self.backend != "yolo":
            return self.simulate(reason=self.detail)

        try:
            import cv2
        except Exception as e:  # noqa: BLE001
            return self.simulate(reason=f"OpenCV unavailable ({type(e).__name__})")

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return self.simulate(reason="clip could not be opened")

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or max_frames
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        stride = max(1, total // max_frames)
        duration = total / max(fps, 1.0)

        frames: list[FrameResult] = []
        idx = 0
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                frames.append(self._analyse_frame(frame, idx, idx / max(fps, 1), conf))
            idx += 1
        cap.release()

        if not frames:
            return self.simulate(reason="no frames decoded")
        return self._summarise(frames, backend="yolo", source=path.name,
                               duration=duration)

    @staticmethod
    def obscuration(frame) -> float:
        """
        How much of the scene smoke has taken away, 0..1.

        Contrast alone is not enough: a flat cartoon and a smoke-filled room
        both have low edge energy, and calling the cartoon "blind" is exactly
        the false alarm that made the first version of this panel useless.
        Smoke has one signature a low-detail image does not — it desaturates
        everything towards grey. So saturation loss gates the score and edge
        loss only sharpens it.
        """
        import cv2
        import numpy as np

        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edge = float(cv2.Laplacian(grey, cv2.CV_64F).var())
        sat = float(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1].mean())

        sat_loss = min(max(1.0 - sat / 40.0, 0.0), 1.0)
        edge_loss = min(max(1.0 - edge / 250.0, 0.0), 1.0)
        return round(sat_loss * (0.55 + 0.45 * edge_loss), 3)

    def _analyse_frame(self, frame, index: int, ts: float, conf: float) -> FrameResult:
        h, w = frame.shape[:2]
        res = FrameResult(index=index, timestamp=ts)
        confs: list[float] = []

        if self._person is not None:
            for r in self._person.predict(frame, conf=conf, verbose=False):
                for box in r.boxes:
                    cls = int(box.cls[0])
                    if cls != 0:            # COCO class 0 is person
                        continue
                    c = float(box.conf[0])
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    res.boxes.append({
                        "label": "person", "conf": round(c, 3),
                        "x": round(x1 / w, 4), "y": round(y1 / h, 4),
                        "w": round((x2 - x1) / w, 4), "h": round((y2 - y1) / h, 4),
                    })
                    confs.append(c)
            res.people = len(res.boxes)

        if self._fire is not None:
            for r in self._fire.predict(frame, conf=conf, verbose=False):
                names = r.names or {}
                for box in r.boxes:
                    label = str(names.get(int(box.cls[0]), "fire")).lower()
                    c = float(box.conf[0])
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    res.boxes.append({
                        "label": "smoke" if "smoke" in label else "fire",
                        "conf": round(c, 3),
                        "x": round(x1 / w, 4), "y": round(y1 / h, 4),
                        "w": round((x2 - x1) / w, 4), "h": round((y2 - y1) / h, 4),
                    })
                    if "smoke" in label:
                        res.smoke = True
                    else:
                        res.fire = True

        res.mean_confidence = sum(confs) / len(confs) if confs else 0.0
        res.obscuration = self.obscuration(frame)
        return res

    # -- scripted fallback --------------------------------------------------

    def simulate(self, reason: str = "", frames_n: int = 18,
                 base_people: int = 11, smoke_ramp: bool = True) -> dict:
        """
        Scripted stand-in, used when no real detector is available.

        It deliberately reproduces the failure this product is designed around:
        as the frame fills with smoke the detector's confidence collapses and
        it stops finding people who are still in the room.
        """
        rng = random.Random(4)
        frames: list[FrameResult] = []
        for i in range(frames_n):
            progress = i / max(frames_n - 1, 1)
            obsc = progress * 0.9 if smoke_ramp else 0.0
            visible = max(0, int(round(base_people * max(0.0, 1 - 1.15 * obsc))))
            conf_scale = max(0.06, 1.0 - 0.95 * obsc)
            fr = FrameResult(index=i, timestamp=i * 0.5, obscuration=obsc)
            confs = []
            for k in range(visible):
                c = min(0.97, (0.72 + 0.2 * rng.random()) * conf_scale + 0.05)
                confs.append(c)
                fr.boxes.append({
                    "label": "person", "conf": round(c, 3),
                    "x": round(0.06 + 0.085 * k + 0.02 * math.sin(i * 0.6 + k), 4),
                    "y": round(0.40 + 0.06 * math.cos(i * 0.4 + k), 4),
                    "w": 0.062, "h": 0.30,
                })
            if obsc > 0.28:
                fr.smoke = True
                fr.boxes.append({"label": "smoke", "conf": round(0.5 + 0.4 * obsc, 3),
                                 "x": 0.42, "y": 0.05, "w": 0.5, "h": 0.55})
            if obsc > 0.55:
                fr.fire = True
                fr.boxes.append({"label": "fire", "conf": round(0.55 + 0.3 * obsc, 3),
                                 "x": 0.60, "y": 0.34, "w": 0.16, "h": 0.28})
            fr.people = visible
            fr.mean_confidence = sum(confs) / len(confs) if confs else 0.0
            frames.append(fr)
        return self._summarise(frames, backend="simulated",
                               source="scripted sequence", reason=reason)

    def _summarise(self, frames: list[FrameResult], backend: str,
                   source: str, reason: str = "", duration: float = 0.0) -> dict:
        peak = max(f.people for f in frames)
        last = frames[-1]
        total_boxes = sum(len(f.boxes) for f in frames)
        worst_obsc = max(f.obscuration for f in frames)
        any_hazard = any(f.fire or f.smoke for f in frames)

        # Judge usability only on the frames that actually had someone in
        # them. Averaging in the empty frames made a clip with no people look
        # like a clip the camera had failed on, which are opposite findings.
        with_people = [f for f in frames if f.people > 0]
        people_conf = (sum(f.mean_confidence for f in with_people) / len(with_people)
                       if with_people else 0.0)

        if total_boxes == 0:
            verdict = ("Nothing detected in this clip. No people and no fire or "
                       "smoke were found above the confidence threshold, so this "
                       "camera contributes no occupancy evidence and the "
                       "non-visual layer would carry the space.")
        elif not with_people and any_hazard:
            verdict = ("Hazard classes detected, no occupants visible. Useful for "
                       "detection, but it provides no occupancy evidence.")
        elif worst_obsc > 0.6:
            verdict = ("Heavy obscuration. The camera count is a floor, not a "
                       "measurement — the non-visual layer must carry this space.")
        elif people_conf < 0.45:
            verdict = ("People found, but at low confidence. The count is fused "
                       "with the presence layer at reduced weight.")
        else:
            verdict = "Camera count usable; fused with the presence layer."

        return {
            "duration": round(duration, 1),
            "detections": total_boxes,
            "empty": total_boxes == 0,
            "peak_obscuration": round(worst_obsc, 3),
            "hazard_detected": any_hazard,
            "people_confidence": round(people_conf, 3),
            "frames_with_people": len(with_people),
            "backend": backend,
            "reason": reason,
            "source": source,
            "frames": [f.as_dict() for f in frames],
            "peak_people": peak,
            "final_people": last.people,
            "final_confidence": round(last.mean_confidence, 3),
            "fire_detected": any(f.fire for f in frames),
            "smoke_detected": any(f.smoke for f in frames),
            "final_obscuration": round(last.obscuration, 3),
            "verdict": verdict,
            "disclaimer": (
                "Experimental detection model, simulated deployment. Not "
                "certified and not a guarantee of detection."
            ),
        }


ENGINE = VisionEngine()
