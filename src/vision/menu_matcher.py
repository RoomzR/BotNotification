"""Распознавание бренда: embedding-галерея + OCR + каталог + эталоны + объём."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
import yaml

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MENU_PATH = ROOT / "data" / "club_menu.yaml"
PLATFORM_PATH = ROOT / "platform.yaml"

# Бар CyberX: 0.33 / 0.45 / 0.5 л. Всё от ~0.9 л (1л, 1.5л, 2л) — «своё».
CLUB_MAX_LITERS = 0.65
LARGE_MIN_LITERS = 0.9
LARGE_BOTTLE_PERSON_RATIO = 0.32
DRINK_CATEGORIES = {"drink", "energy", "alcohol"}
# COCO labels + retail product
BOTTLE_LABELS = {"bottle", "wine_glass", "wine glass", "cup", "product"}
# Только то, что гость реально «несёт как еду/напиток» — без столовых приборов
HELD_PRODUCT_LABELS = {
    "bottle",
    "wine_glass",
    "wine glass",
    "cup",
    "bowl",
    "product",
    *{
        "banana",
        "apple",
        "sandwich",
        "orange",
        "broccoli",
        "carrot",
        "hot_dog",
        "pizza",
        "donut",
        "cake",
    },
}
# Крупный PET: только bottle/product (не бокал/стакан)
SIZE_GATE_LABELS = {"bottle", "product"}


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^a-z0-9а-я&+.,]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_volume_liters(text: str) -> Optional[float]:
    if not text:
        return None
    t = str(text).lower().replace(",", ".").replace("ё", "е")
    t = unicodedata.normalize("NFKD", t)

    for m in re.finditer(r"(\d{2,4}(?:\.\d+)?)\s*(?:ml|мл)\b", t):
        ml = float(m.group(1))
        if 50 <= ml <= 5000:
            return round(ml / 1000.0, 3)

    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:л|l|литр(?:а|ов)?)\b", t):
        lit = float(m.group(1))
        if 0.05 <= lit <= 5.0:
            return round(lit, 3)

    for m in re.finditer(r"(?<!\d)(0\.33|0\.330|0\.45|0\.5|0\.50|1\.0|1\.5|2\.0|1|2)(?!\d)", t):
        raw = m.group(1)
        lit = float(raw)
        if raw in ("1", "2") and not re.search(r"(л|l|литр|ml|мл|pet|бутыл)", t):
            continue
        if 0.05 <= lit <= 5.0:
            return round(lit, 3)

    return None


def volumes_compatible(ocr_vol: float, catalog_vol: Optional[float]) -> bool:
    if catalog_vol is None:
        return ocr_vol < LARGE_MIN_LITERS
    if ocr_vol >= LARGE_MIN_LITERS and catalog_vol <= CLUB_MAX_LITERS:
        return False
    return abs(ocr_vol - catalog_vol) <= 0.2


@dataclass
class BrandVerdict:
    status: str  # club | own | unknown
    brand: str
    ocr_text: str
    reason: str
    certainty: str = "confirmed"  # confirmed | suspicious


class MenuMatcher:
    def __init__(self, menu_path: Path = MENU_PATH):
        self.menu_path = menu_path
        self.allowed: Set[str] = set()
        self.forbidden: Set[str] = set()
        self.always_own: Set[str] = set()
        self._ocr = None
        self._ocr_failed = False
        self._template = None
        self._embed = None
        self._sku_by_alias: Dict[str, str] = {}
        self._refs_by_sku: Dict[str, List[str]] = {}
        self._weight_by_sku: Dict[str, str] = {}
        self._category_by_sku: Dict[str, str] = {}
        self.alert_on_unknown_held = True
        self.club_min_score = 0.28
        self.own_if_below = 0.22
        self.reload()

    def _load_vision_cfg(self) -> dict:
        try:
            if PLATFORM_PATH.exists():
                raw = yaml.safe_load(PLATFORM_PATH.read_text(encoding="utf-8")) or {}
                return raw.get("vision", {}) or {}
        except Exception:
            pass
        return {}

    def reload(self) -> None:
        raw = {}
        if self.menu_path.exists():
            with open(self.menu_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        self.allowed = {_norm(x) for x in raw.get("allowed_brands", []) if x}
        self.forbidden = {_norm(x) for x in raw.get("forbidden_brands", []) if x}
        self.always_own = {str(x) for x in raw.get("always_own_classes", [])}

        vcfg = self._load_vision_cfg()
        own_cfg = vcfg.get("own_products", {}) or {}
        emb_cfg = vcfg.get("embedding", {}) or {}
        self.alert_on_unknown_held = bool(own_cfg.get("alert_on_unknown_held", True))
        self.club_min_score = float(emb_cfg.get("club_min_score", 0.28))
        self.own_if_below = float(emb_cfg.get("own_if_below", 0.22))

        try:
            from src.catalog.service import CatalogService

            cat = CatalogService()
            self._sku_by_alias = {}
            self._weight_by_sku = {}
            self._category_by_sku = {}
            for item in cat.list_items(active_only=True):
                sku = item["sku"]
                self._weight_by_sku[sku] = str(item.get("weight") or "")
                self._category_by_sku[sku] = str(item.get("category") or "")
                for a in item.get("aliases") or []:
                    na = _norm(a)
                    if len(na) < 2:
                        continue
                    self._sku_by_alias[na] = sku
                    self.allowed.add(na)
                self._sku_by_alias[_norm(item["name"])] = sku
                self._sku_by_alias[_norm(item["sku"])] = sku
                self.allowed.add(_norm(item["name"]))
            self._refs_by_sku = {}
            for ref in cat.list_refs():
                self._refs_by_sku.setdefault(ref["sku"], []).append(ref["path"])
            if self._template is not None:
                self._template.invalidate()
            # всегда сбрасываем кэш галереи при смене refs
            if self._embed is None:
                self._get_embed()
            else:
                self._embed.invalidate()
            emb = self._get_embed()
            emb.club_min_score = self.club_min_score
            emb.own_if_below = self.own_if_below
            emb.margin = float(emb_cfg.get("margin", 0.04))
            if emb_cfg.get("enabled", True):
                emb.build_gallery(self._refs_by_sku, force=True)
        except Exception as exc:
            logger.warning("catalog reload failed: %s", exc)

    def _get_ocr(self):
        if self._ocr_failed:
            return None
        if self._ocr is not None:
            return self._ocr
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._ocr = RapidOCR()
            logger.info("RapidOCR загружен для сверки с меню")
            return self._ocr
        except Exception as exc:
            logger.warning("OCR недоступен (%s). Будет упрощённый режим.", exc)
            self._ocr_failed = True
            return None

    def _get_template(self):
        if self._template is None:
            from .template_matcher import TemplateMatcher

            self._template = TemplateMatcher()
        return self._template

    def _get_embed(self):
        if self._embed is None:
            from .embedding_matcher import EmbeddingMatcher

            vcfg = self._load_vision_cfg().get("embedding", {}) or {}
            self._embed = EmbeddingMatcher(
                club_min_score=float(vcfg.get("club_min_score", self.club_min_score)),
                own_if_below=float(vcfg.get("own_if_below", self.own_if_below)),
                margin=float(vcfg.get("margin", 0.04)),
                enabled=bool(vcfg.get("enabled", True)),
            )
        return self._embed

    def read_text(self, bgr_crop: np.ndarray) -> str:
        if bgr_crop is None or bgr_crop.size == 0:
            return ""
        h, w = bgr_crop.shape[:2]
        if h < 12 or w < 12:
            return ""
        scale = max(1.0, 160 / max(h, w))
        if scale > 1.01:
            bgr_crop = cv2.resize(
                bgr_crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC
            )
        ocr = self._get_ocr()
        if ocr is None:
            return ""
        try:
            result, _ = ocr(bgr_crop)
        except Exception:
            return ""
        if not result:
            return ""
        parts: List[str] = []
        for line in result:
            if len(line) >= 2 and line[1]:
                parts.append(str(line[1]))
        return _norm(" ".join(parts))

    def _match_list(self, text: str, brands: Set[str]) -> Optional[str]:
        if not text:
            return None
        compact = text.replace(" ", "")
        # длинные aliases сначала — меньше ложных substring-hit
        ordered = sorted((b for b in brands if b), key=len, reverse=True)
        for b in ordered:
            if len(b) <= 2:
                # короткие только как целое слово
                if re.search(rf"(?:^|\s){re.escape(b)}(?:$|\s)", text):
                    return b
                continue
            if b in text or b.replace(" ", "") in compact:
                return b
        return None

    def _catalog_volume(self, sku: str) -> Optional[float]:
        return parse_volume_liters(self._weight_by_sku.get(sku, ""))

    def _bottle_person_ratio(
        self,
        item_wh: Optional[Tuple[int, int]],
        person_wh: Optional[Tuple[int, int]],
    ) -> Optional[float]:
        if not item_wh or not person_wh:
            return None
        item_h = max(1, int(item_wh[1]))
        person_h = max(1, int(person_wh[1]))
        return item_h / person_h

    def _apply_volume_gate(
        self,
        verdict: BrandVerdict,
        *,
        label: str,
        ocr_text: str,
        item_wh: Optional[Tuple[int, int]] = None,
        person_wh: Optional[Tuple[int, int]] = None,
    ) -> BrandVerdict:
        text = ocr_text or verdict.ocr_text or ""
        ocr_vol = parse_volume_liters(text)
        sku = verdict.brand if verdict.status == "club" else ""
        cat_vol = self._catalog_volume(sku) if sku else None
        ratio = self._bottle_person_ratio(item_wh, person_wh)
        lab = (label or "").lower().replace(" ", "_")
        looks_bottle = lab in SIZE_GATE_LABELS or lab == "bottle"

        if ocr_vol is not None and ocr_vol >= LARGE_MIN_LITERS:
            if verdict.status == "club" and not volumes_compatible(ocr_vol, cat_vol):
                return BrandVerdict(
                    "own",
                    sku or f"{ocr_vol}л",
                    text,
                    f"объём {ocr_vol}л — в баре только до ~0.5л (SKU {sku or '?'} = {cat_vol or '?'}л)",
                )
            if verdict.status != "club":
                return BrandVerdict(
                    "own",
                    f"{ocr_vol}л",
                    text,
                    f"крупная бутылка {ocr_vol}л — такого объёма нет в баре CyberX",
                )

        if verdict.status == "club" and ocr_vol is not None and cat_vol is not None:
            if not volumes_compatible(ocr_vol, cat_vol):
                return BrandVerdict(
                    "own",
                    sku,
                    text,
                    f"объём {ocr_vol}л ≠ барный {cat_vol}л ({sku})",
                )

        if looks_bottle and ratio is not None and ratio >= LARGE_BOTTLE_PERSON_RATIO:
            if verdict.status == "club":
                if cat_vol is None or cat_vol <= CLUB_MAX_LITERS:
                    return BrandVerdict(
                        "own",
                        sku or "bottle",
                        text,
                        f"крупная бутылка (~{ratio:.0%} роста человека) — похоже на 1–2л, не формат бара",
                        certainty="suspicious",
                    )
            elif verdict.status in ("unknown", "own"):
                return BrandVerdict(
                    "own",
                    "bottle",
                    text,
                    f"крупная бутылка (~{ratio:.0%} роста) — вероятно 1–2л «со своим»",
                    certainty="suspicious",
                )

        return verdict

    def classify_item(
        self,
        label: str,
        bgr_crop: np.ndarray,
        hinted_brand: Optional[str] = None,
        item_wh: Optional[Tuple[int, int]] = None,
        person_wh: Optional[Tuple[int, int]] = None,
    ) -> BrandVerdict:
        lab = (label or "").lower()
        if label in self.always_own or lab in self.always_own:
            return BrandVerdict(
                "own",
                label,
                "",
                f"«{label}» нет в меню клуба (свежая еда/не из бара)",
            )

        def gate(v: BrandVerdict, ocr: str = "") -> BrandVerdict:
            return self._apply_volume_gate(
                v, label=label, ocr_text=ocr, item_wh=item_wh, person_wh=person_wh
            )

        # 1) brand-eye hint — чужой сразу own; клуб только как prior (нужен visual/OCR)
        hint_club_sku: Optional[str] = None
        if hinted_brand:
            hb = _norm(hinted_brand)
            bad = self._match_list(hb, self.forbidden)
            if bad:
                return BrandVerdict("own", bad, hb, f"чужой бренд (модель): {bad}")
            good = self._match_list(hb, self.allowed)
            if good:
                hint_club_sku = self._sku_by_alias.get(good, good)

        # 2) embedding gallery (главный visual path)
        embed_score: Optional[float] = None
        try:
            emb = self._get_embed()
            hit = emb.match(bgr_crop, self._refs_by_sku)
            if hit is not None:
                embed_score = hit.score
            if hit is not None and emb.is_club_hit(hit):
                ocr_for_vol = self.read_text(bgr_crop)
                return gate(
                    BrandVerdict(
                        "club",
                        hit.sku,
                        ocr_for_vol,
                        f"embedding {hit.method} {hit.score:.2f}: {hit.sku}",
                    ),
                    ocr_for_vol,
                )
            # embedding уже сказал «не похоже на бар» — не доверяем слабому hist/ORB
            if hit is not None and hit.score < self.own_if_below:
                ocr_text = self.read_text(bgr_crop)
                bad = self._match_list(ocr_text, self.forbidden)
                if bad:
                    return BrandVerdict("own", bad, ocr_text, f"чужой бренд: {bad}")
                good = self._match_list(ocr_text, self.allowed)
                if good:
                    sku = self._sku_by_alias.get(good, good)
                    return gate(
                        BrandVerdict("club", sku, ocr_text, f"продукция клуба: {sku}"),
                        ocr_text,
                    )
                early = gate(
                    BrandVerdict("unknown", label, ocr_text, "этикетка не читается"),
                    ocr_text,
                )
                if early.status == "own":
                    return early
                held = lab.replace(" ", "_") in {
                    x.replace(" ", "_") for x in HELD_PRODUCT_LABELS
                } or lab in HELD_PRODUCT_LABELS
                if self.alert_on_unknown_held and held:
                    return BrandVerdict(
                        "own",
                        label,
                        ocr_text,
                        f"упаковка у гостя, не похожа на бар (embed {hit.score:.2f})",
                        certainty="suspicious",
                    )
                return early
        except Exception as exc:
            logger.debug("embed match fail: %s", exc)

        # 3) ORB/hist templates — только если embed отсутствует или почти club
        allow_template = embed_score is None or embed_score >= self.club_min_score
        if self._refs_by_sku and allow_template:
            try:
                hits = self._get_template().match_all(bgr_crop, self._refs_by_sku)
                for hit in hits:
                    if hit.method == "hist" and hit.score < 0.82:
                        continue
                    if hit.method == "orb" and hit.score < 0.55:
                        continue
                    if hit.score < 0.55:
                        continue
                    ocr_for_vol = self.read_text(bgr_crop)
                    return gate(
                        BrandVerdict(
                            "club",
                            hit.sku,
                            ocr_for_vol,
                            f"эталон каталога ({hit.method} {hit.score:.2f}): {hit.sku}",
                        ),
                        ocr_for_vol,
                    )
            except Exception as exc:
                logger.debug("template match fail: %s", exc)

        # hint club только после visual miss — как suspicious soft club? лучше require OCR
        # (не silent club)
        if hint_club_sku:
            ocr_text = self.read_text(bgr_crop)
            good = self._match_list(ocr_text, self.allowed)
            if good:
                sku = self._sku_by_alias.get(good, good)
                return gate(
                    BrandVerdict("club", sku, ocr_text, f"продукция клуба: {sku}"),
                    ocr_text,
                )

        # 4) OCR
        ocr_text = self.read_text(bgr_crop)
        bad = self._match_list(ocr_text, self.forbidden)
        if bad:
            return BrandVerdict("own", bad, ocr_text, f"чужой бренд: {bad}")

        good = self._match_list(ocr_text, self.allowed)
        if good:
            sku = self._sku_by_alias.get(good, good)
            return gate(
                BrandVerdict("club", sku, ocr_text, f"продукция клуба: {sku}"),
                ocr_text,
            )

        # 5) volume / size heuristics
        early = gate(BrandVerdict("unknown", label, ocr_text, "этикетка не читается"), ocr_text)
        if early.status == "own":
            return early

        if len(ocr_text) >= 3:
            return BrandVerdict(
                "own",
                ocr_text[:40],
                ocr_text,
                f"бренд не из меню клуба (OCR: {ocr_text[:40]})",
                certainty="suspicious",
            )

        # 6) held product без club-match → своё/подозрительно (больше не молчим)
        held = lab.replace(" ", "_") in {x.replace(" ", "_") for x in HELD_PRODUCT_LABELS} or lab in HELD_PRODUCT_LABELS
        if self.alert_on_unknown_held and held:
            # слабый embedding score тоже сигнал
            try:
                emb = self._get_embed()
                hit = emb.match(bgr_crop, self._refs_by_sku)
                if hit is not None and hit.score < self.own_if_below:
                    return BrandVerdict(
                        "own",
                        label,
                        ocr_text,
                        f"упаковка у гостя, не похожа на бар (embed {hit.score:.2f})",
                        certainty="suspicious",
                    )
            except Exception:
                pass
            return BrandVerdict(
                "own",
                label,
                ocr_text,
                f"упаковка/еда у гостя без совпадения с меню бара ({label})",
                certainty="suspicious",
            )

        return early
