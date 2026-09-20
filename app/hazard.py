"""
Hazard state and smoke propagation across the building graph.

This is a coarse transport model, not CFD. Smoke is a scalar 0..1 per space
that is produced by the fire node and moves along edges towards lower
concentration, faster through wide openings than through doorways. That is
enough to make the two things the product depends on behave correctly:

  * a corridor fills with smoke some time *after* the room does, so there is a
    window in which routing decisions still matter,
  * the cameras in a filling space lose confidence before the space becomes
    impassable, which is exactly the moment the non-visual layer has to carry
    the occupancy estimate.

Nothing here should be read as a fire-engineering prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .building import Building

# Smoke level at which a space is treated as untenable for evacuees.
IMPASSABLE_SMOKE = 0.82
# Smoke level at which routing starts actively penalising a space.
PENALTY_SMOKE = 0.22

FIRE_GROWTH_PER_SEC = 0.035
SMOKE_SOURCE_PER_SEC = 0.11
DIFFUSION_PER_SEC = 0.055
DOOR_RESTRICTION = 0.45


@dataclass
class HazardState:
    fire: dict[str, float] = field(default_factory=dict)
    smoke: dict[str, float] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)      # manually blocked nodes
    detected_at: dict[str, float] = field(default_factory=dict)
    alarms: list[dict] = field(default_factory=list)

    def fire_at(self, node: str) -> float:
        return self.fire.get(node, 0.0)

    def smoke_at(self, node: str) -> float:
        return self.smoke.get(node, 0.0)

    def is_passable(self, node: str) -> bool:
        if node in self.blocked:
            return False
        return self.smoke_at(node) < IMPASSABLE_SMOKE and self.fire_at(node) < 0.25

    def as_dict(self) -> dict:
        return {
            "fire": {k: round(v, 3) for k, v in self.fire.items() if v > 0.001},
            "smoke": {k: round(v, 3) for k, v in self.smoke.items() if v > 0.005},
            "blocked": sorted(self.blocked),
            "impassable": sorted(n for n in set(self.smoke) | self.blocked
                                 if not self.is_passable(n)),
            "alarms": self.alarms[-12:],
        }


class HazardModel:
    def __init__(self, building: Building):
        self.b = building
        self.state = HazardState()

    def reset(self) -> None:
        self.state = HazardState()

    def ignite(self, node: str, t: float, intensity: float = 0.12) -> None:
        self.state.fire[node] = max(self.state.fire.get(node, 0.0), intensity)
        self.state.smoke.setdefault(node, 0.05)

    def block(self, node: str) -> None:
        self.state.blocked.add(node)

    def unblock(self, node: str) -> None:
        self.state.blocked.discard(node)

    def inject_smoke(self, node: str, amount: float) -> None:
        self.state.smoke[node] = min(1.0, self.state.smoke.get(node, 0.0) + amount)

    def step(self, dt: float, t: float) -> list[dict]:
        """Advance fire growth and smoke transport by dt seconds."""
        s = self.state
        events: list[dict] = []

        for node, level in list(s.fire.items()):
            s.fire[node] = min(1.0, level + FIRE_GROWTH_PER_SEC * dt)
            s.smoke[node] = min(1.0, s.smoke.get(node, 0.0)
                                + SMOKE_SOURCE_PER_SEC * dt * s.fire[node])

        # Transport: smoke moves down its own gradient along every passage.
        delta: dict[str, float] = {}
        for edge in self.b.edges:
            a, b = edge.a, edge.b
            ca, cb = s.smoke.get(a, 0.0), s.smoke.get(b, 0.0)
            if abs(ca - cb) < 1e-4:
                continue
            restriction = DOOR_RESTRICTION if edge.kind == "door" else 1.0
            # Stairs vent upward, so downward transport into them is reduced.
            if edge.kind == "stair":
                restriction = 0.25
            flux = DIFFUSION_PER_SEC * dt * restriction * (ca - cb)
            delta[a] = delta.get(a, 0.0) - flux
            delta[b] = delta.get(b, 0.0) + flux

        for node, d in delta.items():
            s.smoke[node] = max(0.0, min(1.0, s.smoke.get(node, 0.0) + d))

        # Detection: a spot detector latches once its space passes a threshold.
        for node, level in s.smoke.items():
            if level > 0.18 and node not in s.detected_at:
                s.detected_at[node] = t
                kind = "fire" if s.fire.get(node, 0) > 0.05 else "smoke"
                alarm = {"t": round(t, 1), "node": node, "kind": kind,
                         "device": f"{'HEAT' if kind == 'fire' else 'SMK'}-"
                                   f"{node[1:] if node.startswith('R') else node}"}
                s.alarms.append(alarm)
                events.append(alarm)

        return events
