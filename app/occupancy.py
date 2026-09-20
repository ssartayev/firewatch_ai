"""
Confidence-aware occupancy estimation.

Two independent sensing layers see the same room and disagree:

  * a camera, which counts people accurately in clear air and becomes useless
    as smoke fills the space,
  * a non-visual presence sensor (mmWave radar or a thermopile array), which
    never gives a crisp count but keeps working when the camera cannot see.

Neither layer is trusted on its own. Each produces an estimate *and* a
confidence, and the two are combined by precision weighting: a source's weight
is its precision, the reciprocal of its variance, so a confident source pulls
the fused estimate towards itself and an unreliable one barely moves it.

    variance_i  =  (1 - c_i) / c_i^2
    precision_i =  1 / variance_i
    fused       =  sum(p_i * x_i) / sum(p_i)

When the combined precision is low the answer is deliberately reported as a
*range* rather than a number. A planner that is told "10 to 13 people" can
reason about the worst case; a planner told "11" cannot.

The non-visual layer is modelled as what it actually is — a detector of moving
and micro-motion signatures whose accuracy depends on mounting and on the
partitions in the room. It is not a through-wall people counter, and the code
never treats it as one.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Confidence a healthy camera reaches in clear air with a settled scene.
CAMERA_BASE_CONFIDENCE = 0.94
# Smoke destroys optical confidence far faster than it destroys radar.
CAMERA_SMOKE_SENSITIVITY = 0.92
PRESENCE_SMOKE_SENSITIVITY = 0.06
# Radar sees presence well and counts poorly, so it is capped below the camera.
PRESENCE_BASE_CONFIDENCE = 0.88
# Below this fused confidence the estimate is published as a range.
RANGE_THRESHOLD = 0.80

MIN_VARIANCE_CONF = 0.02


@dataclass
class SourceReading:
    """One sensing layer's opinion about one space."""

    source: str               # camera | presence | thermal
    estimate: float
    confidence: float
    status: str               # ok | degraded | blind | offline
    detail: str = ""
    bound: str = "point"      # point | lower

    @property
    def is_lower_bound(self) -> bool:
        """
        A source that cannot see everything is not an unbiased estimate.

        This distinction matters more than it looks. A camera in smoke does not
        scatter around the true count — it only ever misses people, never
        invents them. Averaging that reading in would drag the fused estimate
        below the truth exactly when the truth is most worth knowing. So an
        obscured camera is treated as a floor: it can raise the estimate, never
        lower it.
        """
        return self.bound == "lower"

    @property
    def precision(self) -> float:
        c = max(min(self.confidence, 0.995), MIN_VARIANCE_CONF)
        return (c * c) / (1.0 - c)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "estimate": round(self.estimate, 1),
            "confidence": round(self.confidence, 3),
            "status": self.status,
            "detail": self.detail,
            "bound": self.bound,
        }


@dataclass
class FusedOccupancy:
    """The building model's belief about one space."""

    node: str
    estimate: float
    confidence: float
    low: int
    high: int
    sources: list[SourceReading]
    primary: str              # which layer is currently carrying the estimate

    @property
    def is_range(self) -> bool:
        return self.high > self.low

    @property
    def display(self) -> str:
        if self.is_range:
            return f"{self.low}–{self.high}"
        return str(self.low)

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "estimate": round(self.estimate, 1),
            "confidence": round(self.confidence, 3),
            "low": self.low, "high": self.high,
            "display": self.display,
            "is_range": self.is_range,
            "primary": self.primary,
            "sources": [s.as_dict() for s in self.sources],
        }


