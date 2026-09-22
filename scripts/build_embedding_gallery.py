#!/usr/bin/env python3
"""Пересобрать data/catalog_embeddings.npz из эталонов каталога."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.catalog.service import CatalogService
    from src.vision.embedding_matcher import EmbeddingMatcher

    cat = CatalogService()
    refs: dict[str, list[str]] = {}
    for ref in cat.list_refs():
        refs.setdefault(ref["sku"], []).append(ref["path"])
    emb = EmbeddingMatcher(enabled=True)
    emb.invalidate()
    n = emb.build_gallery(refs, force=True)
    print(f"gallery vectors: {n}")
    print(f"skus with refs: {len(refs)}")
    missing = [i["sku"] for i in cat.list_items(active_only=True) if i["sku"] not in refs or not refs[i["sku"]]]
    if missing:
        print("SKU без эталонов:", ", ".join(missing))
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
