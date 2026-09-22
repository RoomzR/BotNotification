"""Детекция людей в зоне ROI через YOLOv8."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

from .config import Roi

logger = logging.getLogger(__name__)

# COCO class id для person
PERSON_CLASS_ID = 0


@dataclass
class Detection:
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


class PersonDetector:
    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = 0.45):
        logger.info("Загрузка модели %s ...", model_name)
        self.model = YOLO(model_name)
        self.confidence = confidence

    def detect(self, frame: np.ndarray, roi: Roi) -> List[Detection]:
        h, w = frame.shape[:2]
        rx1, ry1, rx2, ry2 = roi.to_pixels(w, h)
        if rx2 <= rx1 or ry2 <= ry1:
            return []

        crop = frame[ry1:ry2, rx1:rx2]
        results = self.model.predict(
            crop,
            conf=self.confidence,
            classes=[PERSON_CLASS_ID],
            verbose=False,
        )

        detections: List[Detection] = []
        if not results:
            return detections

        boxes = results[0].boxes
        if boxes is None:
            return detections

        for box in boxes:
            xyxy = box.xyxy[0].cpu().numpy()
            conf = float(box.conf[0].cpu().numpy())
            # Координаты относительно полного кадра
            detections.append(
                Detection(
                    x1=int(xyxy[0]) + rx1,
                    y1=int(xyxy[1]) + ry1,
                    x2=int(xyxy[2]) + rx1,
                    y2=int(xyxy[3]) + ry1,
                    conf=conf,
                )
            )
        return detections


def draw_overlay(
    frame: np.ndarray,
    roi: Roi,
    detections: List[Detection],
    status: str,
) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    rx1, ry1, rx2, ry2 = roi.to_pixels(w, h)

    # Полупрозрачная маска вне ROI
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    overlay[ry1:ry2, rx1:rx2] = out[ry1:ry2, rx1:rx2]
    out = cv2.addWeighted(overlay, 0.45, out, 0.55, 0)

    color = (0, 200, 0) if detections else (0, 180, 255)
    cv2.rectangle(out, (rx1, ry1), (rx2, ry2), color, 2)
    cv2.putText(
        out,
        "Admin desk ROI",
        (rx1 + 6, max(20, ry1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
        cv2.LINE_AA,
    )

    for det in detections:
        cv2.rectangle(out, (det.x1, det.y1), (det.x2, det.y2), (0, 255, 0), 2)
        cv2.putText(
            out,
            f"person {det.conf:.2f}",
            (det.x1, max(20, det.y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    cv2.putText(
        out,
        status,
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return out