def camera_reading(truth: int, smoke: float, online: bool = True,
                   rng: random.Random | None = None) -> SourceReading:
    """
    Simulate what an edge-AI camera reports for a space.

    Smoke does two things at once, and both are modelled: the detector's
    confidence collapses, and it also stops seeing people at all, so the count
    itself falls below the truth. A system that only degraded the confidence
    while keeping a correct count would be flattering itself.
    """
    rng = rng or random
    if not online:
        return SourceReading("camera", 0.0, 0.0, "offline", "no signal from device")

    confidence = CAMERA_BASE_CONFIDENCE * (1.0 - CAMERA_SMOKE_SENSITIVITY * smoke)
    confidence = max(confidence, 0.02)

    visible_fraction = max(0.0, 1.0 - 1.15 * smoke)
    seen = truth * visible_fraction
    if seen > 0:
        seen += rng.uniform(-0.35, 0.35)
    seen = max(0.0, seen)

    if smoke >= 0.75:
        status, detail = "blind", "obscured by smoke, count is a floor only"
    elif smoke >= 0.30:
        status, detail = "degraded", "partial obscuration, people may be missed"
    else:
        status, detail = "ok", "clear view"

    bound = "lower" if status in ("degraded", "blind") else "point"
    return SourceReading("camera", seen, confidence, status, detail, bound)


def presence_reading(truth: int, smoke: float, online: bool = True,
                     clutter: float = 0.0,
                     rng: random.Random | None = None) -> SourceReading:
    """
    Simulate a non-visual presence sensor (mmWave or thermopile).

    Smoke is close to transparent at 60 GHz, so the layer survives the event
    that blinds the camera. What it does *not* do is count precisely: people
    standing close together merge into one signature, and furniture and
    partitions scatter the return. `clutter` carries that room-dependent
    penalty, which in a real deployment would come from commissioning.
    """
    rng = rng or random
    if not online:
        return SourceReading("presence", 0.0, 0.0, "offline", "no signal from device")

    confidence = PRESENCE_BASE_CONFIDENCE * (1.0 - PRESENCE_SMOKE_SENSITIVITY * smoke)
    confidence *= (1.0 - 0.45 * clutter)
    confidence = max(confidence, 0.05)

    # Signature merging: the error grows with the number of people present.
    merge_loss = 0.10 * math.sqrt(max(truth, 0))
    signatures = max(0.0, truth - merge_loss + rng.uniform(-0.4, 0.4))

    status = "ok" if confidence >= 0.6 else "degraded"
    detail = "motion and micro-motion signatures" if status == "ok" else \
             "high clutter, signature separation reduced"
    return SourceReading("presence", signatures, confidence, status, detail)


def fuse(node: str, readings: list[SourceReading]) -> FusedOccupancy:
    """Precision-weighted fusion of every layer reporting on one space."""
    usable = [r for r in readings if r.status != "offline" and r.confidence > 0.01]
    if not usable:
        return FusedOccupancy(node, 0.0, 0.0, 0, 0, readings, "none")

    # Only unbiased sources set the estimate. Lower-bound sources can raise it.
    unbiased = [r for r in usable if not r.is_lower_bound]
    pool = unbiased or usable
    total_precision = sum(r.precision for r in pool)
    estimate = sum(r.precision * r.estimate for r in pool) / total_precision
    floor = max((r.estimate for r in usable if r.is_lower_bound), default=0.0)
    estimate = max(estimate, floor)

    # Independent sources: the chance that all of them are wrong is the product
    # of the chances that each one is (noisy-OR).
    miss = 1.0
    for r in usable:
        miss *= (1.0 - min(r.confidence, 0.99))
    confidence = 1.0 - miss

    # The band is the fused standard error. Low precision -> wide band.
    band = 1.2 / math.sqrt(total_precision)
    if confidence < RANGE_THRESHOLD:
        band = max(band, 0.6)

    low = max(0, int(round(max(estimate - band, floor))))
    high = max(low, int(round(estimate + band)))
    if confidence >= RANGE_THRESHOLD and high - low > 1:
        high = low + 1

    primary = max(pool, key=lambda r: r.precision).source
    return FusedOccupancy(node, estimate, confidence, low, high, readings, primary)


def planning_count(f: FusedOccupancy) -> int:
    """
    The number the evacuation planner should actually route for.

    When the estimate is uncertain the planner takes the top of the range.
    Under-provisioning a stair because the model hoped a room was empty is the
    failure mode worth designing against.
    """
    return f.high
