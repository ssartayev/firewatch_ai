"""
The SENSE AI control loop.

Every tick runs the same cycle:

    SENSE       read the simulated devices (cameras, presence, smoke, heat)
    ESTIMATE    fuse them into a confidence-aware occupancy belief
    PLAN        allocate the whole population across every usable exit
    GUIDE       publish per-zone instructions derived from that allocation
    OBSERVE     watch where people actually go
    COMPARE     measure the divergence between the plan and the crowd
    RECALCULATE replan when the divergence, the hazard or the load says so

The part worth watching is the second half. A system that only reacts to the
fire will keep issuing a plan that nobody is following. This one measures its
own guidance against observed movement and corrects, which is why the stair
allocation changes mid-evacuation in the scripted incident.

The crowd itself is simulated with a compliance model: a fixed share of people
follow the instruction they are given and the rest default to the stair they
know, usually the one they came in by. That is the single most reliable
finding in evacuation research and the reason a plan cannot be assumed to
execute itself.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .building import (ASSISTED_EVACUATION, INITIAL_OCCUPANCY,
                       OFF_FLOOR_POPULATION, TOTAL_POPULATION,
                       Building, load_building)
from .hazard import HazardModel
from .occupancy import (FusedOccupancy, camera_reading, fuse, planning_count,
                        presence_reading)
from .routing import Plan, Router, compare_plan_to_reality

PHASES = ["SENSE", "ESTIMATE", "PLAN", "GUIDE", "OBSERVE", "COMPARE", "RECALCULATE"]

TICK_SECONDS = 0.5          # simulated seconds advanced per tick
PLAN_INTERVAL = 4.0         # a full replan at least this often
MIN_REPLAN_GAP = 2.5        # never replan more often than this
# Window over which observed core choice is measured. An instantaneous rate
# falls to zero the moment the last person steps into a stair, which would
# make the plan look unverifiable exactly when it matters most. A window says
# "of the people who committed to a core recently, this is where they went".
OBSERVE_WINDOW = 30.0
BASE_COMPLIANCE = 0.72      # share of people who follow guidance, unaided
GUIDED_COMPLIANCE = 0.91    # once zoned voice guidance is active

# Rooms whose occupants habitually use a particular core. Familiarity bias is
# what loads one stair while another stands empty.
FAMILIAR_STAIR = {
    "R301": "STAIR-A", "R302": "STAIR-A", "R303": "STAIR-A", "R304": "STAIR-A",
    "R305": "STAIR-A", "R306": "STAIR-B", "R311": "STAIR-A", "R312": "STAIR-A",
    "R313": "STAIR-C", "R314": "STAIR-A", "R315": "STAIR-B", "R316": "STAIR-B",
}

# Pre-movement time: seconds between the alarm sounding and a space actually
# starting to empty. This is the least intuitive number in evacuation
# engineering and routinely the largest term in the total. People finish a
# sentence, save a file, look for a colleague, or wait for someone to confirm
# the alarm is real. Meeting rooms are the slowest because the decision is
# social. Spaces that can already smell smoke move almost at once.
PREMOVE = {
    "R301": 26, "R302": 48, "R303": 22, "R304": 11, "R305": 4, "R306": 14,
    "R311": 30, "R312": 52, "R313": 19, "R314": 24, "R315": 38, "R316": 44,
}
PREMOVE_JITTER = 6.0
# Levels 4 and 5 react, then descend to this floor's landings.
UPPER_FLOOR_LAG = 27.0

# Rooms with partitions or dense furniture, where the mmWave layer separates
# signatures less cleanly. Would come from commissioning in a real install.
CLUTTER = {"R302": 0.35, "R304": 0.45, "R305": 0.55, "R312": 0.30,
           "R313": 0.50, "R314": 0.50}

# Groups waiting for assisted evacuation carry this target and never move on
# their own. They are released only when a crew reaches them.
REFUGE = "REFUGE"


@dataclass
class ScriptStep:
    t: float
    key: str
    title: str
    detail: str
    fired: bool = False


def incident_script() -> list[ScriptStep]:
    return [
        ScriptStep(6, "ignition", "Fire starts in Room 305",
                   "Plant room. No occupant present at ignition."),
        ScriptStep(12, "detection", "Fire and smoke detection activates",
                   "Heat detector HEAT-305 and the vision layer agree. Alarm raised."),
        ScriptStep(22, "spread", "Smoke enters corridor C4",
                   "The north-east corridor segment begins to fill."),
        ScriptStep(30, "blind", "Camera confidence collapses in the smoke",
                   "CAM-305 and CAM-C4 lose usable visibility."),
        ScriptStep(36, "nonvisual", "Non-visual layer carries the estimate",
                   "mmWave presence signatures become the dominant input for C4 and 305."),
        ScriptStep(48, "congestion", "Stair A loads up from the floors above",
                   "Levels 4 and 5 discharge into the west core. Flow approaches capacity."),
        ScriptStep(58, "observe", "Observed movement diverges from the plan",
                   "More people are entering Stair A than the allocation assigned to it."),
        ScriptStep(66, "recalc", "Routes recalculated",
                   "Hazard, measured congestion, travel time and core capacity re-weighted."),
        ScriptStep(74, "redistribute", "Population redistributed across A, B and C",
                   "Load is moved off the west core and onto the central and east cores."),
        ScriptStep(80, "reroute", "Guidance updated on the floor plan",
                   "Assigned paths change for the rooms that were feeding Stair A."),
        ScriptStep(88, "speakers", "Zoned instructions differ by area",
                   "Each corridor zone receives a different message. Compliance rises."),
        ScriptStep(100, "responder", "Firefighter view resolves remaining occupants",
                   "Room 314 refuge point: low camera visibility, presence held."),
        ScriptStep(112, "loop", "Loop continues",
                   "Self-evacuation complete. Five occupants remain in the refuge point."),
        ScriptStep(136, "assisted", "Crew reaches the refuge point in Room 314",
                   "Assisted evacuation begins on the recommended approach line."),
    ]


@dataclass
class Group:
    """People in one space who share a destination core."""
    target: str
    count: float


class Simulation:
    def __init__(self, seed: int = 11):
        self.rng = random.Random(seed)
        self.building: Building = load_building()
        self.hazard = HazardModel(self.building)
        self.router = Router(self.building, self.hazard)
        self.reset()

    # -- lifecycle ----------------------------------------------------------

    def reset(self) -> None:
        self.t = 0.0
        self.tick_no = 0
        self.running = False
        self.mode = "normal"                 # normal | incident | clearing | cleared
        self.phase = "SENSE"
        self.phases_fired: list[str] = []
        self.hazard.reset()
        self.rng = random.Random(11)

        # people[node] -> list of Groups
        self.people: dict[str, list[Group]] = {}
        for node, n in INITIAL_OCCUPANCY.items():
            assisted = float(ASSISTED_EVACUATION.get(node, 0))
            groups = []
            if n - assisted > 0:
                groups.append(Group(FAMILIAR_STAIR.get(node, "STAIR-C"), n - assisted))
            if assisted > 0:
                groups.append(Group(REFUGE, assisted))
            self.people[node] = groups
        self.assisted_released = False
        self.transit: list[dict] = []
        self._pop_cache: dict[str, float] | None = None
        self._hop_cache: dict[tuple[str, str], str] = {}
        self.evacuated = 0.0
        self.off_floor_remaining = float(OFF_FLOOR_POPULATION)

        self.fused: dict[str, FusedOccupancy] = {}
        self.plan: Plan = Plan()
        self.previous_plan: Plan | None = None
        self.last_plan_t = -99.0
        self.comparison: dict = {"rows": [], "max_divergence": 0.0, "replan_required": False}
        self.observed_flow: dict[str, float] = {}
        self.arrival_log: list[tuple[float, str, float]] = []
        self.discharge: dict[str, float] = {}
        self.discharge_rate: dict[str, float] = {}
        self.stair_arrivals: dict[str, float] = {}
        self.compliance = BASE_COMPLIANCE
        self.guidance_active = False
        self.script = incident_script()
        self.timeline: list[dict] = []
        self.events: list[dict] = []
        self.replan_count = 0
        self.replan_reason = "initial plan"
        self.camera_offline: set[str] = set()
        self.premove: dict[str, float] = {}
        self.alarm_t = 1e9
        self.manual_smoke = 0.0
        self.speed = 2.0
        self._log("system", "Monitoring. Building occupancy nominal.", 0.0)

    def start_incident(self) -> None:
        if self.mode == "normal":
            self.running = True
            self.mode = "incident"

    def _log(self, kind: str, text: str, t: float | None = None,
             detail: str = "", severity: str = "info") -> None:
        self.events.append({
            "t": round(self.t if t is None else t, 1),
            "kind": kind, "text": text, "detail": detail, "severity": severity,
        })
        self.events = self.events[-80:]

    # -- population helpers -------------------------------------------------

    def population_in(self, node: str) -> float:
        """Everyone in the space, including those already inside its exit passage."""
        cache = self._pop_cache
        if cache is None:
            cache = {}
            for n, groups in self.people.items():
                cache[n] = sum(g.count for g in groups)
            for p in self.transit:
                cache[p["from"]] = cache.get(p["from"], 0.0) + p["count"]
            self._pop_cache = cache
        return cache.get(node, 0.0)

    def waiting_in(self, node: str) -> float:
        """People standing in the space, not counting those already in transit."""
        return sum(g.count for g in self.people.get(node, []))

    def _invalidate(self) -> None:
        """Population and routing caches are valid for one tick at a time."""
        self._pop_cache = None
        self._hop_cache = {}

    @property
    def remaining(self) -> float:
        inside = sum(self.population_in(n) for n in self.people)
        return inside + self.off_floor_remaining

    @property
    def on_floor_remaining(self) -> float:
        """People still on the modelled floor: rooms and corridors only."""
        return sum(self.population_in(n) for n in self.building.nodes
                   if self.building.node(n).floor == 3
                   and not n.startswith("STAIR"))

    @property
    def in_cores(self) -> float:
        return sum(self.population_in(n) for n in self.building.stairs)

    # -- SENSE + ESTIMATE ---------------------------------------------------

    def sense(self) -> None:
        self.phase = "SENSE"
        self.phases_fired.append("SENSE")
        self.hazard.step(TICK_SECONDS, self.t)

    def estimate(self) -> None:
        """Read every sensing layer and fuse it into one belief per space."""
        self.phase = "ESTIMATE"
        self.phases_fired.append("ESTIMATE")
        self.fused = {}
        for node in list(INITIAL_OCCUPANCY) + [s[0] for s in
                                               [("C1",), ("C2",), ("C3",), ("C4",), ("C5",)]]:
            truth = int(round(self.population_in(node)))
            smoke = min(1.0, self.hazard.state.smoke_at(node) + self.manual_smoke)
            cam = camera_reading(truth, smoke,
                                 online=node not in self.camera_offline, rng=self.rng)
            prs = presence_reading(truth, smoke, clutter=CLUTTER.get(node, 0.12),
                                   rng=self.rng)
            self.fused[node] = fuse(node, [cam, prs])

    # -- PLAN ---------------------------------------------------------------

    def make_plan(self, reason: str) -> None:
        self.phase = "PLAN"
        self.phases_fired.append("PLAN")
        demand = {node: planning_count(f) for node, f in self.fused.items()
                  if planning_count(f) > 0}
        occupancy = {node: self.population_in(node) for node in self.building.nodes}
        # People already queueing in a stair core are part of its congestion.
        for stair, flow in self.observed_flow.items():
            occupancy[stair] = occupancy.get(stair, 0.0) + flow * 4.0

        self.previous_plan = self.plan if self.plan.routes else None
        self.plan = self.router.plan(demand, occupancy, self.t)
        self.last_plan_t = self.t
        self.replan_count += 1
        self.replan_reason = reason
        self._annotate_reasons()

    def _annotate_reasons(self) -> None:
        """Explain every route that changed since the previous plan."""
        prev = self.previous_plan
        for r in self.plan.routes:
            if prev is None:
                r.reason = "initial allocation by travel time and core capacity"
                continue
            old = prev.route_for(r.origin)
            if old is None:
                r.reason = "newly occupied space"
            elif old.stair != r.stair:
                smoke_on_old = max(
                    (self.hazard.state.smoke_at(n) for n in old.path), default=0.0)
                if smoke_on_old > 0.25:
                    r.reason = f"smoke on the {old.stair[-1]} route"
                else:
                    r.reason = (f"{old.stair[-1]} at capacity; "
                                f"{r.stair[-1]} is faster despite the distance")
            elif abs(old.travel_time - r.travel_time) > 6:
                r.reason = "travel time revised for measured congestion"
            else:
                r.reason = old.reason or "unchanged"

    # -- GUIDE --------------------------------------------------------------

    def guidance(self) -> list[dict]:
        """Per-zone instructions, derived from the allocation, not hand-written."""
        self.phase = "GUIDE"
        self.phases_fired.append("GUIDE")
        zones: dict[str, dict] = {}
        for r in self.plan.routes:
            seg = next((n for n in r.path if n.startswith("C")), None)
            if not seg or not r.stair:
                continue
            z = zones.setdefault(seg, {"zone": seg, "counts": {}, "people": 0})
            z["counts"][r.stair] = z["counts"].get(r.stair, 0) + r.people
            z["people"] += r.people

        out = []
        for seg, z in sorted(zones.items()):
            if not z["counts"]:
                continue
            stair = max(z["counts"], key=lambda s: z["counts"][s])
            letter = stair[-1]
            blocked = [n for n in self.hazard.state.blocked
                       | {n for n in self.hazard.state.smoke
                          if not self.hazard.state.is_passable(n)}]
            avoid = ""
            if any(b.startswith("C") or b.startswith("R") for b in blocked):
                avoid = " Do not use the north-east corridor."
            out.append({
                "zone": seg,
                "device": f"SPK-{seg}",
                "led": f"LED-{seg}",
                "people": z["counts"][stair],
                "stair": stair,
                "message": f"Zone {seg}: evacuate using Stair {letter}.{avoid}",
                "split": {s: c for s, c in z["counts"].items() if s != stair},
            })
        return out

    # -- OBSERVE ------------------------------------------------------------

    def move_people(self) -> None:
        """
        Advance the crowd one tick.

        Every passage is modelled as a pipe with two independent limits, and
        both of them matter:

          * a flow gate at the entrance — a 1.2 m stair does not admit more
            than about one person per second however many want through it,
          * a transit delay inside — a three-storey stair descent takes the
            time it takes, and the walking speed falls as the space fills.

        People who enter a passage are held in `self.transit` until they are
        due out the far end. That is what produces a queue building up behind
        a bottleneck instead of a crowd teleporting through it, and the queue
        is the thing the planner exists to prevent.
        """
        self.phase = "OBSERVE"
        self.phases_fired.append("OBSERVE")
        arrivals: dict[str, float] = {}
        self.discharge = {}

        # 1. Anyone whose transit has completed arrives at the far end.
        still: list[dict] = []
        for p in self.transit:
            if p["ready_at"] > self.t:
                still.append(p)
                continue
            dst = p["to"]
            if dst.startswith("EXIT"):
                self.evacuated += p["count"]
                continue
            bucket = self.people.setdefault(dst, [])
            g = next((g for g in bucket if g.target == p["target"]), None)
            if g:
                g.count += p["count"]
            else:
                bucket.append(Group(p["target"], p["count"]))
            if dst.startswith("STAIR"):
                arrivals[dst] = arrivals.get(dst, 0.0) + p["count"]
        self.transit = still
        self._pop_cache = None

        # 2. Admit people into the passages they are heading for.
        for node, groups in list(self.people.items()):
            if node.startswith("EXIT"):
                continue
            if self.mode != "clearing" and not node.startswith("STAIR"):
                continue          # no alarm yet, nobody is leaving
            ready = self.premove.get(node)
            if ready is not None and self.t < ready:
                continue          # alarm heard, not yet moving
            here = self.waiting_in(node)
            if here <= 0.01:
                continue
            for g in groups:
                if g.count <= 0.01:
                    continue
                target = g.target
                if target == REFUGE:
                    continue      # awaiting assisted evacuation
                if node == target and target.startswith("STAIR"):
                    target = "EXIT-" + node[-1]
                    g.target = target
                nxt = self._next_hop(node, target)
                if nxt is None:
                    continue
                edge = next((e for e in self.building.neighbours(node) if e.b == nxt), None)
                if edge is None:
                    continue

                share = g.count / max(here, 0.01)
                admitted = min(g.count, edge.capacity * TICK_SECONDS * share)
                if admitted <= 0.001:
                    continue

                speed = self.router.walking_speed(edge, self._edge_occupancy(edge))
                transit_time = edge.length_m / max(speed, 0.08)

                if edge.kind == "stair":
                    self.discharge[node] = self.discharge.get(node, 0.0) + admitted

                g.count -= admitted
                self.transit.append({
                    "edge": edge.key, "from": node, "to": nxt, "target": target,
                    "count": admitted, "ready_at": self.t + transit_time,
                })

        for node in list(self.people):
            if node.startswith("EXIT"):
                self.people[node] = []
            else:
                merged: dict[str, float] = {}
                for g in self.people[node]:
                    merged[g.target] = merged.get(g.target, 0.0) + g.count
                self.people[node] = [Group(t, c) for t, c in merged.items()
                                     if c > 1e-7]
        self._pop_cache = None

        # Upper floors discharge into the cores independently of this floor's plan.
        self._external_inflow(arrivals)

        # Exponentially smoothed observed flow, persons/second per core.
        for stair in self.building.stairs:
            inst = arrivals.get(stair, 0.0) / TICK_SECONDS
            prev = self.observed_flow.get(stair, 0.0)
            self.observed_flow[stair] = prev * 0.70 + inst * 0.30
        for stair, count in arrivals.items():
            if count > 0.001:
                self.arrival_log.append((self.t, stair, count))
        cutoff = self.t - OBSERVE_WINDOW
        self.arrival_log = [a for a in self.arrival_log if a[0] >= cutoff]
        self.stair_arrivals = {k: round(v, 2) for k, v in arrivals.items()}
        for stair in self.building.stairs:
            inst = self.discharge.get(stair, 0.0) / TICK_SECONDS
            self.discharge_rate[stair] = (
                self.discharge_rate.get(stair, 0.0) * 0.7 + inst * 0.3)

    def _edge_occupancy(self, edge) -> float:
        """How many people are currently inside this passage."""
        return sum(p["count"] for p in self.transit if p["edge"] == edge.key)

    def in_transit_at(self, node: str) -> float:
        """People who have left `node` but have not yet arrived anywhere."""
        return sum(p["count"] for p in self.transit if p["from"] == node)

    def _external_inflow(self, arrivals: dict[str, float]) -> None:
        """
        Levels 4 and 5 come down through the cores on their own.

        Those floors are not modelled room by room and SENSE AI is not
        guiding them, so they arrive in the stairs as a load this floor's plan
        did not create. They still have to descend and queue like everyone
        else, which is what turns the west core into a real bottleneck rather
        than a scripted one.
        """
        if self.mode != "clearing" or self.off_floor_remaining <= 0:
            return
        if self.t < self.alarm_t + UPPER_FLOOR_LAG:
            return
        take = min(self.off_floor_remaining, 0.58 * TICK_SECONDS)
        self.off_floor_remaining -= take
        # Before the redistribution beat, the upper floors overwhelmingly use
        # the west core — which is exactly the congestion SENSE AI has to see.
        if self.t < 74:
            split = {"STAIR-A": 0.74, "STAIR-C": 0.18, "STAIR-B": 0.08}
        else:
            split = {"STAIR-A": 0.38, "STAIR-C": 0.37, "STAIR-B": 0.25}
        for stair, frac in split.items():
            share = take * frac
            if share <= 0:
                continue
            arrivals[stair] = arrivals.get(stair, 0.0) + share
            exit_id = "EXIT-" + stair[-1]
            bucket = self.people.setdefault(stair, [])
            g = next((g for g in bucket if g.target == exit_id), None)
            if g:
                g.count += share
            else:
                bucket.append(Group(exit_id, share))
            self._pop_cache = None

    def _next_hop(self, node: str, target: str) -> str | None:
        hit = self._hop_cache.get((node, target))
        if hit is not None:
            return hit or None
        occupancy = {n: self.population_in(n) for n in self.building.nodes}
        path, _cost = self.router.shortest_path(node, {target}, {}, occupancy)
        if len(path) < 2:
            # The chosen core is unreachable; fall back to any exit at all.
            path, _c = self.router.shortest_path(
                node, set(self.building.exits), {}, occupancy)
        hop = path[1] if len(path) >= 2 else ""
        self._hop_cache[(node, target)] = hop
        return hop or None

    # -- COMPARE + RECALCULATE ---------------------------------------------

    def observed_window(self) -> dict[str, float]:
        """Where people actually committed over the last OBSERVE_WINDOW seconds."""
        out: dict[str, float] = {}
        for _t, stair, count in self.arrival_log:
            out[stair] = out.get(stair, 0.0) + count
        return out

    def compare(self) -> None:
        self.phase = "COMPARE"
        self.phases_fired.append("COMPARE")
        self.comparison = compare_plan_to_reality(self.plan, self.observed_window())

    def apply_guidance(self) -> None:
        """Reassign each space's groups to the core the current plan gives it."""
        for r in self.plan.routes:
            if not r.stair or r.origin not in self.people:
                continue
            groups = self.people[r.origin]
            refuge = sum(g.count for g in groups if g.target == REFUGE)
            total = sum(g.count for g in groups if g.target != REFUGE)
            if total <= 0:
                if refuge:
                    self.people[r.origin] = [Group(REFUGE, refuge)]
                continue
            follow = total * self.compliance
            stay = total - follow
            familiar = FAMILIAR_STAIR.get(r.origin, r.stair)
            rebuilt = [Group(r.stair, follow), Group(familiar, stay)]
            if refuge > 0:
                rebuilt.append(Group(REFUGE, refuge))
            self.people[r.origin] = [g for g in rebuilt if g.count > 1e-7]
            self._pop_cache = None
            # Merge duplicates when the plan agrees with habit.
            if r.stair == familiar:
                merged = [Group(r.stair, total)]
                if refuge > 0:
                    merged.append(Group(REFUGE, refuge))
                self.people[r.origin] = merged

    # -- scripted incident --------------------------------------------------

    def run_script(self) -> None:
        for step in self.script:
            if step.fired or self.t < step.t:
                continue
            step.fired = True
            self.timeline.append({"t": step.t, "key": step.key, "title": step.title,
                                  "detail": step.detail})
            self._apply_step(step)

    def _apply_step(self, step: ScriptStep) -> None:
        k = step.key
        if k == "ignition":
            self.hazard.ignite("R305", self.t)
            self._log("hazard", step.title, detail=step.detail, severity="critical")
        elif k == "detection":
            self._log("alarm", "ALARM — fire confirmed in Room 305",
                      detail="HEAT-305 latched; vision layer agrees. Evacuation initiated.",
                      severity="critical")
            self.mode = "clearing"
            self.alarm_t = self.t
            for room, delay in PREMOVE.items():
                self.premove[room] = self.t + delay + self.rng.uniform(
                    -PREMOVE_JITTER, PREMOVE_JITTER)
        elif k == "spread":
            self.hazard.inject_smoke("C4", 0.18)
            self._log("hazard", step.title, detail=step.detail, severity="warn")
        elif k == "blind":
            self.hazard.inject_smoke("C4", 0.22)
            self._log("sensor", "CAM-305 and CAM-C4 degraded",
                      detail="Optical confidence below the usable threshold.",
                      severity="warn")
        elif k == "nonvisual":
            self._log("sensor", "Non-visual layer is now the primary estimator for C4 / 305",
                      detail="mmWave presence signatures weighted above the cameras.",
                      severity="info")
        elif k == "congestion":
            self._log("flow", step.title, detail=step.detail, severity="warn")
        elif k == "observe":
            self._log("compare", step.title, detail=step.detail, severity="warn")
        elif k == "recalc":
            self.make_plan("observed Stair A overload + smoke on the C4 route")
            self._log("plan", step.title, detail=step.detail, severity="info")
        elif k == "redistribute":
            self.make_plan("population rebalanced across all three cores")
            self.apply_guidance()
            loads = ", ".join(f"{s[-1]}:{n}" for s, n in sorted(self.plan.stair_load.items()))
            self._log("plan", "Groups redistributed", detail=f"Core loads now {loads}.",
                      severity="info")
        elif k == "reroute":
            self._log("guide", step.title, detail=step.detail)
        elif k == "speakers":
            self.guidance_active = True
            self.compliance = GUIDED_COMPLIANCE
            self.apply_guidance()
            self._log("guide", "Zoned voice guidance engaged",
                      detail="Per-zone messages issued. Modelled compliance "
                             f"{int(GUIDED_COMPLIANCE * 100)}%.")
        elif k == "responder":
            # Smoke migrating out of C4 reaches the refuge point, which is
            # precisely when knowing who is in there stops being academic.
            self.hazard.inject_smoke("R314", 0.26)
            self._log("responder", "Room 314 flagged for the responder view",
                      detail="Camera visibility low; presence signatures held.",
                      severity="warn")
        elif k == "loop":
            self._log("system", step.title, detail=step.detail)
        elif k == "assisted":
            self.assisted_released = True
            for node, groups in self.people.items():
                for g in groups:
                    if g.target == REFUGE:
                        g.target = "STAIR-C"
            self._log("responder", step.title, detail=step.detail, severity="ok")

    # -- tick ---------------------------------------------------------------

    def tick(self) -> None:
        if not self.running:
            return
        self.tick_no += 1
        self.t += TICK_SECONDS
        self.phases_fired = []
        self._invalidate()

        self.run_script()
        self.sense()
        self.estimate()

        if self.mode in ("clearing", "incident"):
            self.move_people()
            self.compare()

        gap = self.t - self.last_plan_t
        due = gap >= PLAN_INTERVAL
        diverged = self.comparison.get("replan_required") and gap >= MIN_REPLAN_GAP
        if due or diverged:
            reason = ("observed movement diverges from the plan"
                      if diverged and not due else "scheduled replanning cycle")
            self.make_plan(reason)
            if self.mode == "clearing":
                self.apply_guidance()
            self.phase = "RECALCULATE"
            self.phases_fired.append("RECALCULATE")
        elif self.mode == "clearing":
            self.guidance()

        if self.mode == "clearing" and self.remaining <= 0.05:
            self.mode = "cleared"
            self.running = False
            self._log("system", f"Building clear at T+{int(self.t)}s",
                      detail=f"{TOTAL_POPULATION} people accounted for.", severity="ok")

    # -- responder support --------------------------------------------------

    def responder_intel(self) -> dict:
        """
        Decision support for an arriving crew. Not a command, and not certified.

        The recommendation minimises the crew's exposure: it prefers a core
        that is not carrying the evacuation flow, and it refuses to route
        through a space the model believes is untenable.
        """
        occupied = []
        for node, f in self.fused.items():
            if f.high <= 0:
                continue
            smoke = self.hazard.state.smoke_at(node)
            occupied.append({
                "node": node,
                "name": self.building.node(node).name,
                "display": f.display,
                "confidence": round(f.confidence, 3),
                "primary": f.primary,
                "smoke": round(smoke, 2),
                "camera": next((s.status for s in f.sources if s.source == "camera"), "n/a"),
                "presence": next((s.status for s in f.sources if s.source == "presence"), "n/a"),
            })
        occupied.sort(key=lambda r: (-r["smoke"], -r["high"] if "high" in r else 0))

        target = "R314"
        occ = {n: self.population_in(n) for n in self.building.nodes}
        # Responders climb against the evacuation flow, so the core carrying
        # the most egress traffic is penalised rather than preferred.
        window = self.observed_window()
        busiest = max(window, key=lambda s: window.get(s, 0.0), default="") \
            or max(self.plan.stair_load, key=lambda s: self.plan.stair_load.get(s, 0),
                   default="STAIR-A")
        for n in self.building.nodes:
            if n == busiest:
                occ[n] = occ.get(n, 0) + 40

        primary, p_cost = self.router.shortest_path("EXIT-C", {target}, {}, occ)
        alt_occ = dict(occ)
        for n in primary[1:-1]:
            alt_occ[n] = alt_occ.get(n, 0) + 60     # force a genuinely different line
        alternative, a_cost = self.router.shortest_path("EXIT-B", {target}, {}, alt_occ)

        # No path at all is a real answer, and the one a crew most needs said
        # plainly rather than dressed up as a route with an infinite cost.
        def leg(path: list[str], cost: float, origin: str, label: str) -> dict:
            reachable = bool(path) and math.isfinite(cost)
            return {
                "path": path if reachable else [],
                "cost": round(cost, 1) if reachable else None,
                "reachable": reachable,
                "from": origin,
                "label": label if reachable else label + " — no viable line",
            }

        def place(nid: str) -> str:
            return self.building.node(nid).human.split(" — ")[0] if nid in self.building.nodes else nid

        warnings = []
        if not (primary and math.isfinite(p_cost)):
            warnings.append("There is no safe way in to this room from any entrance. "
                            "Every route is blocked or too dangerous.")
        for n in primary:
            sm = self.hazard.state.smoke_at(n)
            if sm > 0.3:
                warnings.append(f"{place(n)} — heavy smoke, breathing apparatus needed")
            elif sm > 0.15:
                warnings.append(f"{place(n)} — smoke building up")
            if self.hazard.state.fire_at(n) > 0.05:
                warnings.append(f"{place(n)} — active fire")
        if not warnings:
            warnings.append("Nothing dangerous on the recommended route.")

        return {
            "target": target,
            "target_name": self.building.node(target).human,
            "occupied": occupied[:10],
            "primary": leg(primary, p_cost, "EXIT-C", "Central core, lobby entry"),
            "alternative": leg(alternative, a_cost, "EXIT-B", "East core, yard entry"),
            "reason": (
                # At rest there is no crowd and no fire, and saying otherwise
                # would be inventing an emergency that has not happened.
                "Nothing is happening yet. This is simply the shortest safe way in."
                if self.mode == "normal" else
                (f"Most people leaving right now are coming down Stair {busiest[-1]}, "
                 f"so going up it would mean pushing against the crowd. "
                 if window.get(busiest, 0) > 1 else
                 f"The rush has mostly cleared; Stair {busiest[-1]} carried most of it. ")
                + "This route avoids that staircase and stays clear of the room on fire."),
            "warnings": warnings,
            "disclaimer": ("This is information to help the officer in charge decide. "
                           "It does not give orders, and it is not an approved "
                           "life-safety system."),
        }

    # -- serialisation ------------------------------------------------------

    def snapshot(self) -> dict:
        stair_status = {}
        for stair in self.building.stairs:
            edge = next((e for e in self.building.edges
                         if e.a == stair and e.kind == "stair"), None)
            cap = edge.capacity if edge else 1.0
            flow = self.discharge_rate.get(stair, 0.0)
            queue = self.waiting_in(stair)
            descending = self.population_in(stair) - queue
            # A core is "congested" when people are waiting to get into it
            # faster than it can pass them, not merely when it is busy.
            demand = flow + queue / 25.0
            stair_status[stair] = {
                "capacity": cap,
                "flow": round(flow, 2),
                "arriving": round(self.observed_flow.get(stair, 0.0), 2),
                "utilisation": round(min(demand / max(cap, 0.01), 2.5), 2),
                "assigned": self.plan.stair_load.get(stair, 0),
                "queue": round(queue, 1),
                "descending": round(descending, 1),
            }

        window = self.observed_window()
        return {
            "t": round(self.t, 1),
            "observed_window": {k: round(v, 1) for k, v in window.items()},
            "observe_window_s": OBSERVE_WINDOW,
            "tick": self.tick_no,
            "running": self.running,
            "mode": self.mode,
            "phase": self.phase,
            "phases_fired": self.phases_fired,
            "compliance": round(self.compliance, 2),
            "guidance_active": self.guidance_active,
            "total_population": TOTAL_POPULATION,
            "remaining": round(self.remaining, 1),
            "on_floor": round(self.on_floor_remaining, 1),
            "in_cores": round(self.in_cores, 1),
            "evacuated": round(min(self.evacuated, TOTAL_POPULATION), 1),
            "off_floor_remaining": round(self.off_floor_remaining, 1),
            "occupancy": {k: v.as_dict() for k, v in self.fused.items()},
            "truth": {k: round(self.population_in(k), 1) for k in self.building.nodes
                      if self.population_in(k) > 0.05},
            "hazard": self.hazard.state.as_dict(),
            "plan": self.plan.as_dict(),
            "guidance": self.guidance() if self.mode != "normal" else [],
            "comparison": self.comparison,
            "stairs": stair_status,
            "timeline": self.timeline,
            "events": self.events[-14:],
            "replan_count": self.replan_count,
            "replan_reason": self.replan_reason,
            "clearance_estimate": round(self.plan.clearance_time, 0),
            "premove": {k: round(max(0.0, v - self.t), 1)
                        for k, v in self.premove.items() if v > self.t},
            "in_transit": round(sum(p["count"] for p in self.transit), 1),
            "manual_smoke": round(self.manual_smoke, 2),
            "camera_offline": sorted(self.camera_offline),
            "refuge": {n: round(sum(g.count for g in gs if g.target == REFUGE), 1)
                       for n, gs in self.people.items()
                       if any(g.target == REFUGE and g.count > 0.01 for g in gs)},
        }
