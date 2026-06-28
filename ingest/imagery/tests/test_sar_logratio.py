"""
Offline gate for the Phase-3e classical SAR log-ratio change detector. No GEE, no model, no DB.
Proves: pure/deterministic infer, threshold + cluster behaviour, the multi-tile determinism
cache, cell pinning, and the headline — a SAR change corroborates a text strike via noisy-OR
(second independent family ⇒ band lifts) and replays bit-identical.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision
from ingest.contract import GeoRef, RawObservation, normalize
from ingest.imagery.framework import Tile
from ingest.imagery.caches import (
    InMemoryDetectionCache, cached_detect_multi, detections_to_observations,
)
from ingest.imagery.sar_logratio import SarLogRatioDetector
from fusion.fuse import fuse
from fusion.replay import assert_bit_identical

_TAX = yaml.safe_load((Path(__file__).parents[3] / "config" / "taxonomy.yaml").read_text())

# A small controlled footprint so pixel→lon/lat is exactly checkable.
BBOX = (37.0, 48.0, 37.5, 48.5)   # (w, s, e, n) — 10×10 grid → 0.05°/pixel
T_BEFORE = datetime(2024, 3, 1, 4, 0, tzinfo=timezone.utc)
T_AFTER = datetime(2024, 3, 13, 4, 0, tzinfo=timezone.utc)   # ~12-day S1 revisit
# centre 3×3 patch at rows/cols 4..6 → centroid (5,5) → these coords:
PATCH_LON, PATCH_LAT = 37.275, 48.225


class _LocalResolver:
    """Offline coarsening resolver: MGRS snap, no DB (mirrors test_framework)."""
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


class _FakeEmbedder:
    """Deterministic stand-in for MiniLM. A shared 'military-event' subspace + a text-specific
    component, so a SAR-change text and a strike report have MODERATE cosine (not 0, not 1) —
    modeling how real embeddings let cross-modal observations corroborate. Imagery auto-text and
    a human report never share vocabulary, so the trigram fallback alone would hard-zero s_text;
    in production the embedding is what carries cross-modal similarity into the gray band."""
    @property
    def dim(self):
        return 16

    def embed(self, text):
        import hashlib
        v = [0.0] * 16
        for i in range(6):
            v[i] = 1.0                       # shared event subspace (baseline similarity)
        h = hashlib.sha256((text or "").encode()).digest()
        for i in range(10):
            v[6 + i] = (h[i] % 7) / 6.0      # text-specific component
        return tuple(v)


def _flat(val=-12.0, h=10, w=10):
    return np.full((h, w), val, dtype=np.float32)


def _with_patch(base, patch_val, r0, r1, c0, c1, h=10, w=10):
    a = _flat(base, h, w)
    a[r0:r1, c0:c1] = patch_val
    return a


def _tile(arr2d, gid, t_start, dur_h=1, bbox=BBOX):
    a = np.asarray(arr2d, dtype=np.float32)[None, :, :]   # (1, H, W), band VV
    buf = io.BytesIO(); np.save(buf, a)
    return Tile(granule_id=gid, acq_start=t_start, acq_end=t_start + timedelta(hours=dur_h),
                data=buf.getvalue(), bbox=bbox, meta={"bands": ["VV"], "scale_m": 500})


def _before():
    return _tile(_flat(-12.0), "S1_BEFORE", T_BEFORE)


def _after_with_patch(patch_val=0.0):
    return _tile(_with_patch(-12.0, patch_val, 4, 7, 4, 7), "S1_AFTER", T_AFTER)


# --------------------------------------------------------------------------- pure infer

def test_identical_tiles_yield_no_change():
    det = SarLogRatioDetector()
    assert det.infer([_before(), _tile(_flat(-12.0), "S1_AFTER", T_AFTER)]) == []


def test_single_tile_cannot_form_a_pair():
    assert SarLogRatioDetector().infer([_before()]) == []


def test_change_patch_is_detected_at_its_centroid():
    det = SarLogRatioDetector()
    obs = det.infer([_before(), _after_with_patch(0.0)])   # +12 dB over a 3×3 patch
    assert len(obs) == 1
    o = obs[0]
    assert o.modality == "imagery" and o.obs_type == "building_damaged"
    assert o.obs_type in set(_TAX["event_types"])
    assert o.source_family_id == "copernicus_sar"
    assert abs(o.geo.lon - PATCH_LON) < 1e-6 and abs(o.geo.lat - PATCH_LAT) < 1e-6
    assert o.self_conf >= 0.85          # 12 dB ≈ saturation → high raw confidence
    assert o.occurred_start == T_AFTER  # timestamped at the detection (after) pass
    assert o.meta["direction"] == "increase" and o.meta["change_from"] == T_BEFORE.isoformat()


def test_subthreshold_change_ignored():
    det = SarLogRatioDetector()                  # threshold_db = 4.0
    assert det.infer([_before(), _after_with_patch(-10.0)]) == []   # Δ = 2 dB < 4


def test_speckle_sized_cluster_ignored():
    det = SarLogRatioDetector()                  # min_cluster_px = 4
    a = _flat(-12.0); a[5, 5] = 0.0              # single 12 dB pixel
    assert det.infer([_before(), _tile(a, "S1_AFTER", T_AFTER)]) == []


def test_infer_is_deterministic():
    det = SarLogRatioDetector()
    tiles = [_before(), _after_with_patch(0.0)]
    a, b = det.infer(tiles), det.infer(tiles)
    assert len(a) == len(b) == 1
    assert (a[0].geo.lon, a[0].geo.lat, a[0].self_conf, a[0].obs_type) == \
           (b[0].geo.lon, b[0].geo.lat, b[0].self_conf, b[0].obs_type)


# --------------------------------------------------------------------------- determinism cache

def test_multi_tile_cache_pins_cell_and_bands_once():
    det = SarLogRatioDetector()
    tiles = [_before(), _after_with_patch(0.0)]
    cache, resolver = InMemoryDetectionCache(), _LocalResolver()

    cds1, rej1 = cached_detect_multi(det, tiles, cache, resolver, "ua_donbas")
    cds2, rej2 = cached_detect_multi(det, tiles, cache, resolver, "ua_donbas")   # cache hit
    assert not rej1 and not rej2 and len(cds1) == 1
    assert cds1 == cds2                                   # bit-identical across miss vs hit
    assert cds1[0].cell_id == to_cell_id(PATCH_LON, PATCH_LAT)   # cell pinned
    assert cds1[0].self_conf_band in ("very_high", "high")      # banded once
    assert not hasattr(cds1[0], "lon")                   # coarsened — no precise coord persisted


def test_unplaceable_change_is_rejection_not_drop():
    class _NullResolver:
        def resolve_to_cell(self, *a, **k): return None
    det = SarLogRatioDetector()
    cds, rej = cached_detect_multi(det, [_before(), _after_with_patch(0.0)],
                                   InMemoryDetectionCache(), _NullResolver(), "ua_donbas")
    assert cds == [] and rej == ["no_cell_resolve"]


# --------------------------------------------------------------------------- headline: 2nd family

def test_sar_change_corroborates_text_strike_and_replays():
    resolver, embedder = _LocalResolver(), _FakeEmbedder()
    det = SarLogRatioDetector()
    cds, _ = cached_detect_multi(det, [_before(), _after_with_patch(0.0)],
                                 InMemoryDetectionCache(), resolver, "ua_donbas")
    img_obs = detections_to_observations(
        cds, det.source_id, det.family_id, "ua_donbas", _TAX, embedder=embedder)[0]

    # A text strike in the SAME cell, co-temporal with the SAR detection (after pass).
    # Both carry embeddings (as in production) so cross-modal s_text is non-zero → gray band →
    # the adjudicator (here _AlwaysSame, modeling the local LLM) corroborates them.
    text = RawObservation(
        theater_id="ua_donbas", source_id="ucdp_ged_bulk", source_family_id="ucdp",
        modality="text", obs_type="strike",
        occurred_start=T_AFTER, occurred_end=T_AFTER + timedelta(hours=1),
        geo=GeoRef(lon=PATCH_LON, lat=PATCH_LAT, precision_m=1000.0),
        text="reported strike on the depot")
    obs = [normalize(text, resolver, _TAX, embedder=embedder)[0], img_obs]

    r1 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")
    r2 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")
    assert len(r1.events) == 1
    e = r1.events[0]
    assert e.n_independent_families == 2          # ucdp (text) + copernicus_sar (imagery)
    assert e.confidence_band == "High"            # cross-modal noisy-OR lift above Rumored
    assert r1.no_silent_drop({o.obs_id for o in obs})
    assert assert_bit_identical(r1, r2)
