"""Сопоставление кропа с эталонными фото каталога (ORB + гистограмма)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class TemplateHit:
    sku: str
    score: float
    method: str


class TemplateMatcher:
    def __init__(self, min_orb_matches: int = 12, hist_threshold: float = 0.72):
        self.min_orb_matches = min_orb_matches
        self.hist_threshold = hist_threshold
        self._orb = cv2.ORB_create(500)
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        self._cache: dict[str, list[tuple[str, np.ndarray, Optional[cv2.KeyPoint], Optional[np.ndarray], np.ndarray]]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    def _load_sku_refs(self, sku: str, paths: List[Path]) -> None:
        packed = []
        for p in paths:
            img = cv2.imread(str(p))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kp, des = self._orb.detectAndCompute(gray, None)
            hist = self._hist(img)
            packed.append((str(p), img, kp, des, hist))
        self._cache[sku] = packed

    def _hist(self, bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        return hist

    def ensure_loaded(self, refs_by_sku: dict[str, list[str]]) -> None:
        for sku, rels in refs_by_sku.items():
            paths = []
            for rel in rels:
                p = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
                if p.exists():
                    paths.append(p)
            if paths:
                self._load_sku_refs(sku, paths)

    def match(self, crop_bgr: np.ndarray, refs_by_sku: dict[str, list[str]]) -> Optional[TemplateHit]:
        hits = self.match_all(crop_bgr, refs_by_sku)
        return hits[0] if hits else None

    def match_all(self, crop_bgr: np.ndarray, refs_by_sku: dict[str, list[str]]) -> List[TemplateHit]:
        """Лучший hist и лучший ORB отдельно (чтобы hist не затенял ORB)."""
        if crop_bgr is None or crop_bgr.size == 0:
            return []
        if not self._cache:
            self.ensure_loaded(refs_by_sku)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        kp, des = self._orb.detectAndCompute(gray, None)
        hist = self._hist(crop_bgr)
        best_hist: Optional[TemplateHit] = None
        best_orb: Optional[TemplateHit] = None

        for sku, packed in self._cache.items():
            for _path, _img, _kp, ref_des, ref_hist in packed:
                corr = float(cv2.compareHist(hist, ref_hist, cv2.HISTCMP_CORREL))
                if corr >= self.hist_threshold:
                    hit = TemplateHit(sku, corr, "hist")
                    if best_hist is None or hit.score > best_hist.score:
                        best_hist = hit
                if des is None or ref_des is None or len(des) < 2 or len(ref_des) < 2:
                    continue
                try:
                    matches = self._bf.knnMatch(des, ref_des, k=2)
                except Exception:
                    continue
                good = 0
                for pair in matches:
                    if len(pair) < 2:
                        continue
                    m, n = pair
                    if m.distance < 0.75 * n.distance:
                        good += 1
                if good >= self.min_orb_matches:
                    score = min(1.0, good / 40.0)
                    hit = TemplateHit(sku, score, "orb")
                    if best_orb is None or hit.score > best_orb.score:
                        best_orb = hit

        # ORB сначала (надёжнее), потом hist
        out: List[TemplateHit] = []
        if best_orb is not None:
            out.append(best_orb)
        if best_hist is not None:
            out.append(best_hist)
        return out
