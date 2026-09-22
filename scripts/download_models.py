#!/usr/bin/env python3
"""Скачивает вспомогательные YOLO-модели для детекции брендов/товаров."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
MODELS.mkdir(parents=True, exist_ok=True)

DOWNLOADS = [
    {
        "id": "brand_eye",
        "repo_id": "haydarkadioglu/brand-eye",
        "filename": "brandeye.pt",
        "local_name": "brandeye.pt",
        "note": "YOLO brand/logo detector (общие бренды, не весь наш ассортимент)",
    },
    {
        "id": "retail_product",
        "repo_id": "prince4332/yolov26-product-detection",
        "filename": "best.pt",
        "local_name": "retail_product.pt",
        "note": "YOLO single-class retail product detector (коробки/упаковки)",
    },
]


def main() -> int:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Установите huggingface_hub: pip install huggingface_hub")
        return 1

    manifest = {"models": {}}
    for item in DOWNLOADS:
        print(f"↓ {item['repo_id']} / {item['filename']} ...")
        try:
            path = hf_hub_download(
                repo_id=item["repo_id"],
                filename=item["filename"],
                local_dir=str(MODELS),
                local_dir_use_symlinks=False,
            )
            src = Path(path)
            dst = MODELS / item["local_name"]
            if src.resolve() != dst.resolve():
                if dst.exists():
                    dst.unlink()
                # hf may place file in nested folder — copy/rename
                if src.exists() and src.name != item["local_name"]:
                    dst.write_bytes(src.read_bytes())
                elif not dst.exists() and src.exists():
                    src.rename(dst)
            # also search nested
            if not dst.exists():
                found = list(MODELS.rglob(item["filename"]))
                if found:
                    dst.write_bytes(found[0].read_bytes())
            ok = dst.exists()
            print(f"  {'OK' if ok else 'FAIL'}: {dst}")
            manifest["models"][item["id"]] = {
                "path": str(dst.relative_to(ROOT)) if ok else "",
                "repo": item["repo_id"],
                "note": item["note"],
                "ok": ok,
            }
        except Exception as exc:
            print(f"  ERROR: {exc}")
            manifest["models"][item["id"]] = {
                "path": "",
                "repo": item["repo_id"],
                "note": item["note"],
                "ok": False,
                "error": str(exc),
            }

    # Ensure base yolov8n exists (ultralytics auto-download if missing)
    yolo = ROOT / "yolov8n.pt"
    manifest["models"]["yolov8n"] = {
        "path": "yolov8n.pt" if yolo.exists() else "",
        "note": "Базовая COCO-модель (люди, bottle, cup…)",
        "ok": yolo.exists(),
    }

    out = MODELS / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nМанифест: {out}")
    print(
        "\nВажно: готовой модели именно под TUC/Хрустим/Бульба/Gorilla нет. "
        "Brand-Eye и retail product — вспомогательные; основной путь — OCR + каталог эталонов."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
