"""
Abstract LayerLoader: fetch-with-cache → clip → normalize → idempotent write.
All layer loaders subclass this.
"""
from __future__ import annotations

import hashlib
import logging
import os
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from geo.db import bulk_upsert_features

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("GEO_CACHE_DIR", Path.home() / ".osint_cop" / "geo_cache"))


class LayerLoader(ABC):
    layer_name: str = ""

    def __init__(self, theater_id: str, conn):
        self.theater_id = theater_id
        self.conn = conn
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def load(self, bbox: tuple[float, float, float, float], as_of: str | None = None) -> int:
        """Full pipeline: fetch → clip → normalize → write. Returns feature count."""
        raw_path = self._fetch(bbox)
        features = self._normalize(raw_path, bbox)
        return bulk_upsert_features(
            self.conn, self.theater_id, self.layer_name,
            features, as_of=as_of, source=self.layer_name,
        )

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        suffix = Path(url).suffix or ".bin"
        return CACHE_DIR / f"{self.layer_name}_{digest}{suffix}"

    def _fetch(self, bbox: tuple) -> Path:
        """Download to cache if not already present. Returns local path."""
        url = self._get_url(bbox)
        path = self._cache_path(url)
        if path.exists():
            logger.info("Cache hit: %s", path)
            return path
        logger.info("Downloading %s → %s", url, path)
        urllib.request.urlretrieve(url, path)
        return path

    @abstractmethod
    def _get_url(self, bbox: tuple) -> str:
        """Return the download URL for the given bbox."""
        ...

    @abstractmethod
    def _normalize(self, raw_path: Path, bbox: tuple) -> Iterable[dict]:
        """Yield feature dicts with 'geom_wkt' and 'properties'."""
        ...
