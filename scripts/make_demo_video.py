"""
Генератор синтетического демо-ролика «огневые работы».

Нужен, чтобы систему можно было запустить и показать без реальной камеры и без
готового видео пожара. Сцена: тёмный цех, «сварщик» (прямоугольник) искрит внизу
слева, огнетушитель стоит в углу, а с ~3-й секунды в центре кадра (внутри зоны A)
разгорается мерцающий огонь.

Запуск:  python scripts/make_demo_video.py [output.mp4]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 960, 540, 25


def _draw_flame(img: np.ndarray, cx: int, cy: int, scale: float, rng: np.random.Generator) -> None:
    """Нарисовать мерцающий «огонь» (набор оранжево-красных эллипсов + свечение)."""
    overlay = img.copy()
    n = int(18 * scale)
    for _ in range(max(4, n)):
        dx = int(rng.normal(0, 22 * scale))
        dy = int(abs(rng.normal(0, 30 * scale)))
        rad = int(rng.integers(8, 26) * scale)
        # цвет от жёлтого к красному (BGR)
        b = int(rng.integers(0, 40))
        g = int(rng.integers(90, 200))
        r = 255
        cv2.circle(overlay, (cx + dx, cy - dy), rad, (b, g, r), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    # ядро пламени поярче
    cv2.circle(img, (cx, cy), int(18 * scale), (0, 200, 255), -1)


def generate(output: str, seconds: int = 12) -> str:
    """Создать демо-ролик по пути output. Возвращает путь."""
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV не смог открыть VideoWriter (кодек mp4v)")

    total = seconds * FPS
    fire_start = 3 * FPS  # огонь появляется на 3-й секунде

    for i in range(total):
        # фон-градиент (тёмный цех)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[:] = (35, 33, 30)
        cv2.rectangle(frame, (0, int(H * 0.75)), (W, H), (55, 52, 48), -1)  # пол

        # «сварщик» + искры внизу слева
        cv2.rectangle(frame, (90, 300), (170, 470), (70, 70, 70), -1)      # корпус
        cv2.circle(frame, (130, 285), 26, (60, 60, 60), -1)                # голова
        for _ in range(12):
            sx = 175 + int(rng.integers(0, 60))
            sy = 400 + int(rng.integers(-30, 30))
            cv2.circle(frame, (sx, sy), int(rng.integers(1, 3)),
                       (0, int(rng.integers(180, 255)), 255), -1)

        # огнетушитель в правом нижнем углу (красный «баллон»)
        cv2.rectangle(frame, (860, 360), (900, 470), (30, 30, 200), -1)
        cv2.rectangle(frame, (872, 340), (888, 360), (40, 40, 40), -1)

        # разгорающийся огонь в центре (внутри зоны A)
        if i >= fire_start:
            grow = min(1.6, 0.5 + (i - fire_start) / (total - fire_start) * 1.3)
            _draw_flame(frame, int(W * 0.5), int(H * 0.6), grow, rng)

        # таймкод
        cv2.putText(frame, f"demo t={i / FPS:5.1f}s", (W - 210, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        writer.write(frame)

    writer.release()
    return str(out_path)


if __name__ == "__main__":
    default = Path(__file__).resolve().parent.parent / "data" / "demo_fire.mp4"
    out = sys.argv[1] if len(sys.argv) > 1 else str(default)
    path = generate(out)
    print(f"Демо-видео создано: {path}")
