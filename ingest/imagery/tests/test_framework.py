"""
Offline gate for the Phase-3 detector framework (gate 3d) + the cross-modal lift machinery
(mini gate 3e). No GEE, no model, no DB — proves the determinism contract and that an imagery
Observation flows through the ONE §6 contract into fusion, corroborates text via noisy-OR, and
replays bit-identical.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision
from ingest.contract import GeoRef, RawObservation, normalize
from ingest.imagery.framework import DetectorRegistry, Tile
from ingest.imagery.caches import (
    DetectionKey, InMemoryDetectionCache, cached_detect, detections_to_observations,
    quantize_self_conf, tile_hash,
)
from ingest.imagery.passthrough import PassthroughDetector
from fusion.fuse import fuse
from fusion.replay import assert_bit_identical

T0 = datetime(2024, 3, 10, 8, 0, tzinfo=timezone.utc)
_TAX = yaml.safe_load((Path(__file__).parents[3] / "config" / "taxonomy.yaml").read_text())
_LON, _LAT = 37.749, 48.139
_CELL = to_cell_id(_LON, _LAT)


class _LocalResolver:
    """Offline coarsening resolver: MGRS snap, no DB."""
    def resolve_to_cell(self, lon, lat, precision_m, place_name, theater_id):
        if lon is None or lat is None:
            return None
        cid = to_cell_id(lon, lat)
        return CellResolution(cell=Cell(cell_id=cid, theater_id=theater_id, label=cid),
                              precision=GeoPrecision.PRECISE, non_precise=False)


class _Empty:
    def get(self, k): return None
    def put(self, k, v, **kw): pass


class _AlwaysSame:
    def adjudicate(self, ctx):
        from llm.schema import Verdict
        return Verdict(same=True, confidence=0.9, rationale="test")


def _tile(detections):
    return Tile(granule_id="S1_TEST_001", acq_start=T0, acq_end=T0 + timedelta(hours=1),
                data=b"\x00\x01\x02\x03", meta={"detections": detections})


# ---- determinism primitives ----

def test_quantize_is_banded_and_stable():
    b1, v1 = quantize_self_conf(0.78)
    b2, v2 = quantize_self_conf(0.83)
    assert b1 == b2 == "high" and v1 == v2 == 0.85   # float drift inside a band → identical
    assert quantize_self_conf(0.0)[0] == "very_low"
    assert quantize_self_conf(1.0)[0] == "very_high"


def test_tile_hash_is_stable():
    t = _tile([])
    assert tile_hash(t) == tile_hash(t)


# ---- §6 contract via the trivial detector ----

def test_passthrough_emits_valid_imagery_raw_observations():
    det = PassthroughDetector(source_id="sentinel1_sar_logratio", family_id="copernicus_sar")
    raws = det.infer([_tile([
        {"obs_type": "building_damaged", "score": 0.8, "lon": _LON, "lat": _LAT,
         "text": "SAR log-ratio change vs OSM footprint"}])])
    assert len(raws) == 1
    r = raws[0]
    assert r.modality == "imagery"
    assert r.obs_type == "building_damaged" and r.obs_type in set(_TAX["event_types"])
    assert r.source_family_id == "copernicus_sar"
    assert r.self_conf == 0.8   # RAW score; banding happens at the cache boundary, not here


# ---- determinism contract: cache stores COARSENED detections, replay reuses ----

def test_detection_cache_stores_coarsened_and_bands_once():
    det = PassthroughDetector(source_id="sentinel1_sar_logratio", family_id="copernicus_sar")
    t = _tile([{"obs_type": "crater", "score": 0.8, "lon": _LON, "lat": _LAT, "text": "crater field"}])
    cache, resolver = InMemoryDetectionCache(), _LocalResolver()

    cds1, rej1 = cached_detect(det, [t], cache, resolver, "ua_donbas")
    cds2, rej2 = cached_detect(det, [t], cache, resolver, "ua_donbas")   # second call = cache hit
    assert not rej1 and not rej2

    cached = cache.get(DetectionKey(tile_hash(t), det.model_digest, det.name))
    assert cached and cached[0].cell_id == _CELL
    # the persisted form is coarsened + banded — no precise coordinate, no raw logit
    assert not hasattr(cached[0], "lon") and not hasattr(cached[0], "lat")
    assert cached[0].self_conf_band == "high" and cached[0].self_conf == 0.85   # banded once

    # observations take the cell_id straight from the detection (no re-derivation)
    o1 = detections_to_observations(cds1, det.source_id, det.family_id, "ua_donbas", _TAX)[0]
    o2 = detections_to_observations(cds2, det.source_id, det.family_id, "ua_donbas", _TAX)[0]
    assert o1.cell_id == o2.cell_id == _CELL
    assert o1.self_conf == 0.85                    # the written Observation carries the banded value
    assert o1.content_hash == o2.content_hash      # deterministic across cache miss vs hit


def test_unplaceable_detection_is_rejection_not_drop():
    # A detection with valid coords that the resolver can't place (e.g. outside the AOI grid)
    # becomes a logged rejection, never a silent drop (invariant #1).
    class _NullResolver:
        def resolve_to_cell(self, *a, **k):
            return None
    det = PassthroughDetector(source_id="sentinel1_sar_logratio", family_id="copernicus_sar")
    t = _tile([{"obs_type": "crater", "score": 0.8, "lon": _LON, "lat": _LAT, "text": "outside AOI"}])
    cds, rej = cached_detect(det, [t], InMemoryDetectionCache(), _NullResolver(), "ua_donbas")
    assert cds == [] and rej == ["no_cell_resolve"]


def test_zone_edge_cell_is_pinned_not_redrifted():
    # Regression: a UTM zone-boundary cell whose CENTROID re-snaps to the adjacent zone
    # (lon 36.0 → cell 37TBN.. but centroid → 36TYT..). The detection must keep its precise
    # cell, never drift to the neighbor, because we pin cell_id instead of round-tripping geo.
    edge_lon, edge_lat = 36.0, 46.92
    edge_cell = to_cell_id(edge_lon, edge_lat)
    det = PassthroughDetector(source_id="sentinel1_sar_logratio", family_id="copernicus_sar")
    t = _tile([{"obs_type": "building_damaged", "score": 0.8, "lon": edge_lon, "lat": edge_lat,
                "text": "edge"}])
    cds, _ = cached_detect(det, [t], InMemoryDetectionCache(), _LocalResolver(), "ua_donbas")
    assert cds[0].cell_id == edge_cell
    o = detections_to_observations(cds, det.source_id, det.family_id, "ua_donbas", _TAX)[0]
    assert o.cell_id == edge_cell                  # pinned — no centroid round-trip drift


# ---- cross-modal lift + replay bit-identical (mini gate 3e, machinery only) ----

def test_imagery_lifts_band_via_noisy_or_and_replays():
    resolver = _LocalResolver()
    text = RawObservation(
        theater_id="ua_donbas", source_id="ucdp_ged_bulk", source_family_id="ucdp",
        modality="text", obs_type="strike", occurred_start=T0, occurred_end=T0 + timedelta(hours=1),
        geo=GeoRef(lon=_LON, lat=_LAT, precision_m=1000.0), text="strike on the coke plant Avdiivka")
    det = PassthroughDetector(source_id="sentinel1_sar_logratio", family_id="copernicus_sar")
    # the imagery observation rides the full detector → cache → coarsen → pre-coarsened-contract path
    cds, _ = cached_detect(det, [_tile([
        {"obs_type": "building_damaged", "score": 0.8, "lon": _LON, "lat": _LAT,
         "text": "SAR change at the coke plant Avdiivka"}])], InMemoryDetectionCache(), resolver, "ua_donbas")
    img_obs = detections_to_observations(cds, det.source_id, det.family_id, "ua_donbas", _TAX)[0]

    obs = [normalize(text, resolver, _TAX)[0], img_obs]
    r1 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")
    r2 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")

    assert len(r1.events) == 1
    e = r1.events[0]
    assert e.n_independent_families == 2          # ucdp (text) + copernicus_sar (imagery)
    assert e.confidence_band == "High"            # cross-modal corroboration lifts the band
    assert r1.no_silent_drop({o.obs_id for o in obs})
    assert assert_bit_identical(r1, r2)           # replay holds with imagery in the mix


# ---- registry ----

def test_registry_register_and_get():
    reg = DetectorRegistry()
    d = PassthroughDetector()
    reg.register(d)
    assert reg.get("passthrough") is d and d in reg.all()
