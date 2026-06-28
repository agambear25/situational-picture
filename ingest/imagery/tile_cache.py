"""
Local file-based tile cache for raw imagery tiles (Phase 3b).

This is SEPARATE from ml.detection_cache (which stores post-detector CachedDetections).
This cache stores raw Tile objects BEFORE any detector runs, so expensive GEE downloads
are never repeated within or across runs.

Key: sha256(granule_id | source_prefix) — stable, no clock, no random.
Value: pickled Tile object (data: bytes + frozen dataclass, safe to pickle).
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Optional

from ingest.imagery.framework import Tile


def _tile_key(granule_id: str, source_prefix: str) -> str:
    return hashlib.sha256(f"{granule_id}|{source_prefix}".encode()).hexdigest()


class TileCache:
    """Stores raw Tile objects on disk. Cache hits avoid re-downloading from GEE.

    An empty directory hit (no .pkl files) is a cold cache — len() == 0 is not a sentinel
    for 'this granule has no data'; a missing key is. The cache never writes a sentinel for
    empty tiles because the acquisition layer decides what granules to request.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, granule_id: str, source_prefix: str) -> Path:
        return self._root / (_tile_key(granule_id, source_prefix) + ".pkl")

    def get(self, granule_id: str, source_prefix: str) -> Optional[Tile]:
        p = self._path(granule_id, source_prefix)
        if not p.exists():
            return None
        with p.open("rb") as f:
            return pickle.load(f)

    def put(self, tile: Tile, source_prefix: str) -> None:
        p = self._path(tile.granule_id, source_prefix)
        with p.open("wb") as f:
            pickle.dump(tile, f)

    def has(self, granule_id: str, source_prefix: str) -> bool:
        return self._path(granule_id, source_prefix).exists()

    def __len__(self) -> int:
        return len(list(self._root.glob("*.pkl")))

    def __bool__(self) -> bool:
        return True  # prevent an empty cache from being falsy in 'cache or default' patterns
