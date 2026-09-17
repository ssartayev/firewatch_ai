"""
Detector wrappers.

Each detector turns a frame into a list of Detection objects with CANONICAL
labels (fire / smoke / person / fire_extinguisher). The canonical label comes
from the `classes` mapping in config.yaml, so models with different class
indices all normalise to the one vocabulary the rule engine understands.

A special weights mode, "mock", provides a scripted detector with no neural
network. It exists so the full logic (zones → rules → debounce → alert → DB
→ dashboard) can be exercised before real weights are available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .config import ModelCfg

log = logging.getLogger("firewatch.detectors")

# Canonical labels understood by the rule engine
FIRE_LABELS = {"fire", "smoke"}
PERSON_LABEL = "person"
EXTINGUISHER_LABEL = "fire_extinguisher"


@dataclass
class Detection:
    """A single detection in a frame (coordinates in frame pixels)."""
    label: str            # canonical label
    confidence: float
    bbox: tuple[int, int, int, int]   # x1, y1, x2, y2

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def norm_center(self, w: int, h: int) -> tuple[float, float]:
        """Box centre in normalised coordinates (0..1)."""
        cx, cy = self.center
        return (cx / max(w, 1), cy / max(h, 1))


# ---------------------------------------------------------------------------
# Real detector backed by Ultralytics YOLO
# ---------------------------------------------------------------------------
class YoloDetector:
    """Wrapper around a single Ultralytics YOLO model."""

    def __init__(self, cfg: ModelCfg, conf_threshold: float):
        self.cfg = cfg
        self.conf = conf_threshold
        self.class_map = cfg.classes            # model class index -> canonical label
        self._model = None
        self._load()

    def _load(self) -> None:
        # Import ultralytics lazily so this module imports without it installed
        from ultralytics import YOLO

        weights = self.cfg.weights
        p = Path(weights)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / weights

        # If the file is missing, try it as an asset NAME: ultralytics downloads
        # known models (yolov8n.pt etc.) itself. For custom paths such as
        # models/fire.pt the name is unknown, so loading fails and we return None.
        target = str(p) if p.exists() else Path(weights).name
        try:
            self._model = YOLO(target)
            log.info("Model '%s' loaded from %s", self.cfg.name, target)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to load model '%s' (%s): %s",
                        self.cfg.name, target, e)
            self._model = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._model is None:
            return []
        results = self._model.predict(frame, conf=self.conf, verbose=False)
        out: list[Detection] = []
        for r in results:
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                cls_idx = int(b.cls[0])
                label = self.class_map.get(cls_idx)
                if label is None:
                    continue  # model class is not mapped — ignore it
                conf = float(b.conf[0])
                x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                out.append(Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2)))
        return out


# ---------------------------------------------------------------------------
# Mock detector (demo without a neural network)
# ---------------------------------------------------------------------------
class MockDetector:
    """
    Scripted detector. Emits detections on a schedule keyed to the frame number,
    so the rules and false-positive filter can be demonstrated deterministically.

    Default behaviour (for fire): the first `warmup` frames are clean (proving no
    false alert fires), then steady "fire" appears in the centre of the frame.
    """

    def __init__(self, cfg: ModelCfg, warmup: int = 6):
        self.cfg = cfg
        self.warmup = warmup
        self._n = 0
        # which canonical label to emulate: first of classes, else 'fire'
        self.emit_label = next(iter(cfg.classes.values()), "fire")

    @property
    def ready(self) -> bool:
        return True

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self._n += 1
        # only fire-group labels are worth scripting
        if self.emit_label not in FIRE_LABELS:
            return []
        if self._n <= self.warmup:
            return []  # quiet start — no alert should fire
        h, w = frame.shape[:2]
        # box covering ~30% of the frame, centred
        bw, bh = int(w * 0.18), int(h * 0.22)
        cx, cy = int(w * 0.5), int(h * 0.52)
        bbox = (cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2)
        return [Detection(label=self.emit_label, confidence=0.87, bbox=bbox)]


# ---------------------------------------------------------------------------
# Detector set (fire + person + extinguisher)
# ---------------------------------------------------------------------------
class DetectorSet:
    """
    Builds and runs every detector from the config. Exposes one flat list of
    Detection objects plus which checks are actually configured (i.e. whether a
    working person / extinguisher model was loaded).
    """

    def __init__(self, models_cfg: dict[str, ModelCfg], conf_threshold: float):
        self.detectors: dict[str, YoloDetector | MockDetector] = {}
        for name, mcfg in models_cfg.items():
            if not mcfg.enabled:
                log.info("Detector '%s' disabled in config", name)
                continue
            if mcfg.is_mock:
                self.detectors[name] = MockDetector(mcfg)
                log.info("Detector '%s' running in MOCK mode", name)
            else:
                self.detectors[name] = YoloDetector(mcfg, conf_threshold)

        # Demo safety net: if the real fire weights failed to load, fall back to the
        # MOCK fire detector so the whole chain (zones → rules → alert → DB →
        # dashboard) stays observable. Point config.yaml at real weights to disable.
        fire_det = self.detectors.get("fire")
        if fire_det is not None and not getattr(fire_det, "ready", False):
            log.warning("Fire weights not loaded — enabling the MOCK fire detector for the demo "
                        "(set models.fire.weights in config.yaml to real weights).")
            self.detectors["fire"] = MockDetector(models_cfg["fire"])

    # canonical labels that at least one working model can actually emit
    def _labels_available(self) -> set[str]:
        labels: set[str] = set()
        for det in self.detectors.values():
            if not getattr(det, "ready", False):
                continue
            if isinstance(det, MockDetector):
                labels.add(det.emit_label)
            else:
                labels.update(det.class_map.values())
        return labels

    @property
    def person_check_enabled(self) -> bool:
        return PERSON_LABEL in self._labels_available()

    @property
    def extinguisher_check_enabled(self) -> bool:
        return EXTINGUISHER_LABEL in self._labels_available()

    @property
    def fire_check_enabled(self) -> bool:
        return bool(FIRE_LABELS & self._labels_available())

    def run(self, frame: np.ndarray) -> list[Detection]:
        """Run every detector over the frame and return the combined detections."""
        out: list[Detection] = []
        for det in self.detectors.values():
            try:
                out.extend(det.detect(frame))
            except Exception as e:  # noqa: BLE001
                log.warning("Detector error: %s", e)
        return out

    def status(self) -> dict[str, bool]:
        """Detector readiness — for the dashboard and logs."""
        return {name: bool(getattr(det, "ready", False)) for name, det in self.detectors.items()}
