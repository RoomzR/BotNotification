"""Визуальная панель проверки камер CyberX Control."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.vision.analyzers import Finding, draw_findings


@dataclass
class CamTile:
    cam_id: str
    name: str
    frame: Optional[np.ndarray] = None
    findings: List[Finding] = field(default_factory=list)
    status: str = "ожидание..."
    last_check: float = 0.0
    ok: bool = False
    reception_count: int = 0
    reception_waited: float = 0.0
    reception_need: float = 25.0
    is_reception: bool = False
    roi: Optional[Tuple[float, float, float, float]] = None


def _put(img: np.ndarray, text: str, org: Tuple[int, int], color, scale=0.55, thick=1) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def render_tile(tile: CamTile, tw: int, th: int, active: bool = False) -> np.ndarray:
    canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    canvas[:] = (28, 28, 28)
    # масштаб подписей от размера клетки
    title_sc = max(0.55, min(0.85, tw / 520))
    sub_sc = max(0.45, min(0.65, tw / 560))
    bar_h = max(42, int(48 * title_sc))

    if tile.frame is not None:
        view = draw_findings(tile.frame.copy(), tile.findings)
        # ROI очереди у Reception
        if tile.is_reception and tile.roi:
            h, w = view.shape[:2]
            x1, y1, x2, y2 = tile.roi
            p1 = (int(x1 * w), int(y1 * h))
            p2 = (int(x2 * w), int(y2 * h))
            color = (0, 255, 120) if tile.reception_count == 0 else (0, 180, 255)
            cv2.rectangle(view, p1, p2, color, 2)
            _put(view, "ADMIN QUEUE ROI", (p1[0] + 4, max(18, p1[1] - 8)), color, 0.5, 1)
            if tile.reception_count > 0:
                _put(
                    view,
                    f"WAIT {tile.reception_waited:.0f}/{tile.reception_need:.0f}s  people={tile.reception_count}",
                    (p1[0] + 4, p1[1] + 22),
                    color,
                    0.55,
                    2,
                )
        canvas = cv2.resize(view, (tw, th))
    else:
        _put(canvas, "NO SIGNAL", (tw // 2 - 70, th // 2), (80, 80, 255), 0.8, 2)

    # верхняя плашка
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (tw, bar_h), (0, 0, 0), -1)
    canvas = cv2.addWeighted(overlay, 0.45, canvas, 0.55, 0)

    border = (0, 220, 255) if active else ((60, 200, 60) if tile.ok else (60, 60, 200))
    cv2.rectangle(canvas, (0, 0), (tw - 1, th - 1), border, 3 if active else 2)

    age = time.time() - tile.last_check if tile.last_check else 999
    age_s = f"{age:.0f}s ago" if tile.last_check else "never"
    _put(canvas, tile.name[:36], (10, int(18 * title_sc / 0.55)), (255, 255, 255), title_sc, 2)
    _put(
        canvas,
        f"{tile.status} | {age_s}",
        (10, int(38 * title_sc / 0.55)),
        (210, 210, 210),
        sub_sc,
        1,
    )

    if tile.findings:
        # action strip — что делать сотруднику в прямом эфире
        tip = ""
        for f in tile.findings:
            if f.kind == "own_products":
                tip = ">> ПРОВЕРИТЬ ГОСТЯ (еда/напиток)" if f.severity == "medium" else ">> ПОДОЙТИ: СВОЁ"
                break
            if f.kind == "reception_queue":
                tip = ">> ОЧЕРЕДЬ У АДМИНКИ"
                break
        if tip:
            cv2.rectangle(canvas, (0, th - 28), (tw, th), (0, 0, 0), -1)
            _put(canvas, tip[:40], (8, th - 8), (0, 220, 255), max(0.5, sub_sc), 2)
        y = th - 34 - 18 * min(2, len(tile.findings))
        for f in tile.findings[:2]:
            col = {
                "critical": (0, 0, 255),
                "high": (0, 100, 255),
                "medium": (0, 200, 255),
                "low": (0, 220, 220),
                "info": (0, 220, 0),
            }.get(f.severity, (255, 255, 255))
            _put(canvas, f.title[:42], (10, y), col, sub_sc, 1)
            y += 18

    return canvas


def build_dashboard(
    tiles: Dict[str, CamTile],
    order: List[str],
    active_id: str,
    recent_alerts: List[str],
    footer: str,
    cols: int = 5,
    cell_w: int = 0,
    cell_h: int = 0,
    target_w: int = 1680,
    target_h: int = 920,
) -> np.ndarray:
    """
    Мозаика камер: по умолчанию 4–5 в ряд, клетки крупнее под Desktop.
    """
    n = len(order)
    if cols is None or cols <= 0:
        cols = 5
    # авто: мало камер — меньше колонок, чтобы плитки были крупнее
    if n > 0:
        if n <= 4:
            cols = min(cols, max(1, n))
        else:
            cols = max(4, min(5, cols))
    cols = max(1, int(cols))
    rows = max(1, (n + cols - 1) // cols) if n else 1
    panel_h = 100

    if cell_w <= 0:
        cell_w = max(320, target_w // cols)
    if cell_h <= 0:
        cell_h = max(220, (target_h - panel_h) // rows)

    grid = np.zeros((rows * cell_h + panel_h, cols * cell_w, 3), dtype=np.uint8)
    grid[:] = (18, 18, 18)

    for i, cid in enumerate(order):
        r, c = divmod(i, cols)
        tile = tiles.get(cid) or CamTile(cid, cid)
        cell = render_tile(tile, cell_w, cell_h, active=(cid == active_id))
        y1, x1 = r * cell_h, c * cell_w
        grid[y1 : y1 + cell_h, x1 : x1 + cell_w] = cell

    # нижняя панель статуса (компактнее — больше места камерам)
    y0 = rows * cell_h
    cv2.rectangle(grid, (0, y0), (grid.shape[1], grid.shape[0]), (12, 12, 12), -1)
    _put(grid, "CyberX Control — проверка камер", (12, y0 + 22), (0, 220, 255), 0.7, 2)
    _put(grid, footer[:140], (12, y0 + 48), (220, 220, 220), 0.5, 1)

    ay = y0 + 72
    _put(grid, "Алерты:", (12, ay), (180, 180, 180), 0.45, 1)
    for line in recent_alerts[:2]:
        ay += 16
        _put(grid, line[:120], (12, ay), (0, 200, 255), 0.45, 1)

    return grid
