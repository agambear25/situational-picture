"""
The determinism contract for imagery detectors (mandatory for any neural detector).

Three primitives keep imagery replay bit-identical (invariant #5):

  1. tile_hash(tile)        — a stable hash of the input tile, so identical input always keys
                              the same; replay can prove it reused the same granule.
  2. quantize_self_conf(s)  — discretize a model score into a band (+ a representative value),
                              so small float drift in a neural score can NEVER change the
                              emitted Observation.
  3. DetectionCache         — keyed by (tile_hash, model_digest, detector); a cache hit reuses
                              cached detections and never re-runs the model or re-hits GEE.

Cached detections are COARSENED — cell_id + banded confidence only, never a precise coordinate
(invariants #2/#3). On a cache hit we rebuild a RawObservation from the cell centroid, which
normalize() snaps straight back to the same cell, so no precision is ever reintroduced.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

from ingest.contract import RawObservation
from ingest.imagery.framework import Tile

# Confidence bands: (name, lower_threshold, representative_value). Highest matching band wins.
# A raw score anywhere inside a band collapses to its representative value, so a neural model's
# 0.78 vs 0.81 both become 'high' → 0.85, and replay can't diverge on float noise.
CONF_BANDS: tuple[tuple[str, float, float], ...] = (
    ("very_high", 0.85, 0.95),
    ("high",      0.70, 0.85),
    ("medium",    0.50, 0.65),
    ("low",       0.30, 0.45),
    ("very_low",  0.00, 0.20),
)


def quantize_self_conf(score: float) -> tuple[str, float]:
    s = max(0.0, min(1.0, float(score)))
    for name, thr, rep in CONF_BANDS:
        if s >= thr:
            return name, rep
    return CONF_BANDS[-1][0], CONF_BANDS[-1][2]


def tile_hash(tile: Tile) -> str:
    h = hashlib.sha256()
    h.update(tile.granule_id.encode())
    h.update(tile.data)
    return h.hexdigest()


@dataclass(frozen=True)
class DetectionKey:
    tile_hash: str
    model_digest: str
    detector: str

    def digest(self) -> str:
        return hashlib.sha256(
            f"{self.tile_hash}|{self.model_digest}|{self.detector}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class CachedDetection:
    """A COARSENED detection — the only form ever persisted. No precise lon/lat, no raw logit."""
    obs_type: str
    cell_id: str
    self_conf_band: str
    self_conf: float
    occurred_start: str   # ISO
    occurred_end: str     # ISO
    text: str = ""
    meta: dict = field(default_factory=dict)


class DetectionCache(Protocol):
    def get(self, key: DetectionKey) -> Optional[list[CachedDetection]]: ...
    def put(self, key: DetectionKey, dets: list[CachedDetection]) -> None: ...


class InMemoryDetectionCache:
    """Hermetic cache (tests / a single run). A present key with [] means 'tile had no
    detections' — a real hit, distinct from a miss (None)."""

    def __init__(self):
        self._d: dict[str, list[CachedDetection]] = {}

    def get(self, key: DetectionKey) -> Optional[list[CachedDetection]]:
        return self._d.get(key.digest())

    def put(self, key: DetectionKey, dets: list[CachedDetection]) -> None:
        self._d[key.digest()] = list(dets)


