"""
Pass-through detector — the trivial detector that proves the §6 contract end-to-end with NO
model (gate 3d). It reads pre-baked "detections" from each tile's meta and emits them as
imagery RawObservations, so we can verify offline that an imagery Observation flows through
normalize → fuse → replay bit-identical and corroborates a text event via noisy-OR — without
any GEE call or neural weights.

It emits the RAW score in self_conf; banding to a deterministic value happens once at the cache
boundary (caches.coarsen_detection), keeping the determinism contract in one place.
"""
from __future__ import annotations

from ingest.contract import GeoRef, RawObservation
from ingest.imagery.framework import Tile


class PassthroughDetector:
    name = "passthrough"
    model_digest = "passthrough-v1"

    def __init__(self, source_id: str = "passthrough_detector",
                 family_id: str = "passthrough", theater_id: str = "ua_donbas"):
        self.source_id = source_id
        self.family_id = family_id
        self.theater_id = theater_id

    def infer(self, tiles: list[Tile]) -> list[RawObservation]:
        out: list[RawObservation] = []
        for t in tiles:
            for det in t.meta.get("detections", []):
                out.append(RawObservation(
                    theater_id=self.theater_id, source_id=self.source_id,
                    source_family_id=self.family_id, modality="imagery",
                    obs_type=det["obs_type"],
                    occurred_start=t.acq_start, occurred_end=t.acq_end,
                    geo=GeoRef(lon=det["lon"], lat=det["lat"], precision_m=1000.0),
                    text=det.get("text", ""),
                    self_conf=det.get("score"),   # raw; banded at the cache boundary
                    meta={"granule": t.granule_id},
                ))
        return out
