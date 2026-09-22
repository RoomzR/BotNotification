"""Few-shot matching кропа с эталонами каталога (Pic2Product-style).

Primary: OpenCLIP ViT-B-32.
Fallback: torchvision MobileNetV3-Small.
Index: FAISS IndexFlatIP если доступен, иначе numpy matmul.
Match: max score per SKU, затем margin ко 2-му SKU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / "data" / "catalog_embeddings.npz"


@dataclass
class EmbedHit:
    sku: str
    score: float
    second: float
    method: str


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v) + 1e-8)
    return (v / n).astype(np.float32)


class EmbeddingMatcher:
    def __init__(
        self,
        club_min_score: float = 0.28,
        own_if_below: float = 0.22,
        margin: float = 0.04,
        enabled: bool = True,
    ):
        self.club_min_score = float(club_min_score)
        self.own_if_below = float(own_if_below)
        self.margin = float(margin)
        self.enabled = bool(enabled)
        self._backend: Optional[str] = None
        self._model = None
        self._preprocess = None
        self._device = "cpu"
        self._skus: List[str] = []
        self._vecs: Optional[np.ndarray] = None  # (N, D)
        self._faiss = None
        self._failed = False

    def invalidate(self) -> None:
        self._skus = []
        self._vecs = None
        self._faiss = None
        try:
            if CACHE_PATH.exists():
                CACHE_PATH.unlink()
        except Exception:
            pass

    def _ensure_backend(self) -> bool:
        if self._failed or not self.enabled:
            return False
        if self._backend is not None:
            return True
        try:
            import torch

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            self._failed = True
            return False

        try:
            import open_clip
            import torch

            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="openai"
            )
            model.eval()
            model.to(self._device)
            self._model = model
            self._preprocess = preprocess
            self._backend = "open_clip"
            logger.info("EmbeddingMatcher: OpenCLIP ViT-B-32 on %s", self._device)
            return True
        except Exception as exc:
            logger.info("OpenCLIP недоступен (%s), пробуем MobileNetV3", exc)

        try:
            import torch
            import torch.nn as nn
            from torchvision import models, transforms

            weights = models.MobileNet_V3_Small_Weights.DEFAULT
            net = models.mobilenet_v3_small(weights=weights)
            net.classifier = nn.Identity()
            net.eval()
            net.to(self._device)
            self._model = net
            mean = (0.485, 0.456, 0.406)
            std = (0.229, 0.224, 0.225)
            try:
                meta = getattr(weights, "meta", None) or {}
                if meta.get("mean") and meta.get("std"):
                    mean, std = tuple(meta["mean"]), tuple(meta["std"])
            except Exception:
                pass
            self._preprocess = transforms.Compose(
                [
                    transforms.ToPILImage(),
                    transforms.Resize((224, 224)),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=mean, std=std),
                ]
            )
            self._backend = "mobilenet"
            logger.info("EmbeddingMatcher: MobileNetV3-Small on %s", self._device)
            return True
        except Exception as exc:
            logger.warning("EmbeddingMatcher недоступен: %s", exc)
            self._failed = True
            return False

    def embed_bgr(self, bgr: np.ndarray) -> Optional[np.ndarray]:
        if bgr is None or bgr.size == 0:
            return None
        if not self._ensure_backend():
            return None
        import torch

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        try:
            if self._backend == "open_clip":
                from PIL import Image

                img = Image.fromarray(rgb)
                t = self._preprocess(img).unsqueeze(0).to(self._device)
                with torch.no_grad():
                    feat = self._model.encode_image(t)
                    feat = feat / feat.norm(dim=-1, keepdim=True)
                return feat.squeeze(0).detach().cpu().numpy().astype(np.float32)
            t = self._preprocess(rgb).unsqueeze(0).to(self._device)
            with torch.no_grad():
                feat = self._model(t)
            return _l2_normalize(feat.squeeze(0).detach().cpu().numpy())
        except Exception as exc:
            logger.debug("embed fail: %s", exc)
            return None

    def _rebuild_faiss(self) -> None:
        self._faiss = None
        if self._vecs is None or not len(self._vecs):
            return
        try:
            import faiss

            index = faiss.IndexFlatIP(int(self._vecs.shape[1]))
            index.add(self._vecs.astype(np.float32))
            self._faiss = index
            logger.info("FAISS IndexFlatIP ready: %s vectors", len(self._skus))
        except Exception as exc:
            logger.debug("FAISS unavailable (%s), using numpy", exc)
            self._faiss = None

    def build_gallery(self, refs_by_sku: Dict[str, List[str]], force: bool = False) -> int:
        if not self.enabled:
            return 0
        if not force and CACHE_PATH.exists() and self._vecs is None:
            try:
                data = np.load(CACHE_PATH, allow_pickle=True)
                self._skus = list(data["skus"].tolist())
                self._vecs = data["vecs"].astype(np.float32)
                if self._vecs is not None and len(self._skus) == len(self._vecs):
                    self._rebuild_faiss()
                    logger.info("Embedding gallery loaded: %s vectors", len(self._skus))
                    return len(self._skus)
            except Exception as exc:
                logger.debug("gallery cache load fail: %s", exc)

        if not self._ensure_backend():
            return 0

        skus: List[str] = []
        vecs: List[np.ndarray] = []
        for sku, rels in (refs_by_sku or {}).items():
            for rel in rels:
                p = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
                if not p.exists():
                    continue
                img = cv2.imread(str(p))
                if img is None:
                    continue
                emb = self.embed_bgr(img)
                if emb is None:
                    continue
                skus.append(sku)
                vecs.append(emb)

        if not vecs:
            self._skus = []
            self._vecs = None
            self._faiss = None
            try:
                if CACHE_PATH.exists():
                    CACHE_PATH.unlink()
            except Exception:
                pass
            return 0

        self._skus = skus
        self._vecs = np.stack(vecs, axis=0).astype(np.float32)
        self._rebuild_faiss()
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                CACHE_PATH,
                skus=np.array(self._skus, dtype=object),
                vecs=self._vecs,
                backend=np.array(self._backend or ""),
            )
        except Exception as exc:
            logger.debug("gallery cache save fail: %s", exc)
        logger.info(
            "Embedding gallery built: %s vectors / %s sku",
            len(self._skus),
            len(set(self._skus)),
        )
        return len(self._skus)

    def _raw_sims(self, emb: np.ndarray) -> np.ndarray:
        if self._faiss is not None and self._vecs is not None:
            try:
                # search all
                k = len(self._skus)
                scores, _idx = self._faiss.search(emb.reshape(1, -1).astype(np.float32), k)
                # reorder to match sku order via index
                full = np.full(len(self._skus), -1.0, dtype=np.float32)
                for score, i in zip(scores[0], _idx[0]):
                    if i >= 0:
                        full[int(i)] = float(score)
                return full
            except Exception:
                pass
        assert self._vecs is not None
        return (self._vecs @ emb).astype(np.float32)

    def match(
        self, crop_bgr: np.ndarray, refs_by_sku: Optional[Dict[str, List[str]]] = None
    ) -> Optional[EmbedHit]:
        if not self.enabled:
            return None
        if self._vecs is None or not len(self._skus):
            if refs_by_sku:
                self.build_gallery(refs_by_sku)
            if self._vecs is None or not len(self._skus):
                return None
        emb = self.embed_bgr(crop_bgr)
        if emb is None:
            return None
        sims = self._raw_sims(emb)
        # aggregate max per SKU
        best_by_sku: Dict[str, float] = {}
        for sku, s in zip(self._skus, sims):
            prev = best_by_sku.get(sku)
            if prev is None or float(s) > prev:
                best_by_sku[sku] = float(s)
        if not best_by_sku:
            return None
        ranked = sorted(best_by_sku.items(), key=lambda x: -x[1])
        best_sku, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        method = self._backend or "embed"
        if self._faiss is not None:
            method = f"{method}+faiss"
        return EmbedHit(best_sku, best, second, method)

    def is_club_hit(self, hit: Optional[EmbedHit]) -> bool:
        if hit is None:
            return False
        if hit.score < self.club_min_score:
            return False
        if (hit.score - hit.second) < self.margin and hit.second > 0:
            return hit.score >= self.club_min_score + 0.05
        return True
