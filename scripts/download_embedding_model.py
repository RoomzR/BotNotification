#!/usr/bin/env python3
"""Скачать/прогреть embedding-модель (OpenCLIP или torchvision MobileNet)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    print("Прогрев YOLOv8s…")
    try:
        from ultralytics import YOLO

        YOLO("yolov8s.pt")
        print("OK: yolov8s.pt")
    except Exception as exc:
        print(f"WARN yolov8s: {exc}")

    print("Пробуем OpenCLIP ViT-B-32…")
    try:
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
        print("OK: OpenCLIP ViT-B-32 загружен")
        del model, preprocess
        return 0
    except Exception as exc:
        print(f"OpenCLIP недоступен: {exc}")
        print("Ставим fallback torchvision MobileNetV3-Small…")

    try:
        from torchvision import models

        _ = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
        print("OK: MobileNetV3-Small weights готовы")
        print("Подсказка: pip install open-clip-torch  — для более точного matching")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
