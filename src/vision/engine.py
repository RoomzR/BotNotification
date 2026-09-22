"""Общий YOLO-движок для платформы."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# COCO ids
PERSON = 0
OWN_PRODUCT_CLASSES = {
    39: "bottle",
    40: "wine_glass",
    41: "cup",
    42: "fork",
    43: "knife",
    44: "spoon",
    45: "bowl",
    46: "banana",
    47: "apple",
    48: "sandwich",
    49: "orange",
    50: "broccoli",
    51: "carrot",
    52: "hot_dog",
    53: "pizza",
    54: "donut",
    55: "cake",
}
BACKPACK = 24
HANDBAG = 26
SUITCASE = 28
CHAIR = 56


@dataclass
class Box:
    cls_id: int
    label: str
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    def iou(self, other: "Box") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        union = self.area + other.area - inter
        return inter / union if union else 0.0

    def near(self, other: "Box", pad: float = 0.35) -> bool:
        # расширяем person-бокс и смотрим пересечение с предметом
        w = self.x2 - self.x1
        h = self.y2 - self.y1
        ex1 = self.x1 - int(w * pad)
        ey1 = self.y1 - int(h * pad)
        ex2 = self.x2 + int(w * pad)
        ey2 = self.y2 + int(h * pad)
        return not (other.x2 < ex1 or other.x1 > ex2 or other.y2 < ey1 or other.y1 > ey2)


class YoloEngine:
    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = 0.4):
        logger.info("YOLO load %s", model_name)
        self.model = YOLO(model_name)
        self.confidence = confidence

    def detect(self, frame: np.ndarray, classes: Optional[Iterable[int]] = None) -> List[Box]:
        kwargs = {"conf": self.confidence, "verbose": False}
        if classes is not None:
            kwargs["classes"] = list(classes)
        results = self.model.predict(frame, **kwargs)
        out: List[Box] = []
        if not results or results[0].boxes is None:
            return out
        names = results[0].names
        for box in results[0].boxes:
            cls_id = int(box.cls[0].cpu().numpy())
            conf = float(box.conf[0].cpu().numpy())
            xyxy = box.xyxy[0].cpu().numpy()
            label = OWN_PRODUCT_CLASSES.get(cls_id) or names.get(cls_id, str(cls_id))
            out.append(
                Box(
                    cls_id=cls_id,
                    label=str(label),
                    conf=conf,
                    x1=int(xyxy[0]),
                    y1=int(xyxy[1]),
                    x2=int(xyxy[2]),
                    y2=int(xyxy[3]),
                )
            )
        return out
