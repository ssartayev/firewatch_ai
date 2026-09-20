# SENSE AI

**Confidence-aware occupancy intelligence and building-wide evacuation optimisation.**

A decision-support prototype that models a burning building as *hazards + people +
topology + crowd movement + route capacity + sensor uncertainty*, and coordinates the
evacuation of the whole population rather than pointing each person at the nearest exit.

> This is a **product simulation** built for evaluation. It is not a certified life-safety
> system, it has no regulatory approval, and it does not guarantee detection or evacuation
> outcomes. Occupancy figures are estimates with stated confidence, not counts.

---

## The question it answers

Most evacuation systems answer: *where is the safest path?* Dynamic hazard detection, live
building models and changing exit signage all exist already, and SENSE AI does not claim
them.

SENSE AI answers a harder question:

> Where are the people, how certain are we, where are they actually moving, which routes
> are becoming congested, and how should the whole population be distributed across the
> exits that are left?

The differentiation is the **integration**: occupancy estimation with explicit confidence,
fusion across visual and non-visual sensing, observation of real crowd flow, route-capacity
modelling, population-level allocation, and repeated replanning against what people
actually did.

## The loop

```
SENSE → ESTIMATE → PLAN → GUIDE → OBSERVE → COMPARE → RECALCULATE
  ↑                                                        │
  └────────────────────────────────────────────────────────┘
```

It reacts not only when the fire changes, but when people do not follow instructions, when
a staircase overloads, or when a camera loses visibility.

## What is actually interesting here

Four things in this repository are real engineering rather than presentation:

**1. Confidence-aware fusion that knows what kind of error each sensor makes.**
Sources are combined by precision weighting — weight = `confidence² / (1 − confidence)`,
the reciprocal of variance. Fused confidence is noisy-OR across independent sources, and
below 80% the answer is published as a *range* rather than a number.

Crucially, an obscured camera is treated as a **lower bound, not a measurement**. A camera
in smoke never invents people, it only misses them; averaging its count in would drag the
estimate below the truth exactly when the truth matters most. So it can raise the estimate
and never pull it down. *(`app/occupancy.py`)*

**2. Allocation, not routing.** The shortest-path search is an ordinary Dijkstra and that
is not the claim. Edge cost is *expected travel time*: walking speed that falls with crowd
density (a Weidmann-style fundamental diagram), queueing delay that explodes above 85% of a
passage's flow capacity, and a hazard penalty rising steeply with smoke. The population is
then assigned in parcels of two, each parcel routed through a network that already knows
about the ones before it — incremental assignment. That is what spreads a building across
its exits instead of stacking it into one. *(`app/routing.py`)*

**3. A crowd that does not obey.** 72% of occupants follow guidance unaided, rising to 91%
once zoned voice guidance engages; the rest default to the stair they came in by. Every
space has a pre-movement delay — the 10–55 seconds between the alarm sounding and anyone
actually leaving, which is routinely the largest term in a real evacuation. Passages are
pipes with a flow gate at the entrance and a transit delay inside, so queues build instead
of crowds teleporting. *(`app/simulation.py`)*

**4. Closed-loop correction.** The system measures where people actually committed over a
rolling 30-second window, compares that distribution to the one it planned, and replans
when the divergence exceeds 18%. In the scripted incident the congestion originates on
floors the system is *not* guiding — so it has to be discovered by observation rather than
predicted. That is the moment worth watching.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --port 8010
```

Open **http://127.0.0.1:8010/** and press **Run emergency simulation**.

The detection layer is optional. `python scripts/download_models.py` fetches the YOLO
weights; without them the video panel reports its backend as `simulated` on screen rather
than pretending to run inference.

## The views

| Page | What it shows |
|---|---|
| `/` | Live floor plan and the full 14-beat scripted incident, with timeline, core loading and zone guidance |
| `/occupancy` | Per-space estimates, fusion detail, heavy-smoke and camera-failure controls, and the video analysis panel |
| `/evacuation` | Interactive routing — simulate fire, crowding, block a corridor, five named test scenarios, and the reason behind every route change |
| `/command` | Firefighter decision support on an isometric building model: who remains, how that was established, and two approach lines |
| `/twin` | The building graph, every device bound to a space, and what is simulated today vs proposed hardware |
| `/technology` | Architecture, model positioning, and an explicit list of what this prototype does not do |

## The scripted incident

84 people. Fire starts in Room 305 at T+6s. Detection at T+12s. Smoke enters corridor C4 and
the cameras there lose confidence; the mmWave layer takes over the estimate. At T+48s levels
4 and 5 discharge into the west core, which SENSE AI is not guiding — Stair A goes over
capacity. The system measures the divergence, redistributes the floor across Stairs B and C,
issues different instructions per zone, and compliance rises. Five occupants who cannot use
the stairs unaided remain at a refuge point in Room 314, and the responder view resolves
them. Building clear around T+3:40.

## Architecture

```
app/
  building.py     digital twin — node/edge graph, flow capacities, devices
  occupancy.py    confidence-aware fusion, lower-bound handling, ranges
  hazard.py       fire growth and smoke transport across the graph
  routing.py      time-varying cost, Dijkstra, incremental parcel allocation
  simulation.py   the control loop, crowd model, scripted incident
  vision.py       YOLO detection layer with an honest simulated fallback
  main.py         FastAPI routes, control API, WebSocket state stream
templates/        Jinja2 pages
static/js/        floorplan.js · isometric.js · sense.js — no framework, no build step
legacy/           FireWatch AI, the hot-work safety monitor this grew out of
```

**Python 3.11 · FastAPI · WebSockets · Ultralytics YOLO · OpenCV · Jinja2 · vanilla JS · SVG**

The simulation runs server-side and is broadcast to every connected client, so the floor
plan, the occupancy table and the responder view are three windows onto one model and
cannot disagree.

## Lineage

SENSE AI grew out of **FireWatch AI**, a computer-vision hot-work safety monitor built for a
pilot with BI Group: it watched a camera feed, detected fire and smoke with YOLO, checked
whether the legally required safety conditions were visible in the work zone, and alerted.
That project's fire/smoke detector is now the hazard-sensing layer here, and its code is
preserved under `legacy/` with its own README.

## Limitations

- **Not certified, no regulatory approval.** Not assessed against any life-safety standard.
- **Detection is experimental.** Published model figures are the authors' self-reported
  validation results on their own datasets, not measurements on this building.
- **The non-visual layer does not see through walls.** It detects motion and micro-motion
  signatures. Accuracy depends on mounting, partition materials and commissioning; it
  merges people standing close together and is never treated as an exact count.
- **The responder view is decision support.** Not autonomous command.
- **Flow capacities are plausible planning numbers**, following the usual egress rule of
  thumb (~1.3 persons/s/m level, 0.9 on stairs). They are not certified figures.
- **The building is schematic.** A real deployment would import IFC or gbXML behind
  `load_building()`; nothing else would change.
- **Not production-ready.** This is a simulation built to evaluate the concept.
