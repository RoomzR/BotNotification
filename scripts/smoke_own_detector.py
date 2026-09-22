#!/usr/bin/env python3
"""Smoke-тесты own-detector + Product Scan Coach + AlertGate."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def test_held_blank_is_suspicious_own() -> None:
    from src.vision.menu_matcher import MenuMatcher

    m = MenuMatcher()
    v = m.classify_item("bottle", np.zeros((120, 60, 3), dtype=np.uint8))
    if v.status != "own":
        _fail(f"blank bottle expected own, got {v.status} {v.reason}")
    if getattr(v, "certainty", "") != "suspicious":
        _fail(f"blank bottle expected suspicious, got {v.certainty}")
    print("OK held blank → own/suspicious")


def test_wine_glass_held() -> None:
    from src.vision.menu_matcher import MenuMatcher, HELD_PRODUCT_LABELS, SIZE_GATE_LABELS

    if "wine_glass" not in HELD_PRODUCT_LABELS and "wine glass" not in HELD_PRODUCT_LABELS:
        _fail("wine_glass not in HELD_PRODUCT_LABELS")
    if "wine_glass" in SIZE_GATE_LABELS:
        _fail("wine_glass must NOT be in SIZE_GATE_LABELS")
    m = MenuMatcher()
    v = m.classify_item("wine glass", np.zeros((120, 60, 3), dtype=np.uint8))
    if v.status != "own":
        _fail(f"wine glass expected own, got {v.status}")
    print("OK wine glass → own (not size-gated)")


def test_utensils_not_held() -> None:
    from src.vision.menu_matcher import HELD_PRODUCT_LABELS, MenuMatcher

    for u in ("fork", "knife", "spoon"):
        if u in HELD_PRODUCT_LABELS:
            _fail(f"{u} should not trigger held-own")
    m = MenuMatcher()
    v = m.classify_item("fork", np.zeros((80, 40, 3), dtype=np.uint8))
    if v.status == "own" and "упаковка" in (v.reason or ""):
        _fail(f"fork should not be held-own: {v}")
    print("OK utensils not held-alert")


def test_forbidden_ocr_path() -> None:
    from src.vision.menu_matcher import MenuMatcher

    m = MenuMatcher()
    v = m.classify_item("product", np.zeros((80, 80, 3), dtype=np.uint8), hinted_brand="pepsi")
    if v.status != "own":
        _fail(f"pepsi hint expected own, got {v}")
    print("OK forbidden hint → own")


def test_coach_duplicate_angle() -> None:
    from src.vision.product_scan_coach import ProductScanCoach

    coach = ProductScanCoach()
    img = np.zeros((240, 180, 3), dtype=np.uint8)
    cv2.rectangle(img, (30, 20), (150, 220), (40, 120, 200), -1)
    cv2.putText(img, "TUC", (45, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
    for i in range(20):
        cv2.circle(img, (40 + i * 5, 40 + (i % 7) * 8), 3, (0, 255, 100), -1)

    tmp = ROOT / "data" / "_smoke_refs"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "front.png"
    cv2.imwrite(str(path), img)
    refs = [{"path": str(path.relative_to(ROOT)), "angle": "front"}]

    v1 = coach.evaluate_capture(img, target_angle="front", existing_refs=[])
    if not v1.ok:
        _fail(f"first capture must be ok for smoke fixture: {v1.message}")

    v2 = coach.evaluate_capture(img.copy(), target_angle="side", existing_refs=refs)
    if v2.ok or "duplicate_angle" not in v2.issues:
        _fail(f"expected duplicate reject, got {v2}")
    print(f"OK coach duplicate reject: {v2.message}")


def test_catalog_angles() -> None:
    from src.catalog.service import CatalogService

    cat = CatalogService()
    stats = cat.coverage_stats()
    assert "ratio" in stats and "incomplete_skus" in stats
    # completeness = angles, not raw photo count
    for sku in ("BULBA-75", "GRENKI-01"):
        item = cat.get_item(sku)
        if not item:
            continue
        have = cat.angles_present(sku)
        if cat.sku_scan_complete(sku) and len(have) < 3:
            _fail(f"{sku} complete but angles={have}")
    print(f"OK coverage_stats ratio={stats['ratio']:.2f} complete={stats['complete']}/{stats['total']}")


def test_alert_gate_presence() -> None:
    from src.platform.orchestrator import AlertGate

    g = AlertGate(cooldown=1.0, confirm=2.0, gap_reset=0.5)
    t0 = time.time()
    assert g.allow("k", t0) is False
    assert g.allow("k", t0 + 1.0) is False
    # gap — таймер сброшен
    assert g.allow("k", t0 + 2.0) is False  # restarts after gap? last_hit was 1.0, now 2.0 gap=1>0.5
    # continuous
    g2 = AlertGate(cooldown=0.1, confirm=1.0, gap_reset=5.0)
    t = time.time()
    assert g2.allow("a", t) is False
    assert g2.allow("a", t + 0.5) is False
    assert g2.allow("a", t + 1.1) is True
    print("OK AlertGate continuous presence")


def test_template_match_all() -> None:
    from src.vision.template_matcher import TemplateMatcher

    tm = TemplateMatcher()
    assert hasattr(tm, "match_all")
    print("OK TemplateMatcher.match_all exists")


def main() -> int:
    test_catalog_angles()
    test_held_blank_is_suspicious_own()
    test_wine_glass_held()
    test_utensils_not_held()
    test_forbidden_ocr_path()
    test_coach_duplicate_angle()
    test_alert_gate_presence()
    test_template_match_all()
    print("ALL SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
