"""
Pinned 384-d embedder (all-MiniLM-L6-v2 via sentence-transformers).
Fails loud if the produced vector is not exactly 384-d.

The heavy import (sentence_transformers / torch) is lazy so that the eval harness
and the ingest contract import this module without pulling in the model stack.
For hermetic CI, DeterministicEmbedder provides stable vectors with no model.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

_CFG = Path(__file__).parent.parent / "config" / "embedding.yaml"


def _cfg() -> dict:
    with open(_CFG) as f:
        return yaml.safe_load(f)["embedding"]


class MiniLMEmbedder:
    """Production embedder. Loads all-MiniLM-L6-v2 on first use."""

    def __init__(self):
        cfg = _cfg()
        self._dim = int(cfg["dim"])
        self._model_name = cfg["model_name"]
        self._model = None  # lazy

    @property
    def dim(self) -> int:
        return self._dim

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy/heavy
            self._model = SentenceTransformer(self._model_name)

    def embed(self, text: str) -> tuple[float, ...]:
        self._ensure()
        vec = self._model.encode(text, normalize_embeddings=True)
        out = tuple(float(x) for x in vec)
        if len(out) != self._dim:
            raise ValueError(
                f"Embedding dim {len(out)} != configured {self._dim} (fail loud — "
                "config/embedding.yaml and the model disagree)"
            )
        return out


class DeterministicEmbedder:
    """Hermetic test embedder: stable pseudo-vectors from a text hash. No model, no GPU.

    Cosine similarity between two of these is meaningless, so the synthetic eval corpus
    is designed to rely on trigram/toponym text scoring, not embeddings (alpha_text blend
    degrades gracefully when embeddings are absent). This class exists only to exercise
    the embedding code path deterministically where a vector is required.
    """

    def __init__(self, dim: int = 384):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> tuple[float, ...]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        # expand the 32-byte digest to dim floats in [-1, 1], deterministic
        vals = []
        i = 0
        while len(vals) < self._dim:
            b = seed[i % len(seed)]
            vals.append((b / 127.5) - 1.0)
            i += 1
        # L2 normalize
        norm = sum(v * v for v in vals) ** 0.5 or 1.0
        return tuple(v / norm for v in vals)
