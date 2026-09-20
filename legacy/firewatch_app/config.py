"""
FireWatch AI configuration loading.

Sources:
  - .env         — secrets (Telegram token, chat_id), via python-dotenv.
  - config.yaml  — everything else (video source, models, zones, rules).

Secrets are NEVER hardcoded and never written to config.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Project root = the firewatch/ folder (one level above app/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env once at import time
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ModelCfg:
    """Settings for a single detector."""
    name: str
    weights: str                       # path to .pt weights, or the special value "mock"
    enabled: bool = True
    classes: dict[int, str] = field(default_factory=dict)  # class index -> canonical name

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
    polygon: list[list[float]]         # normalised points [[x,y], ...], x,y in 0..1


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
    raw: dict[str, Any]                # raw yaml dict (used to write zones back)
    config_path: Path

    # --- secrets from the environment ---
    @property
    def telegram_token(self) -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    @property
    def telegram_chat_id(self) -> str:
        return os.getenv("TELEGRAM_CHAT_ID", "").strip()

    @property
    def telegram_ready(self) -> bool:
        """Is Telegram actually usable?"""
        return self.alerts.telegram_enabled and bool(self.telegram_token) and bool(self.telegram_chat_id)

    # --- convenience absolute paths ---
    def path(self, rel: str) -> Path:
        """Absolute path resolved against the project root."""
        p = Path(rel)
        return p if p.is_absolute() else BASE_DIR / p


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _config_path() -> Path:
    """Path to config.yaml (override with FIREWATCH_CONFIG)."""
    override = os.getenv("FIREWATCH_CONFIG")
    if override:
        p = Path(override)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / "config.yaml"


def load_config() -> Config:
    """Read config.yaml and build a typed Config object."""
    cfg_path = _config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # models
    models: dict[str, ModelCfg] = {}
    for name, m in (raw.get("models") or {}).items():
        # class keys in yaml should be ints; coerce defensively
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

    # --- quick environment overrides (without editing config.yaml) ---
    # FIREWATCH_VIDEO_SOURCE=0            -> webcam
    # FIREWATCH_VIDEO_SOURCE=rtsp://...   -> RTSP
    # FIREWATCH_VIDEO_SOURCE=data/demo_fire.mp4 -> demo file
    video_source = os.getenv("FIREWATCH_VIDEO_SOURCE") or str(raw.get("video_source", ""))
    # FIREWATCH_FIRE_WEIGHTS=mock         -> force the MOCK fire detector (demo)
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
    Write zones back to config.yaml (used by the zone editor UI).
    zones — list of dicts like {"id": "A", "polygon": [[x,y], ...]} (normalised).
    The rest of the config is preserved as-is.
    """
    raw = dict(cfg.raw)
    raw["zones"] = [{"id": z["id"], "polygon": [[float(x), float(y)] for x, y in z["polygon"]]}
                    for z in zones]
    with open(cfg.config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    # update the in-memory object
    cfg.raw = raw
    cfg.zones = [ZoneCfg(id=str(z["id"]),
                         polygon=[[float(x), float(y)] for x, y in z["polygon"]])
                 for z in raw["zones"]]
