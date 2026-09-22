"""Каталог продукции бара: SKU, aliases, эталонные фото, синхронизация с club_menu."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG_YAML = ROOT / "data" / "bar_catalog.yaml"
MENU_PATH = ROOT / "data" / "club_menu.yaml"
REFS_DIR = ROOT / "data" / "product_refs"


class CatalogService:
    def __init__(self, store=None):
        from src.storage.db import Store

        self.store = store or Store()
        REFS_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()
        self.seed_from_yaml()

    ANGLE_SLOTS = ("front", "side", "far")

    def ensure_schema(self) -> None:
        with self.store._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_items (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sku TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL,
                  category TEXT DEFAULT 'other',
                  weight TEXT DEFAULT '',
                  aliases_json TEXT NOT NULL DEFAULT '[]',
                  active INTEGER NOT NULL DEFAULT 1,
                  created_at REAL,
                  updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS catalog_refs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sku TEXT NOT NULL,
                  path TEXT NOT NULL,
                  ocr_text TEXT DEFAULT '',
                  angle TEXT DEFAULT '',
                  created_at REAL,
                  FOREIGN KEY(sku) REFERENCES catalog_items(sku)
                );
                """
            )
            cols = {r[1] for r in c.execute("PRAGMA table_info(catalog_refs)").fetchall()}
            if "angle" not in cols:
                c.execute("ALTER TABLE catalog_refs ADD COLUMN angle TEXT DEFAULT ''")
        self._migrate_ref_angles()

    def _migrate_ref_angles(self) -> None:
        """Старые refs без angle: по порядку id → front, side, far."""
        with self.store._conn() as c:
            rows = c.execute(
                "SELECT id, sku, angle FROM catalog_refs ORDER BY sku, id ASC"
            ).fetchall()
        by_sku: dict[str, list] = {}
        for r in rows:
            by_sku.setdefault(r["sku"], []).append(r)
        with self.store._conn() as c:
            for sku, refs in by_sku.items():
                for i, r in enumerate(refs):
                    ang = (r["angle"] or "").strip().lower()
                    if ang in self.ANGLE_SLOTS:
                        continue
                    assigned = self.ANGLE_SLOTS[min(i, len(self.ANGLE_SLOTS) - 1)]
                    c.execute("UPDATE catalog_refs SET angle=? WHERE id=?", (assigned, r["id"]))

    def seed_from_yaml(self) -> None:
        if not CATALOG_YAML.exists():
            return
        raw = yaml.safe_load(CATALOG_YAML.read_text(encoding="utf-8")) or {}
        for p in raw.get("products", []) or []:
            sku = str(p.get("sku", "")).strip()
            if not sku:
                continue
            existing = self.get_item(sku)
            if existing:
                continue
            self.upsert_item(
                sku=sku,
                name=str(p.get("name", sku)),
                category=str(p.get("category", "other")),
                weight=str(p.get("weight", "")),
                aliases=[str(a) for a in (p.get("aliases") or []) if a],
                sync_menu=False,
            )
        self.sync_club_menu()

    def upsert_item(
        self,
        sku: str,
        name: str,
        category: str = "other",
        weight: str = "",
        aliases: Optional[list[str]] = None,
        sync_menu: bool = True,
    ) -> None:
        now = time.time()
        aliases = aliases or []
        with self.store._conn() as c:
            row = c.execute("SELECT id FROM catalog_items WHERE sku=?", (sku,)).fetchone()
            payload = json.dumps(aliases, ensure_ascii=False)
            if row is None:
                c.execute(
                    """
                    INSERT INTO catalog_items(sku,name,category,weight,aliases_json,active,created_at,updated_at)
                    VALUES(?,?,?,?,?,1,?,?)
                    """,
                    (sku, name, category, weight, payload, now, now),
                )
            else:
                c.execute(
                    """
                    UPDATE catalog_items
                    SET name=?, category=?, weight=?, aliases_json=?, updated_at=?, active=1
                    WHERE sku=?
                    """,
                    (name, category, weight, payload, now, sku),
                )
            # складская запись
            inv = c.execute("SELECT sku FROM products WHERE sku=?", (sku,)).fetchone()
            if inv is None:
                c.execute(
                    "INSERT INTO products(sku,name,qty,price,updated_at) VALUES(?,?,0,0,?)",
                    (sku, name, now),
                )
            else:
                c.execute("UPDATE products SET name=?, updated_at=? WHERE sku=?", (name, now, sku))
        if sync_menu:
            self.sync_club_menu()

    def get_item(self, sku: str) -> Optional[dict]:
        with self.store._conn() as c:
            row = c.execute("SELECT * FROM catalog_items WHERE sku=?", (sku,)).fetchone()
        return self._row(row) if row else None

    def list_items(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM catalog_items"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY category, name"
        with self.store._conn() as c:
            rows = c.execute(q).fetchall()
        return [self._row(r) for r in rows]

    def deactivate(self, sku: str) -> None:
        with self.store._conn() as c:
            c.execute(
                "UPDATE catalog_items SET active=0, updated_at=? WHERE sku=?",
                (time.time(), sku),
            )
        self.sync_club_menu()

    def add_aliases(self, sku: str, extra: list[str]) -> None:
        item = self.get_item(sku)
        if not item:
            raise KeyError(sku)
        aliases = list(dict.fromkeys([*(item["aliases"] or []), *[a for a in extra if a]]))
        self.upsert_item(
            sku=item["sku"],
            name=item["name"],
            category=item["category"],
            weight=item["weight"],
            aliases=aliases,
        )

    def angles_present(self, sku: str) -> set[str]:
        return {
            (r.get("angle") or "").strip().lower()
            for r in self.list_refs(sku)
            if (r.get("angle") or "").strip().lower() in self.ANGLE_SLOTS
        }

    def missing_angles(self, sku: str) -> list[str]:
        have = self.angles_present(sku)
        return [a for a in self.ANGLE_SLOTS if a not in have]

    def next_angle_slot(self, sku: str) -> Optional[str]:
        missing = self.missing_angles(sku)
        return missing[0] if missing else None

    def sku_scan_complete(self, sku: str) -> bool:
        return not self.missing_angles(sku)

    def coverage_stats(self) -> dict:
        """Доля SKU с полным набором front/side/far."""
        items = self.list_items(active_only=True)
        total = len(items)
        complete = 0
        incomplete: list[str] = []
        for it in items:
            if self.sku_scan_complete(it["sku"]):
                complete += 1
            else:
                incomplete.append(it["sku"])
        ratio = (complete / total) if total else 0.0
        return {
            "total": total,
            "complete": complete,
            "ratio": ratio,
            "incomplete_skus": incomplete,
        }

    def add_reference_image(
        self,
        sku: str,
        image_path: Path,
        ocr_text: str = "",
        auto_alias_from_ocr: bool = True,
        angle: str = "",
    ) -> Path:
        item = self.get_item(sku)
        if not item:
            raise KeyError(f"SKU не найден: {sku}")
        ang = (angle or "").strip().lower()
        if ang and ang not in self.ANGLE_SLOTS:
            ang = ""
        if not ang:
            ang = self.next_angle_slot(sku) or "front"
        dest_dir = REFS_DIR / sku
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = image_path.suffix.lower() or ".jpg"
        dest = dest_dir / f"{ang}_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
        shutil.copy2(image_path, dest)
        with self.store._conn() as c:
            c.execute(
                "INSERT INTO catalog_refs(sku,path,ocr_text,angle,created_at) VALUES(?,?,?,?,?)",
                (sku, str(dest.relative_to(ROOT)), ocr_text, ang, time.time()),
            )
        if auto_alias_from_ocr and ocr_text:
            tokens = [t for t in ocr_text.lower().replace("ё", "е").split() if len(t) >= 3]
            if tokens:
                self.add_aliases(sku, tokens[:6])
            else:
                self.sync_club_menu()
        else:
            self.sync_club_menu()
        return dest

    def list_refs(self, sku: Optional[str] = None) -> list[dict]:
        with self.store._conn() as c:
            if sku:
                rows = c.execute(
                    "SELECT * FROM catalog_refs WHERE sku=? ORDER BY id ASC", (sku,)
                ).fetchall()
            else:
                rows = c.execute("SELECT * FROM catalog_refs ORDER BY id ASC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if "angle" not in d or d.get("angle") is None:
                d["angle"] = ""
            out.append(d)
        return out

    def all_allowed_aliases(self) -> list[str]:
        aliases: list[str] = []
        for item in self.list_items(active_only=True):
            aliases.extend(item.get("aliases") or [])
            # имя тоже как alias
            aliases.append(item["name"])
            aliases.append(item["sku"])
        # плюс seed yaml forbidden handled separately
        return [a for a in aliases if a]

    def sync_club_menu(self) -> None:
        """Пересобирает data/club_menu.yaml из каталога + forbidden из bar_catalog.yaml."""
        raw: dict[str, Any] = {}
        if CATALOG_YAML.exists():
            raw = yaml.safe_load(CATALOG_YAML.read_text(encoding="utf-8")) or {}

        allowed: list[str] = []
        seen = set()
        for item in self.list_items(active_only=True):
            for a in item.get("aliases") or []:
                key = str(a).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    allowed.append(str(a).strip())
            for extra in (item["name"], item["sku"]):
                key = str(extra).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    allowed.append(str(extra).strip())

        menu = {
            "allowed_brands": allowed,
            "forbidden_brands": list(raw.get("forbidden_brands") or []),
            "always_own_classes": list(raw.get("always_own_classes") or []),
            # для отладки / внешних тулов: какой объём у SKU бара
            "club_volumes": {
                item["sku"]: str(item.get("weight") or "")
                for item in self.list_items(active_only=True)
                if item.get("weight")
            },
            "club_max_liters": 0.65,
            "note": "Бар: 0.33 / 0.45 / 0.5 л. 1л и 2л = своё, даже если бренд знакомый.",
        }
        MENU_PATH.parent.mkdir(parents=True, exist_ok=True)
        MENU_PATH.write_text(
            yaml.safe_dump(menu, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def export_pack(self, dest_zip: Path, skus: Optional[list[str]] = None) -> Path:
        """
        Экспорт каталога в ZIP для переноса (ноут → Telegram → ПК клуба).
        Содержимое: catalog.json + images/<sku>/*.jpg
        """
        import zipfile

        dest_zip = Path(dest_zip)
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        items = self.list_items(active_only=True)
        if skus:
            want = {s.strip() for s in skus if s}
            items = [i for i in items if i["sku"] in want]

        payload = {
            "format": "cyberx_catalog_pack",
            "version": 2,
            "exported_at": time.time(),
            "products": [],
        }
        with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                refs = self.list_refs(item["sku"])
                entry = {
                    "sku": item["sku"],
                    "name": item["name"],
                    "category": item["category"],
                    "weight": item["weight"],
                    "aliases": item.get("aliases") or [],
                    "images": [],
                }
                for ref in refs:
                    src = ROOT / ref["path"] if not Path(ref["path"]).is_absolute() else Path(ref["path"])
                    if not src.exists():
                        continue
                    arc = f"images/{item['sku']}/{src.name}"
                    zf.write(src, arcname=arc)
                    entry["images"].append(
                        {
                            "file": arc,
                            "ocr_text": ref.get("ocr_text") or "",
                            "angle": ref.get("angle") or "",
                        }
                    )
                payload["products"].append(entry)
            zf.writestr(
                "catalog.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        return dest_zip

    def import_pack(self, zip_path: Path, merge: bool = True) -> dict:
        """
        Импорт ZIP-пакета каталога.
        merge=True — дополняет aliases/эталоны; False — перезаписывает aliases.
        Возвращает статистику {products, images, errors}.
        """
        import zipfile

        zip_path = Path(zip_path)
        stats = {"products": 0, "images": 0, "errors": []}
        if not zip_path.exists():
            raise FileNotFoundError(zip_path)

        tmp = ROOT / "data" / "_import_tmp" / uuid.uuid4().hex[:10]
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp)
            meta_path = tmp / "catalog.json"
            if not meta_path.exists():
                raise ValueError("В архиве нет catalog.json — это не пакет CyberX")
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if payload.get("format") not in (None, "cyberx_catalog_pack"):
                # всё равно пробуем, если есть products
                pass
            for p in payload.get("products") or []:
                try:
                    sku = str(p.get("sku", "")).strip()
                    name = str(p.get("name", sku)).strip()
                    if not sku or not name:
                        continue
                    aliases_in = [str(a) for a in (p.get("aliases") or []) if a]
                    existing = self.get_item(sku)
                    if existing and merge:
                        aliases = list(
                            dict.fromkeys([*(existing.get("aliases") or []), *aliases_in])
                        )
                    else:
                        aliases = aliases_in
                    self.upsert_item(
                        sku=sku,
                        name=name,
                        category=str(p.get("category") or "other"),
                        weight=str(p.get("weight") or ""),
                        aliases=aliases,
                        sync_menu=False,
                    )
                    stats["products"] += 1
                    for img in p.get("images") or []:
                        rel = img.get("file") or ""
                        src = tmp / rel
                        if not src.exists():
                            # иногда путь без images/
                            alt = tmp / "images" / sku / Path(rel).name
                            src = alt if alt.exists() else src
                        if not src.exists():
                            stats["errors"].append(f"нет файла: {rel}")
                            continue
                        self.add_reference_image(
                            sku,
                            src,
                            ocr_text=str(img.get("ocr_text") or ""),
                            auto_alias_from_ocr=False,
                            angle=str(img.get("angle") or ""),
                        )
                        stats["images"] += 1
                except Exception as exc:
                    stats["errors"].append(f"{p.get('sku')}: {exc}")
            self.sync_club_menu()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return stats

    @staticmethod
    def _row(row) -> dict:
        d = dict(row)
        try:
            d["aliases"] = json.loads(d.get("aliases_json") or "[]")
        except Exception:
            d["aliases"] = []
        return d
