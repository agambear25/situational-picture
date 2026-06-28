"""
Offline gate for Phase-3b: GEE acquisition module + tile cache.
No network, no real EE account, no GEE package required.

Gate: tiles round-trip from cache; no-billing assertion passes (project=None accepted).
All GEE calls are replaced by FakeDownloader so this runs in CI with no credentials.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest

from ingest.imagery.framework import Tile
from ingest.imagery.gee_s1 import (
    GranuleInfo,
    TileDownloader,
    _load_bbox,
    fetch_s1_tiles,
    fetch_s2_tiles,
    reset_for_tests,
)
from ingest.imagery.tile_cache import TileCache

_UA_BBOX = (36.0, 46.8, 39.5, 49.5)


# ---------------------------------------------------------------------------
# Fake downloader — no network, no ee
# ---------------------------------------------------------------------------

class FakeDownloader:
    """Returns synthetic .npy bytes. Counts download calls to verify cache behaviour."""

    def __init__(self, granule_id: str = "FAKE_S1_001", n_bands: int = 2):
        self.granule_id = granule_id
        self.n_bands = n_bands
        self.download_count = 0

    def list_granules(self, collection, aoi_bbox, start, end, extra_filters) -> list[GranuleInfo]:
        return [GranuleInfo(
            granule_id=self.granule_id,
            acq_start="2024-03-10T08:00:00+00:00",
            acq_end="2024-03-10T08:00:01+00:00",
        )]

    def download_tile_bytes(self, granule_id, collection, aoi_bbox, bands, scale_m) -> bytes:
        self.download_count += 1
        arr = np.zeros((len(bands), 4, 4), dtype=np.float32)
        buf = io.BytesIO()
        np.save(buf, arr)
        return buf.getvalue()


assert isinstance(FakeDownloader(), TileDownloader), \
    "FakeDownloader must satisfy TileDownloader Protocol"


# ---------------------------------------------------------------------------
# TileCache unit tests
# ---------------------------------------------------------------------------

def test_tile_cache_put_and_get_round_trip(tmp_path):
    cache = TileCache(tmp_path)
    arr = np.ones((2, 3, 3), dtype=np.float32)
    buf = io.BytesIO()
    np.save(buf, arr)
    tile = Tile(
        granule_id="S1_TEST_001",
        acq_start=__import__("datetime").datetime(2024, 3, 10, 8, tzinfo=__import__("datetime").timezone.utc),
        acq_end=__import__("datetime").datetime(2024, 3, 10, 8, 1, tzinfo=__import__("datetime").timezone.utc),
        data=buf.getvalue(),
        bbox=_UA_BBOX,
        meta={"bands": ["VV", "VH"]},
    )
    cache.put(tile, "sentinel1_sar_logratio")
    recovered = cache.get("S1_TEST_001", "sentinel1_sar_logratio")
    assert recovered is not None
    assert recovered.granule_id == tile.granule_id
    assert recovered.data == tile.data           # exact bytes round-trip
    assert recovered.bbox == tile.bbox


def test_tile_cache_miss_returns_none(tmp_path):
    cache = TileCache(tmp_path)
    assert cache.get("NONEXISTENT", "any_prefix") is None


def test_tile_cache_has_reflects_presence(tmp_path):
    cache = TileCache(tmp_path)
    arr = np.zeros((1, 2, 2), dtype=np.float32)
    buf = io.BytesIO()
    np.save(buf, arr)
    from datetime import datetime, timezone
    tile = Tile("G1", datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 1, tzinfo=timezone.utc), data=buf.getvalue())
    assert not cache.has("G1", "s1")
    cache.put(tile, "s1")
    assert cache.has("G1", "s1")
    assert len(cache) == 1


def test_tile_cache_different_prefix_same_granule_is_distinct_entry(tmp_path):
    """Two source prefixes for the same granule_id must not collide."""
    cache = TileCache(tmp_path)
    from datetime import datetime, timezone
    arr = np.zeros((1, 2, 2), dtype=np.float32)
    buf = io.BytesIO(); np.save(buf, arr)
    data = buf.getvalue()
    t = Tile("G1", datetime(2024, 1, 1, tzinfo=timezone.utc),
             datetime(2024, 1, 1, tzinfo=timezone.utc), data=data)
    cache.put(t, "prefix_a")
    assert not cache.has("G1", "prefix_b")       # different prefix → different slot
    assert cache.has("G1", "prefix_a")
    assert len(cache) == 1


# ---------------------------------------------------------------------------
# fetch_s1_tiles — cache behaviour with FakeDownloader
# ---------------------------------------------------------------------------

def test_fetch_s1_downloads_once_then_hits_cache(tmp_path):
    fake = FakeDownloader()
    tiles = fetch_s1_tiles("ua_donbas", "2024-03-01", "2024-03-15",
                           downloader=fake, cache=TileCache(tmp_path))
    assert len(tiles) == 1 and fake.download_count == 1

    # Second call with the same cache → cache hit, no re-download
    tiles2 = fetch_s1_tiles("ua_donbas", "2024-03-01", "2024-03-15",
                            downloader=fake, cache=TileCache(tmp_path))
    assert len(tiles2) == 1 and fake.download_count == 1   # still 1


def test_fetch_s1_tile_has_expected_shape_and_meta(tmp_path):
    fake = FakeDownloader(n_bands=2)
    tiles = fetch_s1_tiles("ua_donbas", "2024-03-01", "2024-03-15",
                           downloader=fake, cache=TileCache(tmp_path), scale_m=500)
    t = tiles[0]
    assert t.granule_id == "FAKE_S1_001"
    assert t.bbox == _UA_BBOX
    assert t.meta["theater_id"] == "ua_donbas"
    assert t.meta["scale_m"] == 500
    assert "VV" in t.meta["bands"]
    # .npy bytes decode correctly
    arr = np.load(io.BytesIO(t.data))
    assert arr.ndim == 3 and arr.dtype == np.float32   # (bands, H, W)


def test_fetch_s2_downloads_once_then_hits_cache(tmp_path):
    fake = FakeDownloader(granule_id="FAKE_S2_001", n_bands=5)
    fetch_s2_tiles("ua_donbas", "2024-03-01", "2024-03-15",
                   downloader=fake, cache=TileCache(tmp_path))
    assert fake.download_count == 1
    fetch_s2_tiles("ua_donbas", "2024-03-01", "2024-03-15",
                   downloader=fake, cache=TileCache(tmp_path))
    assert fake.download_count == 1   # still 1 — pure cache hit


# ---------------------------------------------------------------------------
# AOI loading
# ---------------------------------------------------------------------------

def test_load_bbox_ua_donbas():
    bbox = _load_bbox("ua_donbas")
    w, s, e, n = bbox
    assert w == pytest.approx(36.0) and e == pytest.approx(39.5)
    assert s == pytest.approx(46.8) and n == pytest.approx(49.5)


def test_load_bbox_unknown_theater_raises():
    with pytest.raises(ValueError, match="unknown theater_id"):
        _load_bbox("atlantis")


# ---------------------------------------------------------------------------
# No-billing assertion: initialize accepts project=None (mocked)
# ---------------------------------------------------------------------------

def test_initialize_accepts_none_project(monkeypatch):
    """initialize(project=None) must succeed without error.

    We mock the ee module so no real GEE auth is attempted — the assertion being
    tested is that our code passes project=None (not a billing project string)
    to ee.Initialize, which is what GEE noncommercial research requires.
    """
    reset_for_tests()
    # Hermetic: GEE_PROJECT may be set in the developer's real environment (.env). The
    # no-project case must be tested with it cleared, since initialize() now resolves the
    # project from GEE_PROJECT when no explicit argument is passed.
    monkeypatch.delenv("GEE_PROJECT", raising=False)

    captured = {}
    class FakeEE:
        @staticmethod
        def Initialize(**kwargs):
            captured.update(kwargs)

    import sys
    monkeypatch.setitem(sys.modules, "ee", FakeEE)

    from ingest.imagery import gee_s1
    gee_s1.initialize(project=None)

    assert captured.get("project") is None, \
        "initialize must pass project=None to ee.Initialize (noncommercial — no billing)"

    # And when GEE_PROJECT is set, it must be honored (precedence: arg > env > credentials file).
    reset_for_tests()
    monkeypatch.setenv("GEE_PROJECT", "ee-test-project")
    captured.clear()
    gee_s1.initialize()
    assert captured.get("project") == "ee-test-project"

    reset_for_tests()   # clean up so other tests aren't affected