class PgDetectionCache:
    """Production cache over ml.detection_cache (0008). Lazy psycopg2. Stores ONLY coarsened
    detections. (Empty-tile hits would need a sentinel row — a 3h refinement; documented.)"""

    def __init__(self, conn):
        self._conn = conn

    def get(self, key: DetectionKey) -> Optional[list[CachedDetection]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT obs_type, cell_id, self_conf_band, self_conf, occurred_start, "
                "occurred_end, text, meta FROM ml.detection_cache WHERE cache_key = %s",
                (key.digest(),),
            )
            rows = cur.fetchall()
        if not rows:
            return None
        return [CachedDetection(obs_type=r[0], cell_id=r[1], self_conf_band=r[2], self_conf=r[3],
                                occurred_start=r[4], occurred_end=r[5], text=r[6] or "",
                                meta=r[7] or {}) for r in rows]

    def put(self, key: DetectionKey, dets: list[CachedDetection]) -> None:
        import json
        with self._conn.cursor() as cur:
            for cd in dets:
                cur.execute(
                    """
                    INSERT INTO ml.detection_cache
                        (cache_key, tile_hash, model_digest, detector, obs_type, self_conf_band,
                         self_conf, cell_id, occurred_start, occurred_end, text, meta)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (key.digest(), key.tile_hash, key.model_digest, key.detector, cd.obs_type,
                     cd.self_conf_band, cd.self_conf, cd.cell_id, cd.occurred_start,
                     cd.occurred_end, cd.text, json.dumps(cd.meta, sort_keys=True)),
                )
        self._conn.commit()


def coarsen_detection(raw: RawObservation, resolver, theater_id: str) -> Optional[CachedDetection]:
    """Resolve a detector's precise RawObservation to a cell and BAND its confidence (once).

    Returns None if it cannot be placed — the caller writes an obs_rejection (never a silent
    drop, invariant #1). This is the single point where the raw model score is banded.
    """
    res = resolver.resolve_to_cell(
        raw.geo.lon, raw.geo.lat, raw.geo.precision_m, raw.geo.place_name, theater_id
    )
    if res is None:
        return None
    band, conf = quantize_self_conf(raw.self_conf if raw.self_conf is not None else 0.0)
    return CachedDetection(
        obs_type=raw.obs_type, cell_id=res.cell.cell_id, self_conf_band=band, self_conf=conf,
        occurred_start=raw.occurred_start.isoformat(), occurred_end=raw.occurred_end.isoformat(),
        text=raw.text, meta={**raw.meta, "self_conf_band": band},
    )


def cached_detect(detector, tiles, cache: DetectionCache, resolver,
                  theater_id: str) -> tuple[list[CachedDetection], list[str]]:
    """Run a detector with replay-safe caching. Returns (coarsened detections, rejection reasons).

    Cache miss → infer + coarsen (resolve the PRECISE pixel coord to its cell once, band the
    score once) + store; cache hit → reuse (no model, no GEE). Detections are returned in their
    coarsened form (authoritative cell_id) — the write path pins that cell via
    detections_to_observations, never re-deriving it from a coordinate.
    """
    out: list[CachedDetection] = []
    rejected: list[str] = []
    for tile in tiles:
        key = DetectionKey(tile_hash(tile), detector.model_digest, detector.name)
        cached = cache.get(key)
        if cached is None:
            cached = []
            for raw in detector.infer([tile]):
                cd = coarsen_detection(raw, resolver, theater_id)
                if cd is None:
                    rejected.append("no_cell_resolve")
                    continue
                cached.append(cd)
            cache.put(key, cached)
        out.extend(cached)
    return out, rejected


def stack_hash(tiles: list[Tile]) -> str:
    """A stable hash over a SET of tiles (order-independent), for multi-tile detectors whose
    cache key is the whole input stack, not a single granule."""
    h = hashlib.sha256()
    for th in sorted(tile_hash(t) for t in tiles):
        h.update(th.encode())
    return h.hexdigest()


def cached_detect_multi(detector, tiles, cache: DetectionCache, resolver,
                        theater_id: str) -> tuple[list[CachedDetection], list[str]]:
    """Replay-safe caching for MULTI-tile detectors (change detection).

    Unlike cached_detect (per-tile), a change detector needs the whole before/after stack in one
    infer() call, so the cache key is over the combined stack (stack_hash), not a single tile.
    Otherwise identical: coarsen + band once at the boundary, store the coarsened cell-only form;
    a hit reuses it and never re-runs the model or re-hits GEE.
    """
    key = DetectionKey(stack_hash(tiles), detector.model_digest, detector.name)
    cached = cache.get(key)
    rejected: list[str] = []
    if cached is None:
        cached = []
        for raw in detector.infer(tiles):
            cd = coarsen_detection(raw, resolver, theater_id)
            if cd is None:
                rejected.append("no_cell_resolve")
                continue
            cached.append(cd)
        cache.put(key, cached)
    return cached, rejected


def detections_to_observations(cds: list[CachedDetection], source_id: str, family_id: str,
                               theater_id: str, taxonomy: Optional[dict] = None, embedder=None):
    """Turn coarsened detections into Observations via the pre-coarsened contract entry point.

    The cell_id is taken straight from each detection (authoritative), so the placement is
    stable even for zone-edge cells — no centroid round-trip, no re-resolution. Reuses the
    contract's content_hash / embedding logic so imagery obs are written exactly like any other.
    """
    from ingest.contract import observation_from_cell
    return [
        observation_from_cell(
            cell_id=cd.cell_id, theater_id=theater_id, source_id=source_id,
            source_family_id=family_id, modality="imagery", obs_type=cd.obs_type,
            occurred_start=datetime.fromisoformat(cd.occurred_start),
            occurred_end=datetime.fromisoformat(cd.occurred_end),
            text=cd.text, self_conf=cd.self_conf, meta=cd.meta,
            taxonomy=taxonomy, embedder=embedder,
        )
        for cd in cds
    ]
