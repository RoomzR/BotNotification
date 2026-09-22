"""Live Product Scan Coach — ORB «точки» + проверка ракурсов front/side/far.

Паттерн UX как Objectron (точки на объекте + подсказки), без MediaPipe Objectron
(он не знает снеки бара). Matching-ракурсов через embedding distance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]

ANGLE_SLOTS = ("front", "side", "far")
ANGLE_LABELS_RU = {
    "front": "ЛИЦО",
    "side": "БОК",
    "far": "ДАЛЬШЕ",
}
ANGLE_TIPS = {
    "front": "Держите этикетку ПРЯМО к камере, крупно в рамке. Без бликов.",
    "side": "ПОВЕРНИТЕ упаковку вправо/влево ~45°. Нужен другой ракурс.",
    "far": "ОТОДВИНЬТЕ товар чуть дальше. Весь корпус в кадре.",
}

# cosine к уже сохранённому ракурсу — слишком похоже → дубликат
# (JPEG/локализация дают ~0.85–0.95 на «том же» кадре)
DUPLICATE_COSINE = 0.82
# минимальная «новизна» относительно заполненных слотов
MIN_NOVELTY = 0.12


@dataclass
class CoachVerdict:
    ok: bool
    angle: str  # целевой слот
    message: str
    issues: List[str] = field(default_factory=list)
    keypoint_count: int = 0
    coverage: float = 0.0
    duplicate_of: str = ""  # angle который слишком похож
    bbox: Optional[Tuple[int, int, int, int]] = None


class ProductScanCoach:
    def __init__(self, n_features: int = 2000):
        self._orb = cv2.ORB_create(n_features)
        self._embed = None

    def _get_embed(self):
        if self._embed is None:
            from .embedding_matcher import EmbeddingMatcher

            self._embed = EmbeddingMatcher(enabled=True)
        return self._embed

    def localize_pack(self, bgr: np.ndarray) -> Tuple[np.ndarray, Optional[Tuple[int, int, int, int]]]:
        """Грубый crop упаковки: наибольший контур в центре / весь кадр."""
        h, w = bgr.shape[:2]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 40, 120)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_area = 0.0
        min_area = (h * w) * 0.04
        cx0, cy0 = w / 2, h / 2
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            area = float(bw * bh)
            if area < min_area:
                continue
            cx, cy = x + bw / 2, y + bh / 2
            dist = ((cx - cx0) ** 2 + (cy - cy0) ** 2) ** 0.5
            score = area / (1.0 + dist / max(w, h))
            if score > best_area:
                best_area = score
                best = (x, y, x + bw, y + bh)
        if best is None:
            m = int(min(h, w) * 0.12)
            best = (m, m, w - m, h - m)
        x1, y1, x2, y2 = best
        pad = 8
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        crop = bgr[y1:y2, x1:x2]
        return crop, (x1, y1, x2, y2)

    def keypoints(self, bgr: np.ndarray) -> Tuple[List, np.ndarray]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        kps, des = self._orb.detectAndCompute(gray, None)
        return list(kps or []), des

    def coverage_score(self, bgr: np.ndarray, kps: Sequence) -> float:
        """Доля сетки 4x4 в центре, где есть точки."""
        if not kps:
            return 0.0
        h, w = bgr.shape[:2]
        x0, x1 = int(w * 0.2), int(w * 0.8)
        y0, y1 = int(h * 0.2), int(h * 0.8)
        gw, gh = 4, 4
        cells = set()
        for kp in kps:
            x, y = kp.pt
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            cx = min(gw - 1, int((x - x0) / max(1, (x1 - x0)) * gw))
            cy = min(gh - 1, int((y - y0) / max(1, (y1 - y0)) * gh))
            cells.add((cx, cy))
        return len(cells) / float(gw * gh)

    def quality(self, bgr: np.ndarray) -> Tuple[bool, str, List[str]]:
        if bgr is None or bgr.size == 0:
            return False, "пустой кадр", ["empty"]
        h, w = bgr.shape[:2]
        issues: List[str] = []
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean = float(np.mean(gray))
        if sharp < 45:
            issues.append("размыто")
        if mean < 45:
            issues.append("темно")
        if mean > 220:
            issues.append("пересвет / блик")
        if min(h, w) < 120:
            issues.append("слишком мелко")
        kps, _ = self.keypoints(bgr)
        cov = self.coverage_score(bgr, kps)
        if len(kps) < 40:
            issues.append(f"мало точек ({len(kps)})")
        if cov < 0.35:
            issues.append("мало покрытия по центру")
        ok = not issues
        msg = "OK" if ok else ", ".join(issues)
        return ok, msg, issues

    def draw_overlay(
        self,
        bgr: np.ndarray,
        *,
        target_angle: str,
        have_angles: Sequence[str],
        verdict: Optional[CoachVerdict] = None,
    ) -> np.ndarray:
        """BGR frame with keypoints + slot progress."""
        out = bgr.copy()
        crop, bbox = self.localize_pack(out)
        kps, _ = self.keypoints(crop if crop.size else out)
        ox, oy = (bbox[0], bbox[1]) if bbox else (0, 0)
        for kp in kps[:800]:
            x, y = int(kp.pt[0] + ox), int(kp.pt[1] + oy)
            cv2.circle(out, (x, y), 2, (0, 220, 255), -1, lineType=cv2.LINE_AA)
        # соединяем ближайшие точки — «mesh» как у face-landmarks (визуально)
        pts = [(int(kp.pt[0] + ox), int(kp.pt[1] + oy)) for kp in kps[:120]]
        for i, (x1, y1) in enumerate(pts):
            for x2, y2 in pts[i + 1 : i + 4]:
                if abs(x1 - x2) + abs(y1 - y2) < 55:
                    cv2.line(out, (x1, y1), (x2, y2), (0, 140, 255), 1, cv2.LINE_AA)
        if bbox:
            x1, y1, x2, y2 = bbox
            cv2.rectangle(out, (x1, y1), (x2, y2), (255, 40, 40), 2)

        h, w = out.shape[:2]
        have = {a for a in have_angles}
        parts = []
        for a in ANGLE_SLOTS:
            mark = "OK" if a in have else (">>" if a == target_angle else "--")
            parts.append(f"{ANGLE_LABELS_RU[a]}{mark}")
        bar = " | ".join(parts)
        cv2.rectangle(out, (0, 0), (w, 56), (0, 0, 0), -1)
        cv2.putText(
            out,
            bar,
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tip = ANGLE_TIPS.get(target_angle, "")
        if verdict and not verdict.ok:
            tip = verdict.message[:80]
        tip_color = (0, 200, 255) if (verdict and not verdict.ok) else (0, 255, 180)
        if verdict and verdict.ok:
            tip = f"OK SNAP: {ANGLE_LABELS_RU.get(target_angle, target_angle)} | {tip}"[:80]
            tip_color = (80, 220, 120)
        cv2.putText(
            out,
            tip[:72],
            (10, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            tip_color,
            1,
            cv2.LINE_AA,
        )
        n = len(kps)
        cv2.putText(
            out,
            f"pts:{n}",
            (w - 80, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 180),
            1,
            cv2.LINE_AA,
        )
        return out

    def _load_ref_embs(self, refs: List[dict]) -> Dict[str, List[np.ndarray]]:
        emb = self._get_embed()
        by_ang: Dict[str, List[np.ndarray]] = {}
        for r in refs:
            ang = (r.get("angle") or "").strip().lower()
            if ang not in ANGLE_SLOTS:
                continue
            p = ROOT / r["path"] if not Path(r["path"]).is_absolute() else Path(r["path"])
            if not p.exists():
                continue
            img = cv2.imread(str(p))
            if img is None:
                continue
            crop, _ = self.localize_pack(img)
            vec = emb.embed_bgr(crop if crop is not None and crop.size else img)
            if vec is None:
                continue
            by_ang.setdefault(ang, []).append(vec)
        return by_ang

    def evaluate_capture(
        self,
        bgr: np.ndarray,
        *,
        target_angle: str,
        existing_refs: List[dict],
    ) -> CoachVerdict:
        target = target_angle if target_angle in ANGLE_SLOTS else "front"
        crop, bbox = self.localize_pack(bgr)
        work = crop if crop is not None and crop.size else bgr
        ok_q, msg_q, issues = self.quality(work)
        kps, _ = self.keypoints(work)
        cov = self.coverage_score(work, kps)

        if not ok_q:
            return CoachVerdict(
                ok=False,
                angle=target,
                message=f"Качество: {msg_q}. Переснимите.",
                issues=issues,
                keypoint_count=len(kps),
                coverage=cov,
                bbox=bbox,
            )

        emb = self._get_embed()
        if not emb._ensure_backend():
            return CoachVerdict(
                ok=False,
                angle=target,
                message="Нет embedding-модели — скачайте: scripts/download_embedding_model.py",
                issues=["no_embed"],
                keypoint_count=len(kps),
                coverage=cov,
                bbox=bbox,
            )
        vec = emb.embed_bgr(work)
        if vec is None:
            return CoachVerdict(
                ok=False,
                angle=target,
                message="Не удалось посчитать embedding кадра — переснимите / проверьте модель",
                issues=["embed_fail"],
                keypoint_count=len(kps),
                coverage=cov,
                bbox=bbox,
            )
        by_ang = self._load_ref_embs(existing_refs)
        if by_ang:
            best_ang = ""
            best_sim = -1.0
            for ang, vecs in by_ang.items():
                for v in vecs:
                    sim = float(np.dot(vec, v))
                    if sim > best_sim:
                        best_sim = sim
                        best_ang = ang
            if best_sim >= DUPLICATE_COSINE:
                label = ANGLE_LABELS_RU.get(best_ang, best_ang)
                need = ANGLE_LABELS_RU.get(target, target)
                return CoachVerdict(
                    ok=False,
                    angle=target,
                    message=f"Это снова {label} (похоже {best_sim:.0%}). Нужен ракурс: {need}.",
                    issues=["duplicate_angle"],
                    keypoint_count=len(kps),
                    coverage=cov,
                    duplicate_of=best_ang,
                    bbox=bbox,
                )
            if target in by_ang and best_ang == target and best_sim > (1.0 - MIN_NOVELTY):
                return CoachVerdict(
                    ok=False,
                    angle=target,
                    message=f"Ракурс {ANGLE_LABELS_RU[target]} уже есть. Смените сторону.",
                    issues=["slot_filled"],
                    keypoint_count=len(kps),
                    coverage=cov,
                    duplicate_of=target,
                    bbox=bbox,
                )

        return CoachVerdict(
            ok=True,
            angle=target,
            message=f"OK — можно сохранить как {ANGLE_LABELS_RU.get(target, target)}",
            issues=[],
            keypoint_count=len(kps),
            coverage=cov,
            bbox=bbox,
        )

    @staticmethod
    def progress_text(have_angles: Sequence[str], target: Optional[str]) -> str:
        have = {a for a in have_angles}
        parts = []
        for a in ANGLE_SLOTS:
            if a in have:
                parts.append(f"{ANGLE_LABELS_RU[a]} OK")
            elif a == target:
                parts.append(f"{ANGLE_LABELS_RU[a]} >>")
            else:
                parts.append(f"{ANGLE_LABELS_RU[a]} --")
        return " · ".join(parts)
