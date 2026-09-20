"""
Build the sample clip used by the video-analysis panel.

The point of the clip is to reproduce, on footage a real detector genuinely
responds to, the failure the whole product is designed around: a camera that
counts people perfectly in clear air, and then stops being able to see them at
all as the room fills with smoke.

It is assembled from the Ultralytics sample images (which contain real people,
so the COCO detector fires on them properly) with a grey haze ramped over the
run. Nothing about the detections is scripted — YOLO is doing the work, and
the clip simply takes its visibility away.

    python scripts/make_sample_clip.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "static" / "sample-smoke.mp4"
W, H, FPS, SECONDS = 960, 540, 12, 14

ASSET_DIRS = [
    BASE / ".venv/lib/python3.11/site-packages/ultralytics/assets",
    BASE / ".venv/lib/python3.12/site-packages/ultralytics/assets",
]


def find_assets() -> list[Path]:
    for d in ASSET_DIRS:
        if d.exists():
            found = sorted(d.glob("*.jpg"))
            if found:
                return found
    try:
        import ultralytics
        d = Path(ultralytics.__file__).parent / "assets"
        return sorted(d.glob("*.jpg"))
    except Exception:  # noqa: BLE001
        return []


def fit(img, w: int, h: int):
    """Cover-fit, so the frame is filled without distorting anyone."""
    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    resized = cv2.resize(img, (int(iw * scale) + 1, int(ih * scale) + 1))
    y = (resized.shape[0] - h) // 2
    x = (resized.shape[1] - w) // 2
    return resized[y:y + h, x:x + w]


def main() -> int:
    assets = find_assets()
    if not assets:
        print("No Ultralytics sample assets found; install ultralytics first.")
        return 1

    frames_n = FPS * SECONDS
    plates = [fit(cv2.imread(str(p)), W, H) for p in assets[:2]]
    plates = [p for p in plates if p is not None]
    if not plates:
        print("Could not read the sample images.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"avc1"), FPS, (W, H))
    if not writer.isOpened():
        writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not writer.isOpened():
        print("No usable MP4 encoder available in this OpenCV build.")
        return 1

    smoke = np.full((H, W, 3), 168, dtype=np.uint8)

    for i in range(frames_n):
        t = i / max(frames_n - 1, 1)
        plate = plates[0] if t < 0.5 or len(plates) == 1 else plates[1]
        frame = plate.copy()

        # A slow drift, so the clip reads as footage rather than a still.
        shift = int(10 * np.sin(i * 0.07))
        frame = np.roll(frame, shift, axis=1)

        # Haze holds off for the first fifth, then ramps hard.
        alpha = 0.0 if t < 0.2 else min((t - 0.2) / 0.62, 1.0) * 0.93
        if alpha > 0:
            # Drifting turbulence, so the obscuration is not a flat wash.
            band = np.clip(
                np.linspace(0.75, 1.25, H).reshape(-1, 1) + 0.1 * np.sin(i * 0.2), 0, 2
            )
            local = np.clip(alpha * band, 0, 1).astype(np.float32)[:, :, None]
            frame = (frame * (1 - local) + smoke * local).astype(np.uint8)

        writer.write(frame)

    writer.release()
    size = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.relative_to(BASE)} — {frames_n} frames, {SECONDS}s, {size:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
