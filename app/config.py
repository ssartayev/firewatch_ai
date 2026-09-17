"""
Загрузка конфигурации FireWatch AI.

Источники:
  - .env         — секреты (токен Telegram, chat_id). Через python-dotenv.
  - config.yaml  — всё остальное (источник видео, модели, зоны, правила).

Секреты НИКОГДА не хардкодятся и не пишутся в config.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Корень проекта = папка firewatch/ (на уровень выше app/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Подхватываем .env один раз при импорте
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Датаклассы конфигурации
# ---------------------------------------------------------------------------
@dataclass
class ModelCfg:
    """Настройки одного детектора."""
    name: str
    weights: str                       # путь к .pt или спец-значение "mock"
    enabled: bool = True
    classes: dict[int, str] = field(default_factory=dict)  # индекс -> каноничное имя

    @property
    def is_mock(self) -> bool:
        return str(self.weights).strip().lower() == "mock"


@dataclass
class DetectionCfg:
    conf_threshold: float = 0.40
    persistence_frames: int = 4
    alert_cooldown_sec: int = 60
    near_zone_margin: float = 0.05


@dataclass
class ZoneCfg:
    id: str
    polygon: list[list[float]]         # нормализованные точки [[x,y], ...], x,y в 0..1


@dataclass
class AlertsCfg:
    telegram_enabled: bool = True
    webhook_url: str = ""


@dataclass
class Config:
    video_source: str
    target_fps: int
    loop_video: bool
    models: dict[str, ModelCfg]
    detection: DetectionCfg
    zones: list[ZoneCfg]
    alerts: AlertsCfg
    raw: dict[str, Any]                # исходный словарь yaml (для сохранения зон обратно)
    config_path: Path

    # --- секреты из окружения ---
    @property
    def telegram_token(self) -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    @property
    def telegram_chat_id(self) -> str:
        return os.getenv("TELEGRAM_CHAT_ID", "").strip()

    @property
    def telegram_ready(self) -> bool:
        """Telegram действительно можно использовать?"""
        return self.alerts.telegram_enabled and bool(self.telegram_token) and bool(self.telegram_chat_id)

    # --- удобные абсолютные пути ---
    def path(self, rel: str) -> Path:
        """Абсолютный путь относительно корня проекта."""
        p = Path(rel)
        return p if p.is_absolute() else BASE_DIR / p


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------
def _config_path() -> Path:
    """Путь к config.yaml (можно переопределить через FIREWATCH_CONFIG)."""
    override = os.getenv("FIREWATCH_CONFIG")
    if override:
        p = Path(override)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / "config.yaml"


def load_config() -> Config:
    """Прочитать config.yaml и собрать типизированный объект Config."""
    cfg_path = _config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # модели
    models: dict[str, ModelCfg] = {}
    for name, m in (raw.get("models") or {}).items():
        # ключи classes в yaml — int, приводим на всякий случай
        classes = {int(k): str(v) for k, v in (m.get("classes") or {}).items()}
        models[name] = ModelCfg(
            name=name,
            weights=str(m.get("weights", "")),
            enabled=bool(m.get("enabled", True)),
            classes=classes,
        )

    d = raw.get("detection") or {}
    detection = DetectionCfg(
        conf_threshold=float(d.get("conf_threshold", 0.40)),
        persistence_frames=int(d.get("persistence_frames", 4)),
        alert_cooldown_sec=int(d.get("alert_cooldown_sec", 60)),
        near_zone_margin=float(d.get("near_zone_margin", 0.05)),
    )

    zones = [
        ZoneCfg(id=str(z["id"]), polygon=[[float(x), float(y)] for x, y in z["polygon"]])
        for z in (raw.get("zones") or [])
    ]

    a = raw.get("alerts") or {}
    alerts = AlertsCfg(
        telegram_enabled=bool((a.get("telegram") or {}).get("enabled", True)),
        webhook_url=str(a.get("webhook_url", "") or ""),
    )

    # --- быстрые переопределения через окружение (не трогая config.yaml) ---
    # FIREWATCH_VIDEO_SOURCE=0            -> веб-камера
    # FIREWATCH_VIDEO_SOURCE=rtsp://...   -> RTSP
    # FIREWATCH_VIDEO_SOURCE=data/demo_fire.mp4 -> демо-файл
    video_source = os.getenv("FIREWATCH_VIDEO_SOURCE") or str(raw.get("video_source", ""))
    # FIREWATCH_FIRE_WEIGHTS=mock         -> форсировать MOCK-детектор огня (для демо)
    fire_override = os.getenv("FIREWATCH_FIRE_WEIGHTS")
    if fire_override and "fire" in models:
        models["fire"].weights = fire_override

    return Config(
        video_source=video_source,
        target_fps=int(raw.get("target_fps", 3)),
        loop_video=bool(raw.get("loop_video", True)),
        models=models,
        detection=detection,
        zones=zones,
        alerts=alerts,
        raw=raw,
        config_path=cfg_path,
    )


def save_zones(cfg: Config, zones: list[dict[str, Any]]) -> None:
    """
    Сохранить зоны обратно в config.yaml (используется UI-редактором зон).
    zones — список словарей вида {"id": "A", "polygon": [[x,y], ...]} (нормализованные).
    Остальная часть конфига сохраняется как есть.
    """
    raw = dict(cfg.raw)
    raw["zones"] = [{"id": z["id"], "polygon": [[float(x), float(y)] for x, y in z["polygon"]]}
                    for z in zones]
    with open(cfg.config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    # обновим объект в памяти
    cfg.raw = raw
    cfg.zones = [ZoneCfg(id=str(z["id"]),
                         polygon=[[float(x), float(y)] for x, y in z["polygon"]])
                 for z in raw["zones"]]
