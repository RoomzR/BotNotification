"""Анализаторы кадров: своя продукция, чистота, холодильник, инциденты, персонал."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml

from .brand_models import ExtraDetectors
from .engine import (
    BACKPACK,
    HANDBAG,
    OWN_PRODUCT_CLASSES,
    PERSON,
    SUITCASE,
    Box,
    YoloEngine,
)
from .menu_matcher import MenuMatcher

ROOT = __file__
try:
    from pathlib import Path

    _PLATFORM = Path(__file__).resolve().parents[2] / "platform.yaml"
except Exception:
    _PLATFORM = None


@dataclass
class Finding:
    kind: str
    severity: str
    title: str
    detail: str
    boxes: List[Box]


@dataclass
class _Track:
    box: Box
    first_ts: float
    last_ts: float
    hits: int = 1


class FrameAnalyzer:
    def __init__(self, engine: YoloEngine):
        self.engine = engine
        self.menu = MenuMatcher()
        self.extra = ExtraDetectors()
        self._tracks: Dict[str, List[_Track]] = {}  # camera_name -> tracks
        self._person_pad = 0.28
        self._min_catalog_coverage = 0.9
        self._catalog_weak = False
        self._catalog_note = ""
        self._load_own_cfg()

    def _load_own_cfg(self) -> None:
        try:
            if _PLATFORM and _PLATFORM.exists():
                raw = yaml.safe_load(_PLATFORM.read_text(encoding="utf-8")) or {}
                own = (raw.get("vision") or {}).get("own_products") or {}
                self._person_pad = float(own.get("person_pad", 0.28))
                self._min_catalog_coverage = float(own.get("min_catalog_coverage", 0.9))
        except Exception:
            self._person_pad = 0.28
            self._min_catalog_coverage = 0.9
        self._refresh_catalog_gate()

    def _refresh_catalog_gate(self) -> None:
        try:
            from src.catalog.service import CatalogService

            cov = CatalogService().coverage_stats()
            self._catalog_weak = cov["ratio"] < self._min_catalog_coverage
            if self._catalog_weak:
                self._catalog_note = (
                    f"каталог неполный {cov['complete']}/{cov['total']} "
                    f"({int(100 * cov['ratio'])}% < {int(100 * self._min_catalog_coverage)}%)"
                )
            else:
                self._catalog_note = ""
        except Exception:
            self._catalog_weak = True
            self._catalog_note = "каталог недоступен"

    def reload_menu(self) -> None:
        self.menu.reload()
        self._load_own_cfg()

    def _iou(self, a: Box, b: Box) -> float:
        return a.iou(b)

    def _update_tracks(self, camera_name: str, own_boxes: List[Box], now: float) -> List[Box]:
        """Простое IoU-tracking, чтобы confirm_seconds переживал round-robin."""
        prev = self._tracks.get(camera_name, [])
        updated: List[_Track] = []
        used = set()
        for box in own_boxes:
            best_i, best_iou = -1, 0.0
            for i, tr in enumerate(prev):
                if i in used:
                    continue
                iou = self._iou(box, tr.box)
                if iou > best_iou:
                    best_iou, best_i = iou, i
            if best_i >= 0 and best_iou >= 0.25:
                tr = prev[best_i]
                used.add(best_i)
                tr.box = box
                tr.last_ts = now
                tr.hits += 1
                updated.append(tr)
            else:
                updated.append(_Track(box=box, first_ts=now, last_ts=now, hits=1))
        # drop stale (>90s)
        updated = [t for t in updated if now - t.last_ts < 90]
        self._tracks[camera_name] = updated
        return [t.box for t in updated]

    def analyze_own_products(self, frame: np.ndarray, camera_name: str) -> List[Finding]:
        """Своё vs меню клуба: held product без club-match → алерт."""
        classes = [PERSON, *OWN_PRODUCT_CLASSES.keys()]
        boxes = self.engine.detect(frame, classes=classes)
        people = [b for b in boxes if b.cls_id == PERSON]
        items = [b for b in boxes if b.cls_id in OWN_PRODUCT_CLASSES]

        brand_boxes = self.extra.detect_brands(frame)
        product_boxes = self.extra.detect_products(frame)
        for pb in product_boxes:
            items.append(Box(pb.cls_id, "product", pb.conf, pb.x1, pb.y1, pb.x2, pb.y2))

        findings: List[Finding] = []
        h, w = frame.shape[:2]
        pad = self._person_pad
        now = time.time()

        def nearest_brand_hint(it: Box) -> Optional[str]:
            best = None
            best_iou = 0.0
            for bb in brand_boxes:
                iou = it.iou(bb)
                if iou > best_iou:
                    best_iou = iou
                    best = bb.label
            if best_iou >= 0.15:
                return best
            return None

        for person in people:
            held = [it for it in items if person.near(it, pad=pad)]
            for bb in brand_boxes:
                if person.near(bb, pad=pad):
                    held.append(bb)
            if not held:
                continue

            # дедуп пересекающихся боксов
            held_dedup: List[Box] = []
            for it in sorted(held, key=lambda b: -b.conf):
                if any(it.iou(x) > 0.55 for x in held_dedup):
                    continue
                held_dedup.append(it)
            held = held_dedup

            own_boxes: List[Box] = []
            club_boxes: List[Box] = []
            reasons: List[str] = []
            certainties: List[str] = []
            brand_ids = {id(b) for b in brand_boxes}

            for it in held:
                x1 = max(0, it.x1 - 4)
                y1 = max(0, it.y1 - 4)
                x2 = min(w, it.x2 + 4)
                y2 = min(h, it.y2 + 4)
                crop = frame[y1:y2, x1:x2]
                hint = nearest_brand_hint(it)
                if id(it) in brand_ids:
                    hint = it.label
                item_wh = (max(1, it.x2 - it.x1), max(1, it.y2 - it.y1))
                person_wh = (max(1, person.x2 - person.x1), max(1, person.y2 - person.y1))
                verdict = self.menu.classify_item(
                    it.label,
                    crop,
                    hinted_brand=hint,
                    item_wh=item_wh,
                    person_wh=person_wh,
                )

                certainty = getattr(verdict, "certainty", "confirmed") or "confirmed"
                if verdict.status == "own":
                    tag = "СВОЁ" if certainty == "confirmed" else "??"
                elif verdict.status == "club":
                    tag = "КЛУБ"
                else:
                    tag = "?"
                tagged = Box(
                    cls_id=it.cls_id,
                    label=f"{tag}:{verdict.brand}",
                    conf=it.conf,
                    x1=it.x1,
                    y1=it.y1,
                    x2=it.x2,
                    y2=it.y2,
                )
                if verdict.status == "own":
                    own_boxes.append(tagged)
                    reasons.append(verdict.reason)
                    certainties.append(certainty)
                elif verdict.status == "club":
                    club_boxes.append(tagged)

            if not own_boxes:
                continue

            self._update_tracks(camera_name, own_boxes, now)
            confirmed = any(c == "confirmed" for c in certainties)
            # неполный каталог: не понижаем hard evidence (forbidden OCR / confirmed brand)
            if self._catalog_weak and confirmed:
                soft_only = all(
                    ("подозрит" in (r or "").lower())
                    or ("embed" in (r or "").lower())
                    or ("не похож" in (r or "").lower())
                    or ("без совпадения" in (r or "").lower())
                    or ("вероятно" in (r or "").lower())
                    for r in reasons
                )
                if soft_only or all(c == "suspicious" for c in certainties):
                    confirmed = False
                    certainties = ["suspicious"] * len(certainties)
                    why_extra = f" [{self._catalog_note}]"
                else:
                    why_extra = f" [{self._catalog_note}; hard evidence сохранён]"
            else:
                why_extra = f" [{self._catalog_note}]" if self._catalog_weak else ""
            person_box = Box(
                PERSON,
                "гость",
                person.conf,
                person.x1,
                person.y1,
                person.x2,
                person.y2,
            )
            why = "; ".join(reasons[:4]) + why_extra
            if confirmed:
                findings.append(
                    Finding(
                        kind="own_products",
                        severity="high",
                        title="Человек со своим (не из меню клуба)",
                        detail=f"{camera_name}: {why}. Разрешено только меню бара CyberX.",
                        boxes=[person_box, *own_boxes, *club_boxes],
                    )
                )
            else:
                title = "Подозрительно: возможно своё"
                if self._catalog_weak:
                    title = "Подозрительно (каталог неполный)"
                findings.append(
                    Finding(
                        kind="own_products",
                        severity="medium",
                        title=title,
                        detail=(
                            f"{camera_name}: {why}. "
                            f"Мягкий режим — нужна визуальная проверка сотрудника."
                        ),
                        boxes=[person_box, *own_boxes, *club_boxes],
                    )
                )
        return findings

    def analyze_cleanliness(self, frame: np.ndarray, camera_name: str) -> List[Finding]:
        h, w = frame.shape[:2]
        classes = [PERSON, BACKPACK, HANDBAG, SUITCASE, *OWN_PRODUCT_CLASSES.keys()]
        boxes = self.engine.detect(frame, classes=classes)
        findings: List[Finding] = []
        floor_items = [
            b
            for b in boxes
            if (
                (b.cls_id in OWN_PRODUCT_CLASSES or b.cls_id in (BACKPACK, HANDBAG, SUITCASE))
                and b.cy > int(h * 0.72)
            )
        ]
        if len(floor_items) >= 2:
            labels = ", ".join(sorted({b.label for b in floor_items}))
            findings.append(
                Finding(
                    kind="cleanliness",
                    severity="medium",
                    title="Беспорядок / вещи на полу",
                    detail=f"{camera_name}: внизу кадра [{labels}] — нужна уборка/порядок.",
                    boxes=floor_items,
                )
            )
        floor = frame[int(h * 0.75) : h, int(w * 0.1) : int(w * 0.9)]
        if floor.size:
            gray = cv2.cvtColor(floor, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 80, 160)
            density = float(edges.mean())
            if density > 28:
                findings.append(
                    Finding(
                        kind="cleanliness",
                        severity="low",
                        title="Возможный беспорядок на полу",
                        detail=f"{camera_name}: высокая «зашумлённость» пола (score={density:.1f}).",
                        boxes=[],
                    )
                )
        return findings

    def analyze_fridge(self, frame: np.ndarray, camera_name: str, roi: Optional[tuple] = None) -> List[Finding]:
        h, w = frame.shape[:2]
        if roi is None:
            x1, y1, x2, y2 = int(w * 0.55), int(h * 0.25), int(w * 0.95), int(h * 0.85)
        else:
            rx1, ry1, rx2, ry2 = roi
            x1, y1, x2, y2 = int(rx1 * w), int(ry1 * h), int(rx2 * w), int(ry2 * h)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # reuse rest from original file via reading - keep simplified
        mean = float(gray.mean())
        findings: List[Finding] = []
        if mean > 90:
            findings.append(
                Finding(
                    kind="fridge_stock",
                    severity="medium",
                    title="Витрина/холодильник выглядит пусто",
                    detail=f"{camera_name}: яркость ROI={mean:.0f} — проверьте заполненность.",
                    boxes=[Box(-1, "fridge_roi", 1.0, x1, y1, x2, y2)],
                )
            )
        return findings

    def analyze_incidents(self, frame: np.ndarray, camera_name: str) -> List[Finding]:
        boxes = self.engine.detect(frame, classes=[PERSON])
        people = [b for b in boxes if b.cls_id == PERSON]
        findings: List[Finding] = []
        if len(people) >= 6:
            findings.append(
                Finding(
                    kind="incidents",
                    severity="high",
                    title="Скопление людей",
                    detail=f"{camera_name}: {len(people)} человек в кадре.",
                    boxes=people,
                )
            )
        return findings

    def analyze_staff(self, frame: np.ndarray, camera_name: str) -> List[Finding]:
        boxes = self.engine.detect(frame, classes=[PERSON])
        people = [b for b in boxes if b.cls_id == PERSON]
        if people:
            return [
                Finding(
                    kind="staff_presence",
                    severity="info",
                    title="Люди в зоне",
                    detail=f"{camera_name}: {len(people)} чел.",
                    boxes=people[:3],
                )
            ]
        return [
            Finding(
                kind="staff_presence",
                severity="medium",
                title="Зона пуста",
                detail=f"{camera_name}: людей не видно.",
                boxes=[],
            )
        ]

    def count_people_in_roi(self, frame: np.ndarray, roi: Tuple[float, float, float, float]) -> int:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
        boxes = self.engine.detect(frame, classes=[PERSON])
        n = 0
        for b in boxes:
            if b.cls_id != PERSON:
                continue
            if x1 <= b.cx <= x2 and y1 <= b.cy <= y2:
                n += 1
        return n


def draw_findings(frame: np.ndarray, findings: List[Finding]) -> np.ndarray:
    """Рисует боксы + live-подсказку сотруднику прямо на кадре мониторинга."""
    out = frame.copy()
    color_map = {
        "critical": (0, 0, 255),
        "high": (0, 0, 255),
        "medium": (0, 200, 255),
        "low": (0, 220, 220),
        "info": (0, 220, 0),
    }
    tip_lines: List[str] = []
    for f in findings:
        color = color_map.get(f.severity, (255, 255, 255))
        for b in f.boxes:
            cv2.rectangle(out, (b.x1, b.y1), (b.x2, b.y2), color, 2)
            lab = b.label[:30]
            (tw, th), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            ty = max(th + 4, b.y1 - 6)
            cv2.rectangle(out, (b.x1, ty - th - 4), (b.x1 + tw + 6, ty + 4), (0, 0, 0), -1)
            cv2.putText(
                out,
                lab,
                (b.x1 + 3, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        cv2.putText(
            out,
            f.title[:52],
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            color,
            2,
            cv2.LINE_AA,
        )
        if f.kind == "own_products":
            if f.severity in ("high", "critical"):
                tip_lines.append("ДЕЙСТВИЕ: подойти к гостю — своя еда/напиток")
            else:
                tip_lines.append("ПРОВЕРИТЬ: возможно своё — уточнить у бара")
        elif f.kind == "reception_queue":
            tip_lines.append("ОЧЕРЕДЬ: принять гостя у админки")
        elif f.detail:
            tip_lines.append(f.detail[:60])

    if tip_lines:
        h, w = out.shape[:2]
        band_h = 36 + 22 * min(2, len(tip_lines))
        cv2.rectangle(out, (0, h - band_h), (w, h), (0, 0, 0), -1)
        y = h - band_h + 24
        for line in tip_lines[:2]:
            cv2.putText(
                out,
                line[:70],
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (0, 220, 255),
                2,
                cv2.LINE_AA,
            )
            y += 22
    return out
