"""
Скрипт первичной инициализации каталога (без GUI).
Запуск: python scripts/init_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from src.catalog.service import CatalogService

    cat = CatalogService()
    items = cat.list_items()
    print(f"Каталог: {len(items)} позиций")
    for it in items:
        print(f"  - {it['sku']}: {it['name']} | aliases={len(it.get('aliases') or [])}")
    cat.sync_club_menu()
    print("Синхронизирован data/club_menu.yaml")


if __name__ == "__main__":
    main()
