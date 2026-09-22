"""Опциональные внешние YOLO-модели: brand-eye, retail product."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

from .engine import Box

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
MANIFEST = MODELS / "manifest.json"

# Маппинг имён классов внешних моделей → наши aliases (частичное пересечение)
BRAND_EYE_TO_CLUB = {
    "coca-cola": "coca",
    "cocacola": "coca",
    "coca_cola": "coca",
    "cola": "coca",
    "fanta": "fanta",
    "sprite": "sprite",
    "pepsi": "pepsi",  # forbidden
    "redbull": "red bull",
    "red_bull": "red bull",
    "monster": "monster",
    "skittles": "skittles",
    "mms": "m&m",
    "m&m": "m&m",
    "m&ms": "m&m",
    "snickers": "snickers",
    "mars": "mars",
    "twix": "twix",
    "bounty": "bounty",
    "kitkat": "kitkat",
    "schweppes": "schweppes",
}


class ExtraDetectors:
    """Ленивая загрузка доп. моделей. Если файла нет — тихо пропускаем."""

    def __init__(self):
        self._brand = None
        self._product = None
        self._brand_failed = False
        self._product_failed = False
        self.enabled_brand = True
        self.enabled_product = True

    def _resolve(self, key: str, default_name: str) -> Optional[Path]:
        if MANIFEST.exists():
            try:
                data = json.loads(MANIFEST.read_text(encoding="utf-8"))
                rel = (data.get("models") or {}).get(key, {}).get("path")
                if rel:
                    p = ROOT / rel
                    if p.exists():
                        return p
            except Exception:
                pass
        p = MODELS / default_name
        return p if p.exists() else None

    def _load_yolo(self, path: Path):
        from ultralytics import YOLO

        return YOLO(str(path))

    def detect_brands(self, frame: np.ndarray, conf: float = 0.35) -> List[Box]:
        if not self.enabled_brand or self._brand_failed:
            return []
        if self._brand is None:
            path = self._resolve("brand_eye", "brandeye.pt")
            if path is None:
                self._brand_failed = True
                return []
            try:
                logger.info("Load brand-eye: %s", path)
                self._brand = self._load_yolo(path)
            except Exception as exc:
                logger.warning("brand-eye unavailable: %s", exc)
                self._brand_failed = True
                return []
        try:
            results = self._brand.predict(frame, conf=conf, verbose=False)
        except Exception:
            return []
        return self._to_boxes(results)

    def detect_products(self, frame: np.ndarray, conf: float = 0.35) -> List[Box]:
        if not self.enabled_product or self._product_failed:
            return []
        if self._product is None:
            path = self._resolve("retail_product", "retail_product.pt")
            if path is None:
                self._product_failed = True
                return []
            try:
                logger.info("Load retail product model: %s", path)
                self._product = self._load_yolo(path)
            except Exception as exc:
                logger.warning("retail product model unavailable: %s", exc)
                self._product_failed = True
                return []
        try:
            results = self._product.predict(frame, conf=conf, verbose=False)
        except Exception:
            return []
        boxes = self._to_boxes(results)
        # единый класс для held-product политики
        for b in boxes:
            b.label = "product"
        return boxes

    @staticmethod
    def _to_boxes(results) -> List[Box]:
        out: List[Box] = []
        if not results or results[0].boxes is None:
            return out
        names = results[0].names or {}
        for box in results[0].boxes:
            cls_id = int(box.cls[0].cpu().numpy())
            conf = float(box.conf[0].cpu().numpy())
            xyxy = box.xyxy[0].cpu().numpy()
            raw = str(names.get(cls_id, str(cls_id)))
            mapped = BRAND_EYE_TO_CLUB.get(raw.lower().replace(" ", "_"), raw)
            out.append(
                Box(
                    cls_id=cls_id,
                    label=str(mapped),
                    conf=conf,
                    x1=int(xyxy[0]),
                    y1=int(xyxy[1]),
                    x2=int(xyxy[2]),
                    y2=int(xyxy[3]),
                )
            )
        return out

    def map_brand_label(self, label: str) -> str:
        key = label.lower().replace(" ", "_")
        return BRAND_EYE_TO_CLUB.get(key, label)
