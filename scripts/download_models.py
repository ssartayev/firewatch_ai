"""
Загрузка готовых весов в models/.

1) person — стандартный COCO-детектор yolov8n.pt (ultralytics скачает сам).
2) fire/smoke — пробуем готовые веса с Hugging Face (репозиторий из брифа
   TommyNgx/YOLOv10-Fire-and-Smoke-Detection и пара запасных вариантов). Если
   ничего не скачалось — не страшно: система при запуске включит MOCK-детектор
   огня для демо, а вы позже пропишете реальные веса в config.yaml.
3) extinguisher — единого гарантированного публичного файла нет, поэтому
   пропускаем; если у вас есть свои веса, положите их в models/ext.pt.

Запуск:  python scripts/download_models.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Кандидаты для fire/smoke: (repo_id, filename | None). None = взять любой *.pt.
# Первый — проверенный публичный репо с чистыми классами {0: fire, 1: smoke},
# что совпадает с маппингом в config.yaml. Дальше — запасные.
FIRE_CANDIDATES = [
    ("mfranzon/fire-smoke-yolov8", "fire_smoke_yolov8.pt"),          # {0:fire, 1:smoke} — проверено
    ("rabahdev/fire-smoke-yolov8n", "best.pt"),
    ("TommyNgx/YOLOv10-Fire-and-Smoke-Detection", None),             # gated — нужен доступ/логин
]


def download_person() -> bool:
    """Скачать yolov8n.pt в models/ (через ultralytics)."""
    target = MODELS_DIR / "yolov8n.pt"
    if target.exists():
        print(f"[person] уже есть: {target}")
        return True
    print("[person] скачиваю yolov8n.pt (COCO)…")
    cwd = os.getcwd()
    try:
        os.chdir(MODELS_DIR)          # ultralytics кладёт файл в текущую папку
        from ultralytics import YOLO
        YOLO("yolov8n.pt")            # триггерит загрузку ассета
    except Exception as e:            # noqa: BLE001
        print(f"[person] ошибка: {e}")
        return False
    finally:
        os.chdir(cwd)
    ok = target.exists()
    print(f"[person] {'готово: ' + str(target) if ok else 'не удалось'}")
    return ok


def download_fire() -> bool:
    """Попробовать скачать веса fire/smoke с Hugging Face."""
    target = MODELS_DIR / "fire.pt"
    if target.exists():
        print(f"[fire] уже есть: {target}")
        return True
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
    except Exception as e:            # noqa: BLE001
        print(f"[fire] huggingface_hub недоступен: {e}")
        return False

    for repo_id, fname in FIRE_CANDIDATES:
        try:
            print(f"[fire] пробую {repo_id}…")
            candidates = [fname] if fname else [
                f for f in list_repo_files(repo_id) if f.lower().endswith(".pt")
            ]
            if not candidates:
                print(f"[fire]   в {repo_id} нет .pt файлов")
                continue
            path = hf_hub_download(repo_id=repo_id, filename=candidates[0])
            shutil.copy(path, target)
            print(f"[fire] готово: {target} (из {repo_id}/{candidates[0]})")
            return True
        except Exception as e:        # noqa: BLE001
            print(f"[fire]   не вышло: {e}")
            continue

    print("[fire] реальные веса не скачаны — при запуске включится MOCK-детектор.")
    print("       Позже пропишите свои веса в config.yaml -> models.fire.weights.")
    return False


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ok_person = download_person()
    ok_fire = download_fire()

    ext = MODELS_DIR / "ext.pt"
    if ext.exists():
        print(f"[extinguisher] найдены веса: {ext}")
    else:
        print("[extinguisher] весов нет (опционально). Проверка огнетушителя будет "
              "помечена как «не проверяется». Положите модель в models/ext.pt при наличии.")

    print("\nИтог: person=%s, fire=%s" % ("OK" if ok_person else "нет",
                                          "OK" if ok_fire else "MOCK"))
    # Ненулевой код только если совсем ничего не готово
    return 0 if (ok_person or ok_fire) else 1


if __name__ == "__main__":
    sys.exit(main())
