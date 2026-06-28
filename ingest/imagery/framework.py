"""
Imagery detector framework — the reusable spine every Phase-3+ sensor rides (gate 3d).

A Detector turns preprocessed tiles into RawObservations through the ONE §6 contract
(ingest/contract.py), so fusion / API / UI need ZERO changes to corroborate imagery. SAR
amplitude, optical spectral indices, a deep change model, and future drone / SAR-vessel
microservices are all "config + one module" registered here.

Detectors are PURE and offline-testable: `infer(tiles) -> list[RawObservation]`. Acquisition
(Google Earth Engine) and any neural weights live in the acquisition path and are made
replay-safe by the determinism contract in caches.py (tile-hash + model-digest detection cache
+ banded confidence). A detector NEVER persists a precise coordinate — coarsening happens at
the cache boundary and again at normalize().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from ingest.contract import RawObservation


@dataclass(frozen=True)
class Tile:
    """One preprocessed image tile handed to a detector.

    `data` is the raw bytes the tile hash is computed over (e.g. a serialized σ0 / optical
    array) — it is the determinism anchor, not a coordinate list. `granule_id` pins the GEE
    granule so replay can prove it reused the same input.
    """
    granule_id: str
    acq_start: datetime
    acq_end: datetime
    data: bytes
    bbox: tuple | None = None
    meta: dict = field(default_factory=dict)


@runtime_checkable
class Detector(Protocol):
    """The contract every sensor implements. `infer` is PURE — no DB, no network, no clock."""
    name: str
    source_id: str          # registered in config/sources.yaml (family + reliability_w)
    family_id: str          # independence key for noisy-OR (SAR vs optical are distinct families)
    model_digest: str       # pinned weight digest; classical detectors use a version string

    def infer(self, tiles: list[Tile]) -> list[RawObservation]:
        """Emit RawObservations (modality='imagery'). self_conf is the RAW score; banding to a
        deterministic value happens once at the cache boundary (caches.coarsen_detection)."""
        ...


class DetectorRegistry:
    """`config + one module`: detectors register here; the runner looks them up by name."""

    def __init__(self):
        self._by_name: dict[str, Detector] = {}

    def register(self, detector: Detector) -> Detector:
        if detector.name in self._by_name:
            raise ValueError(f"detector {detector.name!r} already registered")
        self._by_name[detector.name] = detector
        return detector

    def get(self, name: str) -> Detector:
        return self._by_name[name]

    def all(self) -> list[Detector]:
        return list(self._by_name.values())


# Module-level registry; detector modules call registry.register(...) on import (the
# drone-imagery / SAR-vessel slots plug in here later — "config + one module").
registry = DetectorRegistry()
