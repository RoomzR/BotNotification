#!/usr/bin/env python3
"""Очистка мусорных aliases в SQLite + синхронизация с bar_catalog.yaml."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# токены, которые OCR/автозаполнение часто засовывают в aliases
BLOCKLIST = {
    "intel",
    "hyperx",
    "logitech",
    "logitech6403",
    "razer",
    "corsair",
    "steelseries",
    "nvidia",
    "amd",
    "windows",
    "microsoft",
    "chrome",
    "space",
    "chips",  # слишком общее → ложный клуб
    "snack",
    "product",
    "bottle",
    "drink",
    "food",
    "pack",
    "packet",
    "net",
    "www",
    "http",
    "com",
    "ru",
    "by",
    "jpg",
    "png",
    "img",
    "photo",
    "camera",
    "cyberx",
    "gomel",
}

# короткие/шумные паттерны
NOISE_RE = re.compile(r"^(\d+[a-z]*|[a-z]\d+|6e3|e3|x\d+)$", re.I)


def is_bad_alias(a: str) -> bool:
    t = (a or "").strip().lower().replace("ё", "е")
    if len(t) < 2:
        return True
    if t in BLOCKLIST:
        return True
    if NOISE_RE.match(t.replace(" ", "")):
        return True
    if any(b in t for b in ("logitech", "hyperx", "intel", "razer")):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-yaml", action="store_true", help="перезаписать aliases из bar_catalog.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.catalog.service import CatalogService
    import yaml

    cat = CatalogService()
    yaml_aliases: dict[str, list[str]] = {}
    yaml_path = ROOT / "data" / "bar_catalog.yaml"
    if yaml_path.exists():
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        for p in raw.get("products") or []:
            sku = str(p.get("sku", "")).strip()
            if sku:
                yaml_aliases[sku] = [str(a) for a in (p.get("aliases") or []) if a]

    changed = 0
    for item in cat.list_items(active_only=False):
        sku = item["sku"]
        old = list(item.get("aliases") or [])
        if args.from_yaml and sku in yaml_aliases:
            new = list(yaml_aliases[sku])
        else:
            new = [a for a in old if not is_bad_alias(str(a))]
            # подмешать чистые из yaml, если есть
            for a in yaml_aliases.get(sku, []):
                if a not in new and not is_bad_alias(a):
                    new.append(a)
        if new != old:
            print(f"{sku}: {old} -> {new}")
            changed += 1
            if not args.dry_run:
                cat.upsert_item(
                    sku=sku,
                    name=item["name"],
                    category=item.get("category") or "other",
                    weight=item.get("weight") or "",
                    aliases=new,
                    sync_menu=False,
                )
    if not args.dry_run:
        cat.sync_club_menu()
    print(f"done: {changed} SKU updated" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
