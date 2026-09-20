"""
Hot-work zones: polygon storage and spatial checks.

Polygons are stored in NORMALISED coordinates (0..1) so they do not depend on
the camera/frame resolution. Conversion to pixels uses the actual frame size.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ZoneCfg


@dataclass
class Zone:
    """A polygon zone (normalised vertices)."""
    id: str
    polygon: list[tuple[float, float]]   # [(x,y), ...], x,y in 0..1

    @classmethod
    def from_cfg(cls, z: ZoneCfg) -> "Zone":
        return cls(id=z.id, polygon=[(float(x), float(y)) for x, y in z.polygon])

    # --- geometry ---
    def contains_norm(self, x: float, y: float) -> bool:
        """Is the (normalised) point inside the polygon? Ray casting algorithm."""
        return _point_in_polygon(x, y, self.polygon)

    def contains_point(self, x: float, y: float, w: int, h: int) -> bool:
        """Is the pixel point inside the zone?"""
        return self.contains_norm(x / max(w, 1), y / max(h, 1))

    def near_norm(self, x: float, y: float, margin: float) -> bool:
        """
        Point inside the zone OR within margin (fraction of the frame) of its
        vertices. Used for the "extinguisher in or near the zone" check.
        """
        if self.contains_norm(x, y):
            return True
        for px, py in self.polygon:
            if abs(px - x) <= margin and abs(py - y) <= margin:
                return True
        return False

    def pixel_polygon(self, w: int, h: int) -> np.ndarray:
        """Polygon in pixels (for drawing with OpenCV)."""
        pts = [(int(px * w), int(py * h)) for px, py in self.polygon]
        return np.array(pts, dtype=np.int32)


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """Classic ray casting: parity of ray/edge intersections."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        # does edge (i, j) cross the horizontal ray from the point?
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def load_zones(zone_cfgs: list[ZoneCfg]) -> list[Zone]:
    return [Zone.from_cfg(z) for z in zone_cfgs]
