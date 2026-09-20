"""
The building digital twin.

SENSE AI reasons over a *graph*, not a picture. Every room, corridor segment,
stair shaft and exit is a node; every doorway and passage is an edge with a
physical width, a length and therefore a flow capacity (persons per second).
The SVG floor plan the browser draws is generated from this same structure, so
what an operator sees and what the planner computes can never drift apart.

Geometry is authored in one flat coordinate space (a 1000x560 viewBox) because
the plan is a schematic, not a CAD drawing. Swapping this module for an IFC or
gbXML importer would not change any other file — that is the point of keeping
the twin behind `load_building()`.

Flow capacities follow the usual engineering rule of thumb for egress design:
roughly 1.3 persons per second per metre of clear width, derated for stairs.
They are plausible planning numbers for a simulation, not certified figures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Specific flow (persons / second / metre of clear width).
FLOW_PER_METRE_LEVEL = 1.30
FLOW_PER_METRE_STAIR = 0.90

# Free walking speed on the level and on stairs (metres / second).
SPEED_LEVEL = 1.25
SPEED_STAIR = 0.65

# Crowd density at which movement stops (persons / square metre).
DENSITY_JAM = 3.8


@dataclass
class Node:
    """A space in the building: a room, a corridor segment, a stair or an exit."""

    id: str
    kind: str                 # room | corridor | stair | exit
    name: str
    floor: int
    x: float                  # schematic rectangle, in viewBox units
    y: float
    w: float
    h: float
    area_m2: float            # usable floor area, for density
    label: str = ""           # short tag drawn on the plan
    use: str = "office"       # office | meeting | lab | core | circulation
    human: str = ""           # plain-English name, for anyone who is not an engineer

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "name": self.name, "floor": self.floor,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "cx": round(self.cx, 1), "cy": round(self.cy, 1),
            "area_m2": self.area_m2, "label": self.label or self.id, "use": self.use,
            "human": self.human or self.name,
        }


@dataclass
class Edge:
    """A traversable connection. Directed pairs are created automatically."""

    a: str
    b: str
    length_m: float
    width_m: float
    kind: str = "level"       # level | door | stair | discharge

    @property
    def capacity(self) -> float:
        """Maximum sustainable flow through this connection, persons/second."""
        rate = FLOW_PER_METRE_STAIR if self.kind == "stair" else FLOW_PER_METRE_LEVEL
        return round(rate * self.width_m, 2)

    @property
    def free_speed(self) -> float:
        return SPEED_STAIR if self.kind == "stair" else SPEED_LEVEL

    @property
    def key(self) -> str:
        return f"{self.a}->{self.b}"

    def as_dict(self) -> dict:
        return {
            "a": self.a, "b": self.b, "length_m": self.length_m,
            "width_m": self.width_m, "kind": self.kind,
            "capacity": self.capacity,
        }


@dataclass
class Device:
    """A physical device mounted in a space and bound to the live model."""

    id: str
    kind: str                 # camera | presence | smoke | heat | speaker | led | panel
    node: str
    model: str
    status: str = "online"    # online | degraded | offline
    note: str = ""
    future: bool = False      # part of the proposed future hardware layer

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "node": self.node, "model": self.model,
            "status": self.status, "note": self.note, "future": self.future,
        }


@dataclass
class Building:
    name: str
    nodes: dict[str, Node]
    edges: list[Edge]
    devices: list[Device]
    floors: list[dict]
    adjacency: dict[str, list[Edge]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adjacency = {nid: [] for nid in self.nodes}
        for e in self.edges:
            self.adjacency[e.a].append(e)
            # every passage is walkable in both directions
            self.adjacency[e.b].append(
                Edge(e.b, e.a, e.length_m, e.width_m, e.kind)
            )

    def node(self, nid: str) -> Node:
        return self.nodes[nid]

    def neighbours(self, nid: str) -> list[Edge]:
        return self.adjacency.get(nid, [])

    @property
    def exits(self) -> list[str]:
        return [n.id for n in self.nodes.values() if n.kind == "exit"]

    @property
    def stairs(self) -> list[str]:
        return [n.id for n in self.nodes.values() if n.kind == "stair"]

    def rooms_on(self, floor: int) -> list[Node]:
        return [n for n in self.nodes.values() if n.floor == floor and n.kind == "room"]

    def devices_in(self, node_id: str) -> list[Device]:
        return [d for d in self.devices if d.node == node_id]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "floors": self.floors,
            "nodes": {nid: n.as_dict() for nid, n in self.nodes.items()},
            "edges": [e.as_dict() for e in self.edges],
            "devices": [d.as_dict() for d in self.devices],
            "exits": self.exits,
            "stairs": self.stairs,
        }


# ---------------------------------------------------------------------------
# The demo building: Tower B, a six-storey mixed office block.
# Floor 3 is modelled room-by-room because that is where the incident starts.
# Floors 1, 2, 4, 5 are modelled as aggregate plates — enough to make the
# stair loading realistic without pretending we have a full survey of them.
# ---------------------------------------------------------------------------

# Room strips on floor 3. (id, x, width, display name, use, area m2, seats)
_NORTH = [
    ("R301", 102, 132, "Open desk north", "office", 74, 9, "Room 301 — open-plan desks"),
    ("R302", 234, 132, "Meeting 302", "meeting", 74, 7, "Room 302 — meeting room"),
    ("R303", 366, 132, "Open desk 303", "office", 74, 8, "Room 303 — open-plan desks"),
    ("R304", 498, 133, "Lab 304", "lab", 75, 6, "Room 304 — lab"),
    ("R305", 631, 133, "Plant / server 305", "lab", 75, 3, "Room 305 — plant and server room"),
    ("R306", 764, 134, "Open desk east", "office", 75, 8, "Room 306 — open-plan desks"),
]
_SOUTH = [
    ("R311", 102, 128, "Open desk south", "office", 63, 7, "Room 311 — open-plan desks"),
    ("R312", 230, 128, "Meeting 312", "meeting", 63, 6, "Room 312 — meeting room"),
    ("R313", 468, 105, "Store 313", "office", 52, 2, "Room 313 — storeroom"),
    ("R314", 573, 130, "Open desk 314", "office", 64, 8, "Room 314 — open-plan desks"),
    ("R315", 703, 98, "Quiet room 315", "office", 48, 4, "Room 315 — quiet room"),
    ("R316", 801, 97, "Meeting 316", "meeting", 48, 5, "Room 316 — meeting room"),
]

# Corridor spine, split into segments so congestion can be localised.
_SEGMENTS = [
    ("C1", 102, 148, "West corridor"),
    ("C2", 250, 160, "West-central corridor"),
    ("C3", 410, 160, "Central corridor"),
    ("C4", 570, 160, "East-central corridor"),
    ("C5", 730, 168, "East corridor"),
]

# Which corridor segment each room's door opens onto.
_DOOR = {
    "R301": "C1", "R302": "C2", "R303": "C3", "R304": "C3", "R305": "C4", "R306": "C5",
    "R311": "C1", "R312": "C2", "R313": "C3", "R314": "C4", "R315": "C5", "R316": "C5",
}

CORRIDOR_Y, CORRIDOR_H = 245.0, 66.0
NORTH_Y, NORTH_H = 62.0, 183.0
SOUTH_Y, SOUTH_H = 311.0, 160.0


def load_building() -> Building:
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    devices: list[Device] = []

    def add(n: Node) -> None:
        nodes[n.id] = n

    # -- floor 3 rooms -----------------------------------------------------
    for rid, x, w, name, use, area, _seats, human in _NORTH:
        add(Node(rid, "room", name, 3, x, NORTH_Y, w, NORTH_H, area, rid[1:], use, human))
    for rid, x, w, name, use, area, _seats, human in _SOUTH:
        add(Node(rid, "room", name, 3, x, SOUTH_Y, w, SOUTH_H, area, rid[1:], use, human))

    # -- floor 3 corridor spine -------------------------------------------
    for cid, x, w, human in _SEGMENTS:
        add(Node(cid, "corridor", human, 3, x, CORRIDOR_Y, w, CORRIDOR_H,
                 round(w * CORRIDOR_H / 90, 1), cid, "circulation", human))
    for (a, *_), (b, *_) in zip(_SEGMENTS, _SEGMENTS[1:]):
        edges.append(Edge(a, b, 12.0, 2.4, "level"))

    # -- room doors --------------------------------------------------------
    for rid, seg in _DOOR.items():
        edges.append(Edge(rid, seg, 6.0, 1.1, "door"))

    # -- stair shafts ------------------------------------------------------
    add(Node("STAIR-A", "stair", "Stair A (west core)", 3, 40, NORTH_Y, 62, 409, 24, "A",
             "core", "Stair A — west, the narrow one"))
    add(Node("STAIR-B", "stair", "Stair B (east core)", 3, 898, NORTH_Y, 62, 409, 24, "B",
             "core", "Stair B — east"))
    add(Node("STAIR-C", "stair", "Stair C (central core)", 3, 358, SOUTH_Y, 110, 160, 30, "C",
             "core", "Stair C — central, the widest"))

    # Stair A is the narrow legacy core; C is the wide central core.
    edges.append(Edge("C1", "STAIR-A", 8.0, 1.2, "level"))
    edges.append(Edge("C5", "STAIR-B", 8.0, 1.4, "level"))
    edges.append(Edge("C3", "STAIR-C", 8.0, 1.8, "level"))

    # -- discharge to grade -------------------------------------------------
    add(Node("EXIT-A", "exit", "Exit A — west door", 0, 40, 480, 62, 40, 20, "EXIT A",
             "core", "Exit A — west door to the street"))
    add(Node("EXIT-B", "exit", "Exit B — east yard", 0, 898, 480, 62, 40, 20, "EXIT B",
             "core", "Exit B — east door to the yard"))
    add(Node("EXIT-C", "exit", "Exit C — main lobby", 0, 358, 481, 110, 39, 40, "EXIT C",
             "core", "Exit C — main lobby, the main way out"))

    # Three storeys down to grade: about 8 m of tread per storey.
    edges.append(Edge("STAIR-A", "EXIT-A", 24.0, 1.2, "stair"))
    edges.append(Edge("STAIR-B", "EXIT-B", 24.0, 1.4, "stair"))
    edges.append(Edge("STAIR-C", "EXIT-C", 24.0, 1.8, "stair"))

    # -- devices ------------------------------------------------------------
    cam_rooms = ["R301", "R303", "R304", "R305", "R306", "R311", "R313", "R314", "R316"]
    for rid in cam_rooms:
        devices.append(Device(f"CAM-{rid[1:]}", "camera", rid, "edge-AI camera / person detection"))
    for cid, *_ in _SEGMENTS:
        devices.append(Device(f"CAM-{cid}", "camera", cid, "edge-AI camera / person detection"))

    # Non-visual presence sensing is specified per space; performance depends on
    # mounting height and the material of the partitions, so it is never assumed
    # to be a perfect count.
    for rid in list(_DOOR) + [s[0] for s in _SEGMENTS]:
        devices.append(Device(
            f"PRS-{rid[1:] if rid.startswith('R') else rid}", "presence", rid,
            "60 GHz mmWave presence array",
            note="counts moving and micro-motion signatures; needs per-room calibration",
        ))
    for rid in ["R302", "R312", "R315"]:
        devices.append(Device(f"PRS-{rid[1:]}-T", "presence", rid,
                              "thermopile array (low-resolution IR)",
                              note="fallback where mmWave coverage is partial"))

    for rid in list(_DOOR) + [s[0] for s in _SEGMENTS]:
        tag = rid[1:] if rid.startswith("R") else rid
        devices.append(Device(f"SMK-{tag}", "smoke", rid, "photo-optical smoke detector"))
    for rid in ["R304", "R305"]:
        devices.append(Device(f"HEAT-{rid[1:]}", "heat", rid, "fixed-temperature heat detector"))

    for cid, *_ in _SEGMENTS:
        devices.append(Device(f"SPK-{cid}", "speaker", cid, "zoned voice-alarm speaker"))
    for sid in ["STAIR-A", "STAIR-B", "STAIR-C"]:
        devices.append(Device(f"SPK-{sid[-1]}", "speaker", sid, "stair-core voice-alarm speaker"))

    # Proposed hardware, clearly separated from what the MVP simulates today.
    for cid, *_ in _SEGMENTS:
        devices.append(Device(f"LED-{cid}", "led", cid,
                              "ceiling directional LED strip",
                              note="proposed future guidance output", future=True))
    for sid in ["STAIR-A", "STAIR-B", "STAIR-C"]:
        devices.append(Device(f"LED-{sid[-1]}", "led", sid,
                              "stair-entry directional LED",
                              note="proposed future guidance output", future=True))
    devices.append(Device("PANEL-3", "panel", "C3", "fire alarm panel interface (BACnet / Modbus)",
                          note="proposed integration with building systems", future=True))

    floors = [
        {"floor": 5, "name": "Level 5 — offices", "population": 14, "modelled": False},
        {"floor": 4, "name": "Level 4 — offices", "population": 16, "modelled": False},
        {"floor": 3, "name": "Level 3 — offices and plant", "population": 41, "modelled": True},
        {"floor": 2, "name": "Level 2 — offices", "population": 9, "modelled": False},
        {"floor": 1, "name": "Level 1 — reception", "population": 4, "modelled": False},
    ]

    return Building("Tower B — Innovation Park", nodes, edges, devices, floors)


# Where people are before anything happens. 41 on the modelled floor,
# 43 spread over the four floors above and below = 84 in the building.
INITIAL_OCCUPANCY: dict[str, int] = {
    "R301": 5, "R302": 5, "R303": 4, "R304": 3, "R305": 1, "R306": 4,
    "R311": 3, "R312": 3, "R313": 1, "R314": 8, "R315": 2, "R316": 2,
}

# People who will not self-evacuate: a designated refuge point where occupants
# who cannot use the stairs unaided wait for an assisted evacuation. Every
# building has some, they are invisible to a headcount at the assembly point,
# and finding them is the single thing an arriving crew most needs to know.
ASSISTED_EVACUATION: dict[str, int] = {"R314": 5}
OFF_FLOOR_POPULATION = 43
TOTAL_POPULATION = sum(INITIAL_OCCUPANCY.values()) + OFF_FLOOR_POPULATION
