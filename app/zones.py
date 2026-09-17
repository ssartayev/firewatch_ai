"""
Зоны огневых работ: хранение полигонов и пространственные проверки.

Полигоны хранятся в НОРМАЛИЗОВАННЫХ координатах (0..1), чтобы не зависеть от
разрешения камеры/кадра. Перевод в пиксели — по фактическому размеру кадра.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ZoneCfg


@dataclass
class Zone:
    """Полигональная зона (нормализованные вершины)."""
    id: str
    polygon: list[tuple[float, float]]   # [(x,y), ...], x,y в 0..1

    @classmethod
    def from_cfg(cls, z: ZoneCfg) -> "Zone":
        return cls(id=z.id, polygon=[(float(x), float(y)) for x, y in z.polygon])

    # --- геометрия ---
    def contains_norm(self, x: float, y: float) -> bool:
        """Точка (нормализованная) внутри полигона? Алгоритм ray casting."""
        return _point_in_polygon(x, y, self.polygon)

    def contains_point(self, x: float, y: float, w: int, h: int) -> bool:
        """Точка в пикселях внутри зоны?"""
        return self.contains_norm(x / max(w, 1), y / max(h, 1))

    def near_norm(self, x: float, y: float, margin: float) -> bool:
        """
        Точка внутри зоны ИЛИ в пределах margin (доля кадра) от её вершин.
        Используется для огнетушителя «в зоне или рядом».
        """
        if self.contains_norm(x, y):
            return True
        for px, py in self.polygon:
            if abs(px - x) <= margin and abs(py - y) <= margin:
                return True
        return False

    def pixel_polygon(self, w: int, h: int) -> np.ndarray:
        """Полигон в пикселях (для отрисовки в OpenCV)."""
        pts = [(int(px * w), int(py * h)) for px, py in self.polygon]
        return np.array(pts, dtype=np.int32)


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """Классический ray casting: чётность пересечений луча с рёбрами полигона."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        # ребро (i, j) пересекает горизонтальный луч из точки?
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def load_zones(zone_cfgs: list[ZoneCfg]) -> list[Zone]:
    return [Zone.from_cfg(z) for z in zone_cfgs]
