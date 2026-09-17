"""
Download pretrained weights into models/.

1) person — the standard COCO detector yolov8n.pt (ultralytics fetches it).
2) fire/smoke — try pretrained weights from Hugging Face (the repo named in the
   brief, TommyNgx/YOLOv10-Fire-and-Smoke-Detection, plus fallbacks). If none
   download, that is fine: the app enables the MOCK fire detector at startup
   for the demo, and you can point config.yaml at real weights later.
3) extinguisher — there is no single reliable public file, so this is skipped;
   if you have your own weights, place them at models/ext.pt.

Run:  python scripts/download_models.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# fire/smoke candidates: (repo_id, filename | None). None = take any *.pt.
# The first is a verified public repo with clean classes {0: fire, 1: smoke},
# matching the mapping in config.yaml. The rest are fallbacks.
FIRE_CANDIDATES = [
    ("mfranzon/fire-smoke-yolov8", "fire_smoke_yolov8.pt"),          # {0:fire, 1:smoke} — verified
    ("rabahdev/fire-smoke-yolov8n", "best.pt"),
    ("TommyNgx/YOLOv10-Fire-and-Smoke-Detection", None),             # gated — requires granted access/login
]


def download_person() -> bool:
    """Download yolov8n.pt into models/ (via ultralytics)."""
    target = MODELS_DIR / "yolov8n.pt"
    if target.exists():
        print(f"[person] already present: {target}")
        return True
    print("[person] downloading yolov8n.pt (COCO)…")
    cwd = os.getcwd()
    try:
        os.chdir(MODELS_DIR)          # ultralytics writes the file into the current folder
        from ultralytics import YOLO
        YOLO("yolov8n.pt")            # triggers the asset download
    except Exception as e:            # noqa: BLE001
        print(f"[person] error: {e}")
        return False
    finally:
        os.chdir(cwd)
    ok = target.exists()
    print(f"[person] {'done: ' + str(target) if ok else 'failed'}")
    return ok


def download_fire() -> bool:
    """Try to download fire/smoke weights from Hugging Face."""
    target = MODELS_DIR / "fire.pt"
    if target.exists():
        print(f"[fire] already present: {target}")
        return True
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
    except Exception as e:            # noqa: BLE001
        print(f"[fire] huggingface_hub unavailable: {e}")
        return False

    for repo_id, fname in FIRE_CANDIDATES:
        try:
            print(f"[fire] trying {repo_id}…")
            candidates = [fname] if fname else [
                f for f in list_repo_files(repo_id) if f.lower().endswith(".pt")
            ]
            if not candidates:
                print(f"[fire]   no .pt files in {repo_id}")
                continue
            path = hf_hub_download(repo_id=repo_id, filename=candidates[0])
            shutil.copy(path, target)
            print(f"[fire] done: {target} (from {repo_id}/{candidates[0]})")
            return True
        except Exception as e:        # noqa: BLE001
            print(f"[fire]   failed: {e}")
            continue

    print("[fire] real weights not downloaded — the MOCK detector will be used at startup.")
    print("       Set your own weights later in config.yaml -> models.fire.weights.")
    return False


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ok_person = download_person()
    ok_fire = download_fire()

    ext = MODELS_DIR / "ext.pt"
    if ext.exists():
        print(f"[extinguisher] weights found: {ext}")
    else:
        print("[extinguisher] no weights (optional). The extinguisher check will be "
              "reported as 'not checked'. Place a model at models/ext.pt if you have one.")

    print("\nResult: person=%s, fire=%s" % ("OK" if ok_person else "no",
                                          "OK" if ok_fire else "MOCK"))
    # non-zero exit only if nothing at all is ready
    return 0 if (ok_person or ok_fire) else 1


if __name__ == "__main__":
    sys.exit(main())
