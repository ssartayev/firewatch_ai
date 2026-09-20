"""
Capacity-aware routing and building-wide population allocation.

The shortest-path search at the bottom of this file is an ordinary Dijkstra.
That is not the interesting part and the product does not claim it is. What
matters is everything that feeds it:

  1. Edge cost is *expected travel time*, not distance. It combines walking
     time at a speed that falls as crowd density rises, queueing delay when
     demand approaches the passage's flow capacity, and a hazard penalty that
     rises steeply with smoke before the space becomes impassable.

  2. People are not all sent down the best route. A single shortest path per
     room is what turns one staircase into a bottleneck while another stands
     empty. Instead the population is assigned in small parcels, and every
     parcel sees the congestion the previous parcels created. This is
     incremental assignment, and it is what spreads a building across its
     exits instead of stacking it into one.

  3. The counts being assigned are the *upper bound* of the occupancy range,
     so an uncertain room is planned for pessimistically rather than
     optimistically.

  4. The whole thing is recomputed from scratch every planning cycle, because
     the inputs — smoke, confidence, observed flow — are changing underneath
     it. The plan is a snapshot, never a commitment.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .building import DENSITY_JAM, Building, Edge
from .hazard import HazardModel, IMPASSABLE_SMOKE, PENALTY_SMOKE

# People are split into parcels of this size during assignment. Smaller is a
# finer allocation and more compute; 2 is a good balance at building scale.
PARCEL_SIZE = 2
# Cost multiplier applied to a space at the impassability threshold.
HAZARD_PENALTY_MAX = 900.0
BLOCKED_COST = 1e7


@dataclass
class RouteLeg:
    node: str
    cost: float


@dataclass
class Route:
    """One assigned path from an origin space to an exit."""

    origin: str
    exit_id: str
    path: list[str]
    people: int
    travel_time: float
    reason: str = ""

    @property
    def stair(self) -> str:
        for n in self.path:
            if n.startswith("STAIR"):
                return n
        return ""

    def as_dict(self) -> dict:
        return {
            "origin": self.origin, "exit": self.exit_id, "path": self.path,
            "people": self.people, "travel_time": round(self.travel_time, 1),
            "stair": self.stair, "reason": self.reason,
        }


@dataclass
class Plan:
    """The output of one planning cycle."""

    routes: list[Route] = field(default_factory=list)
    edge_load: dict[str, float] = field(default_factory=dict)
    node_load: dict[str, float] = field(default_factory=dict)
    exit_load: dict[str, int] = field(default_factory=dict)
    stair_load: dict[str, int] = field(default_factory=dict)
    clearance_time: float = 0.0
    unroutable: dict[str, int] = field(default_factory=dict)
    generated_at: float = 0.0

    def route_for(self, origin: str) -> Route | None:
        best = [r for r in self.routes if r.origin == origin]
        if not best:
            return None
        return max(best, key=lambda r: r.people)

    def as_dict(self) -> dict:
        return {
            "routes": [r.as_dict() for r in self.routes],
            "edge_load": {k: round(v, 2) for k, v in self.edge_load.items()},
            "node_load": {k: round(v, 2) for k, v in self.node_load.items()},
            "exit_load": self.exit_load,
            "stair_load": self.stair_load,
            "clearance_time": round(self.clearance_time, 1),
            "unroutable": self.unroutable,
            "generated_at": round(self.generated_at, 1),
        }


class Router:
    def __init__(self, building: Building, hazard: HazardModel):
        self.b = building
        self.hazard = hazard

    # -- cost model ---------------------------------------------------------

    def density(self, node: str, occupancy: float) -> float:
        area = max(self.b.node(node).area_m2, 1.0)
        return occupancy / area

    def walking_speed(self, edge: Edge, downstream_occupancy: float) -> float:
        """
        Speed falls linearly with crowd density until movement stops.

        This is the standard Weidmann-style fundamental diagram, simplified:
        an empty corridor is walked at free speed, a jammed one is not walked
        at all. It is why a longer, emptier route can be the faster one.
        """
        d = self.density(edge.b, downstream_occupancy)
        factor = max(0.12, 1.0 - d / DENSITY_JAM)
        return edge.free_speed * factor

    def hazard_penalty(self, node: str) -> float:
        h = self.hazard.state
        if not h.is_passable(node):
            return BLOCKED_COST
        smoke = h.smoke_at(node)
        fire = h.fire_at(node)
        if smoke <= PENALTY_SMOKE and fire <= 0.01:
            return 0.0
        # Steep but finite: a lightly smoked corridor is a last resort, not a
        # wall, because sometimes the alternative is worse.
        ratio = max(smoke - PENALTY_SMOKE, 0.0) / (IMPASSABLE_SMOKE - PENALTY_SMOKE)
        return HAZARD_PENALTY_MAX * (ratio ** 2.2) + 400.0 * fire

    def edge_cost(self, edge: Edge, load: dict[str, float],
                  occupancy: dict[str, float]) -> float:
        """Expected seconds to traverse this passage under current conditions."""
        downstream = occupancy.get(edge.b, 0.0) + load.get(edge.b, 0.0)
        speed = self.walking_speed(edge, downstream)
        walk = edge.length_m / max(speed, 0.05)

        # Queueing: as assigned demand approaches capacity the wait explodes.
        demand = load.get(edge.key, 0.0)
        utilisation = demand / max(edge.capacity * 60.0, 0.01)
        if utilisation <= 0.85:
            queue = 24.0 * utilisation
        else:
            queue = 20.4 + 220.0 * (utilisation - 0.85) ** 1.6

        return walk + queue + self.hazard_penalty(edge.b)

    # -- shortest path ------------------------------------------------------

    def shortest_path(self, origin: str, targets: set[str],
                      load: dict[str, float],
                      occupancy: dict[str, float]) -> tuple[list[str], float]:
        """Least expected travel time from origin to the nearest of `targets`."""
        dist = {origin: 0.0}
        prev: dict[str, str] = {}
        seen: set[str] = set()
        pq: list[tuple[float, str]] = [(0.0, origin)]

        while pq:
            d, node = heapq.heappop(pq)
            if node in seen:
                continue
            seen.add(node)
            if node in targets:
                path = [node]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return list(reversed(path)), d
            for edge in self.b.neighbours(node):
                if edge.b in seen:
                    continue
                c = self.edge_cost(edge, load, occupancy)
                if c >= BLOCKED_COST:
                    continue
                nd = d + c
                if nd < dist.get(edge.b, math.inf):
                    dist[edge.b] = nd
                    prev[edge.b] = node
                    heapq.heappush(pq, (nd, edge.b))

        return [], math.inf

    # -- population allocation ---------------------------------------------

    def plan(self, demand: dict[str, int], occupancy: dict[str, float],
             t: float = 0.0) -> Plan:
        """
        Distribute everyone in `demand` across all reachable exits.

        Parcels are assigned worst-first: the room that is hardest to get out
        of picks its route while the network is still empty, so the difficult
        cases are not left with whatever capacity happens to be unused. After
        each parcel the load it adds is written back, so the next parcel is
        routed through a network that already knows about it.
        """
        plan = Plan(generated_at=t)
        load: dict[str, float] = {}
        exits = set(self.b.exits)

        # Rank origins by how bad their situation is with an empty network.
        seeds: list[tuple[float, str, int]] = []
        for origin, people in demand.items():
            if people <= 0:
                continue
            _path, cost = self.shortest_path(origin, exits, {}, occupancy)
            seeds.append((-cost if cost < math.inf else -1e9, origin, people))
        seeds.sort()

        remaining = {origin: people for _c, origin, people in seeds}
        order = [origin for _c, origin, _p in seeds]
        assignments: dict[tuple[str, str], list] = {}

        while any(remaining[o] > 0 for o in order):
            for origin in order:
                if remaining[origin] <= 0:
                    continue
                parcel = min(PARCEL_SIZE, remaining[origin])
                path, cost = self.shortest_path(origin, exits, load, occupancy)

                if not path:
                    plan.unroutable[origin] = plan.unroutable.get(origin, 0) + remaining[origin]
                    remaining[origin] = 0
                    continue

                remaining[origin] -= parcel
                key = (origin, path[-1])
                if key in assignments:
                    assignments[key][0] += parcel
                    assignments[key][1] = max(assignments[key][1], cost)
                else:
                    assignments[key] = [parcel, cost, path]

                # Write the parcel's footprint back into the network.
                for a, b in zip(path, path[1:]):
                    load[f"{a}->{b}"] = load.get(f"{a}->{b}", 0.0) + parcel
                for n in path[1:]:
                    load[n] = load.get(n, 0.0) + parcel * 0.55

        for (origin, exit_id), (people, cost, path) in assignments.items():
            plan.routes.append(Route(origin, exit_id, path, people, cost))
            plan.exit_load[exit_id] = plan.exit_load.get(exit_id, 0) + people
            stair = next((n for n in path if n.startswith("STAIR")), "")
            if stair:
                plan.stair_load[stair] = plan.stair_load.get(stair, 0) + people

        plan.edge_load = {k: v for k, v in load.items() if "->" in k}
        plan.node_load = {k: v for k, v in load.items() if "->" not in k}
        plan.clearance_time = self.clearance_estimate(plan)
        plan.routes.sort(key=lambda r: -r.people)
        return plan

    def clearance_estimate(self, plan: Plan) -> float:
        """
        Time until the last person is out: the slowest of the stair cores.

        Each core has to move its assigned population through a fixed flow
        capacity, and nobody starts discharging until they have walked to it.
        The building's clearance time is whichever core finishes last, which
        is precisely why the allocation step exists.
        """
        worst = 0.0
        for stair, people in plan.stair_load.items():
            edge = next((e for e in self.b.edges
                         if e.a == stair and e.kind == "stair"), None)
            capacity = edge.capacity if edge else 1.0
            discharge = people / max(capacity, 0.05)
            approach = max(
                (r.travel_time for r in plan.routes if r.stair == stair), default=0.0
            )
            # Approach and discharge overlap, so the tail is what counts.
            worst = max(worst, min(approach, 90.0) + discharge)
        if plan.unroutable:
            worst = max(worst, 600.0)
        return worst


def compare_plan_to_reality(plan: Plan, observed_stair_flow: dict[str, float]
                            ) -> dict:
    """
    The closed-loop step: does the crowd actually do what the plan assumed?

    Real evacuees ignore guidance. They use the stair they came in by, they
    follow the person in front of them, and they avoid a route that looks
    crowded even when the model says it is fine. Measuring that divergence is
    what lets the system correct instead of repeating a plan nobody is
    executing.
    """
    planned_total = sum(plan.stair_load.values()) or 1
    observed_total = sum(observed_stair_flow.values())
    if observed_total < 1.0:
        # Nobody is moving yet. A plan cannot diverge from an empty building,
        # and reporting that it has would make the signal meaningless later.
        return {
            "rows": [{"stair": s, "planned": n,
                      "planned_share": round(n / planned_total, 3),
                      "observed_share": None, "divergence": 0.0}
                     for s, n in sorted(plan.stair_load.items())],
            "max_divergence": 0.0, "replan_required": False, "observing": False,
        }

    rows = []
    max_div = 0.0
    for stair in set(plan.stair_load) | set(observed_stair_flow):
        planned_share = plan.stair_load.get(stair, 0) / planned_total
        observed_share = observed_stair_flow.get(stair, 0.0) / observed_total
        divergence = observed_share - planned_share
        max_div = max(max_div, abs(divergence))
        rows.append({
            "stair": stair,
            "planned": plan.stair_load.get(stair, 0),
            "planned_share": round(planned_share, 3),
            "observed_share": round(observed_share, 3),
            "divergence": round(divergence, 3),
        })

    rows.sort(key=lambda r: -abs(r["divergence"]))
    return {
        "rows": rows,
        "max_divergence": round(max_div, 3),
        "replan_required": max_div > 0.18,
        "observing": True,
    }
