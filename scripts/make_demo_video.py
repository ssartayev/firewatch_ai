"""
Generator for the synthetic "hot work" demo clip.

Lets the system be run and demonstrated without a real camera and without
real fire footage. Scene: a dark workshop, a "welder" (rectangle) throwing
sparks bottom-left, an extinguisher in the corner, and from ~3s a flickering
fire growing in the centre of the frame (inside zone A).

Run:  python scripts/make_demo_video.py [output.mp4]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 960, 540, 25


def _draw_flame(img: np.ndarray, cx: int, cy: int, scale: float, rng: np.random.Generator) -> None:
    """Draw a flickering fire (orange-red ellipses plus a glow)."""
    overlay = img.copy()
    n = int(18 * scale)
    for _ in range(max(4, n)):
        dx = int(rng.normal(0, 22 * scale))
        dy = int(abs(rng.normal(0, 30 * scale)))
        rad = int(rng.integers(8, 26) * scale)
        # colour from yellow to red (BGR)
        b = int(rng.integers(0, 40))
        g = int(rng.integers(90, 200))
        r = 255
        cv2.circle(overlay, (cx + dx, cy - dy), rad, (b, g, r), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    # brighter flame core
    cv2.circle(img, (cx, cy), int(18 * scale), (0, 200, 255), -1)


def generate(output: str, seconds: int = 12) -> str:
    """Create the demo clip at output. Returns the path."""
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open VideoWriter (mp4v codec)")

    total = seconds * FPS
    fire_start = 3 * FPS  # fire appears at the 3-second mark

    for i in range(total):
        # gradient background (dark workshop)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[:] = (35, 33, 30)
        cv2.rectangle(frame, (0, int(H * 0.75)), (W, H), (55, 52, 48), -1)  # floor

        # "welder" plus sparks, bottom-left
        cv2.rectangle(frame, (90, 300), (170, 470), (70, 70, 70), -1)      # body
        cv2.circle(frame, (130, 285), 26, (60, 60, 60), -1)                # head
        for _ in range(12):
            sx = 175 + int(rng.integers(0, 60))
            sy = 400 + int(rng.integers(-30, 30))
            cv2.circle(frame, (sx, sy), int(rng.integers(1, 3)),
                       (0, int(rng.integers(180, 255)), 255), -1)

        # extinguisher in the bottom-right corner (red cylinder)
        cv2.rectangle(frame, (860, 360), (900, 470), (30, 30, 200), -1)
        cv2.rectangle(frame, (872, 340), (888, 360), (40, 40, 40), -1)

        # fire growing in the centre (inside zone A)
        if i >= fire_start:
            grow = min(1.6, 0.5 + (i - fire_start) / (total - fire_start) * 1.3)
            _draw_flame(frame, int(W * 0.5), int(H * 0.6), grow, rng)

        # timecode
        cv2.putText(frame, f"demo t={i / FPS:5.1f}s", (W - 210, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        writer.write(frame)

    writer.release()
    return str(out_path)


if __name__ == "__main__":
    default = Path(__file__).resolve().parent.parent / "data" / "demo_fire.mp4"
    out = sys.argv[1] if len(sys.argv) > 1 else str(default)
    path = generate(out)
    print(f"Demo video created: {path}")
