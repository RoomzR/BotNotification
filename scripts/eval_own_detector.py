#!/usr/bin/env python3
"""
Прогон тестовых кадров для own-detector.

Структура папки:
  eval_own/
    club/   # кадры/кропы продукции бара (ожидаем club или не own)
    own/    # кадры/кропы чужого (ожидаем own)

Exit code 1 если есть ≥5 own-фото и recall < --min-recall (по умолчанию 0.9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def iter_images(folder: Path):
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "eval_own")
    ap.add_argument("--min-recall", type=float, default=0.9)
    ap.add_argument("--min-samples", type=int, default=5, help="минимум own-фото для fail-gate")
    args = ap.parse_args()

    from src.vision.menu_matcher import MenuMatcher

    menu = MenuMatcher()
    club_dir = args.dir / "club"
    own_dir = args.dir / "own"
    if not club_dir.exists() and not own_dir.exists():
        print(f"Создайте {args.dir}/club и {args.dir}/own с фото")
        club_dir.mkdir(parents=True, exist_ok=True)
        own_dir.mkdir(parents=True, exist_ok=True)
        return 0

    def run_set(folder: Path, expect_own: bool):
        tp = fp = fn = tn = 0
        details = []
        n = 0
        for p in iter_images(folder):
            img = cv2.imread(str(p))
            if img is None:
                continue
            n += 1
            v = menu.classify_item("product", img)
            is_own = v.status == "own"
            if expect_own and is_own:
                tp += 1
            elif expect_own and not is_own:
                fn += 1
                details.append(f"FN {p.name}: {v.status} {v.reason}")
            elif (not expect_own) and is_own:
                fp += 1
                details.append(f"FP {p.name}: {v.status} {v.reason}")
            else:
                tn += 1
        return tp, fp, fn, tn, details, n

    print("=== club (ожидаем НЕ own) ===")
    c_tp, c_fp, c_fn, c_tn, c_d, c_n = run_set(club_dir, expect_own=False)
    print(f"n={c_n} correct_non_own={c_tn} false_own={c_fp}")

    print("=== own (ожидаем own) ===")
    o_tp, o_fp, o_fn, o_tn, o_d, o_n = run_set(own_dir, expect_own=True)
    print(f"n={o_n} true_own={o_tp} missed_own={o_fn}")

    prec = o_tp / max(1, o_tp + c_fp)
    rec = o_tp / max(1, o_tp + o_fn)
    print(f"precision≈{prec:.2f} recall≈{rec:.2f}")
    for line in (c_d + o_d)[:20]:
        print(" ", line)

    if o_n >= args.min_samples and rec < args.min_recall:
        print(f"FAIL: recall {rec:.2f} < {args.min_recall} (own samples={o_n})")
        return 1
    if o_n < args.min_samples:
        print(f"SKIP gate: нужно ≥{args.min_samples} own-фото (сейчас {o_n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
