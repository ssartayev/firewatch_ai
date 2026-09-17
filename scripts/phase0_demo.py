"""
Фаза 0 — минимальное доказательство, что детект работает.

Прогоняет детекторы (из config.yaml) по видеофайлу, рисует боксы и сохраняет
размеченный ролик в data/phase0_out.mp4. Никаких зон/правил/алертов — только
инференс + отрисовка.

Запуск:  python scripts/phase0_demo.py [input_video] [output_video]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

# делаем пакет app импортируемым при запуске из папки firewatch/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config              # noqa: E402
from app.detectors import DetectorSet           # noqa: E402
from app.pipeline import _COLORS                # noqa: E402


def main() -> int:
    cfg = load_config()
    src = sys.argv[1] if len(sys.argv) > 1 else str(cfg.path(cfg.video_source))
    dst = sys.argv[2] if len(sys.argv) > 2 else str(cfg.path("data/phase0_out.mp4"))

    if not Path(src).exists():
        print(f"Нет входного видео: {src}. Сначала: python scripts/make_demo_video.py")
        return 1

    dets = DetectorSet(cfg.models, cfg.detection.conf_threshold)
    print("Детекторы:", dets.status())

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"Не открыть видео: {src}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    frames, total_det = 0, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames += 1
        detections = dets.run(frame)
        total_det += len(detections)
        for d in detections:
            x1, y1, x2, y2 = d.bbox
            color = _COLORS.get(d.label, (200, 200, 200))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{d.label} {d.confidence:.2f}", (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        writer.write(frame)

    cap.release()
    writer.release()
    print(f"Кадров: {frames}, всего детекций: {total_det}")
    print(f"Размеченное видео: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
