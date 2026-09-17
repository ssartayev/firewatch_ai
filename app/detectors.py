"""
Обёртки над детекторами.

Каждый детектор превращает кадр в список объектов Detection с КАНОНИЧНЫМИ
метками (fire / smoke / person / fire_extinguisher). Каноничная метка задаётся
маппингом classes из config.yaml, поэтому разные модели с разными индексами
классов приводятся к единому словарю, который понимает логика правил.

Поддерживается спец-режим weights: "mock" — детектор без нейросети, выдающий
сценарные детекции. Он нужен, чтобы прогнать всю логику (зоны → правила →
дебаунс → алерт → БД → дашборд) даже когда реальных весов ещё нет.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .config import ModelCfg

log = logging.getLogger("firewatch.detectors")

# Каноничные метки, которые понимает логика
FIRE_LABELS = {"fire", "smoke"}
PERSON_LABEL = "person"
EXTINGUISHER_LABEL = "fire_extinguisher"


@dataclass
class Detection:
    """Одна детекция на кадре (координаты — в пикселях кадра)."""
    label: str            # каноничная метка
    confidence: float
    bbox: tuple[int, int, int, int]   # x1, y1, x2, y2

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def norm_center(self, w: int, h: int) -> tuple[float, float]:
        """Центр бокса в нормализованных координатах (0..1)."""
        cx, cy = self.center
        return (cx / max(w, 1), cy / max(h, 1))


# ---------------------------------------------------------------------------
# Реальный детектор на ultralytics YOLO
# ---------------------------------------------------------------------------
class YoloDetector:
    """Обёртка над одной моделью Ultralytics YOLO."""

    def __init__(self, cfg: ModelCfg, conf_threshold: float):
        self.cfg = cfg
        self.conf = conf_threshold
        self.class_map = cfg.classes            # индекс модели -> каноничная метка
        self._model = None
        self._load()

    def _load(self) -> None:
        # Ленивая загрузка ultralytics, чтобы модуль импортировался и без неё
        from ultralytics import YOLO

        weights = self.cfg.weights
        p = Path(weights)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / weights

        # Если файла нет — пробуем передать как ИМЯ ассета: ultralytics сам
        # скачает известные модели (yolov8n.pt и т.п.). Для кастомных путей
        # (models/fire.pt) имя неизвестно — загрузка не удастся, вернём None.
        target = str(p) if p.exists() else Path(weights).name
        try:
            self._model = YOLO(target)
            log.info("Модель '%s' загружена из %s", self.cfg.name, target)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось загрузить модель '%s' (%s): %s",
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
                    continue  # класс модели не замаплен — игнорируем
                conf = float(b.conf[0])
                x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                out.append(Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2)))
        return out


# ---------------------------------------------------------------------------
# Мок-детектор (демо без нейросети)
# ---------------------------------------------------------------------------
class MockDetector:
    """
    Сценарный детектор. Выдаёт детекции согласно расписанию по номеру кадра,
    чтобы детерминированно продемонстрировать работу правил и анти-FP фильтра.

    Логика по умолчанию (для fire): первые `warmup` кадров — чисто (проверяем,
    что нет ложного алерта), далее в центре кадра стабильно «огонь».
    """

    def __init__(self, cfg: ModelCfg, warmup: int = 6):
        self.cfg = cfg
        self.warmup = warmup
        self._n = 0
        # какую каноничную метку эмулировать: берём первую из classes, иначе 'fire'
        self.emit_label = next(iter(cfg.classes.values()), "fire")

    @property
    def ready(self) -> bool:
        return True

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self._n += 1
        # только метки из «пожарной» группы имеет смысл эмулировать сценарно
        if self.emit_label not in FIRE_LABELS:
            return []
        if self._n <= self.warmup:
            return []  # «спокойный» старт — не должно быть алерта
        h, w = frame.shape[:2]
        # бокс ~30% кадра в центре
        bw, bh = int(w * 0.18), int(h * 0.22)
        cx, cy = int(w * 0.5), int(h * 0.52)
        bbox = (cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2)
        return [Detection(label=self.emit_label, confidence=0.87, bbox=bbox)]


# ---------------------------------------------------------------------------
# Набор детекторов (fire + person + extinguisher)
# ---------------------------------------------------------------------------
class DetectorSet:
    """
    Собирает и запускает все детекторы из конфига. Наружу отдаёт единый плоский
    список Detection и информацию о том, какие проверки вообще «настроены»
    (есть ли рабочая модель person / extinguisher).
    """

    def __init__(self, models_cfg: dict[str, ModelCfg], conf_threshold: float):
        self.detectors: dict[str, YoloDetector | MockDetector] = {}
        for name, mcfg in models_cfg.items():
            if not mcfg.enabled:
                log.info("Детектор '%s' отключён в конфиге", name)
                continue
            if mcfg.is_mock:
                self.detectors[name] = MockDetector(mcfg)
                log.info("Детектор '%s' работает в режиме MOCK", name)
            else:
                self.detectors[name] = YoloDetector(mcfg, conf_threshold)

        # Страховка для демо: если реальные веса fire не загрузились — включаем
        # MOCK-детектор огня, чтобы вся логика (зоны → правила → алерт → БД →
        # дашборд) оставалась наблюдаемой. Замените на реальные веса в config.yaml.
        fire_det = self.detectors.get("fire")
        if fire_det is not None and not getattr(fire_det, "ready", False):
            log.warning("Веса fire не загружены — включаю MOCK-детектор огня для демо "
                        "(замените models.fire.weights в config.yaml на реальные веса).")
            self.detectors["fire"] = MockDetector(models_cfg["fire"])

    # какие каноничные метки в принципе может выдавать хоть одна рабочая модель
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
        """Прогнать все детекторы по кадру и вернуть общий список детекций."""
        out: list[Detection] = []
        for det in self.detectors.values():
            try:
                out.extend(det.detect(frame))
            except Exception as e:  # noqa: BLE001
                log.warning("Ошибка детектора: %s", e)
        return out

    def status(self) -> dict[str, bool]:
        """Готовность детекторов — для дашборда/логов."""
        return {name: bool(getattr(det, "ready", False)) for name, det in self.detectors.items()}
