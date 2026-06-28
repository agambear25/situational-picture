"""
Offline assembly gate for the imagery ingest runner (Phase 3e live path). No GEE, no real DB:
a fake connection records the appends and a fake downloader stands in for GEE, so the wiring
(detector → determinism cache → coarsened Observation → log append) is verified end-to-end.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np

from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision
from ingest.imagery.framework import Tile
from ingest.imagery.caches import InMemoryDetectionCache
from ingest.imagery.sar_logratio import SarLogRatioDetector
from ingest.imagery.run import detect_and_persist, fetch_window_tiles

BBOX = (37.0, 48.0, 37.5, 48.5)
T_BEFORE = datetime(2024, 2, 5, 4, 0, tzinfo=timezone.utc)
T_AFTER = datetime(2024, 3, 5, 4, 0, tzinfo=timezone.utc)
PATCH_CELL = to_cell_id(37.275, 48.225)


class _LocalResolver:
    def resolve_to_cell(self, lon, lat, precision_m, place_name, theater_id):
        if lon is None or lat is None:
            return None
        cid = to_cell_id(lon, lat)
        return CellResolution(cell=Cell(cell_id=cid, theater_id=theater_id, label=cid),
                              precision=GeoPrecision.PRECISE, non_precise=False)


class _FakeEmbedder:
    @property
    def dim(self): return 8
    def embed(self, text): return tuple([1.0] * 8)


class _FakeCursor:
    def __init__(self, conn): self._c = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self._c.executed.append((sql, params))
    @property
    def rowcount(self): return self._c._rowcount


class _FakeConn:
    """Records executed statements; _rowcount controls insert-vs-dup."""
    def __init__(self, rowcount=1):
        self.executed = []
        self._rowcount = rowcount
        self.commits = 0
    def cursor(self): return _FakeCursor(self)
    def commit(self): self.commits += 1
    def rollback(self): pass


def _tile(arr2d, gid, t):
    a = np.asarray(arr2d, dtype=np.float32)[None, :, :]
    buf = io.BytesIO(); np.save(buf, a)
    return Tile(granule_id=gid, acq_start=t, acq_end=t + timedelta(hours=1),
                data=buf.getvalue(), bbox=BBOX, meta={"bands": ["VV"], "scale_m": 500})


def _before_after_with_change():
    before = _tile(np.full((10, 10), -12.0, np.float32), "S1_BEFORE", T_BEFORE)
    arr = np.full((10, 10), -12.0, np.float32)
    arr[4:7, 4:7] = 0.0          # +12 dB 3×3 patch → one detection
    after = _tile(arr, "S1_AFTER", T_AFTER)
    return [before, after]


def test_detect_and_persist_writes_one_coarsened_observation():
    conn = _FakeConn(rowcount=1)
    counters = detect_and_persist(
        SarLogRatioDetector(), _before_after_with_change(), conn,
        _LocalResolver(), _FakeEmbedder(), InMemoryDetectionCache())
    assert counters == {"detections": 1, "ingested": 1, "exact_dup": 0,
                        "rejected": 0, "by_reason": {}}
    # exactly one INSERT into the observation log, carrying the pinned cell (no precise coord)
    inserts = [(s, p) for (s, p) in conn.executed if "INSERT INTO log.observation" in s]
    assert len(inserts) == 1
    assert PATCH_CELL in inserts[0][1]            # cell_id is in the params
    assert all(v != 37.275 for v in inserts[0][1])  # the precise lon never reaches SQL


def test_detect_and_persist_counts_exact_dup():
    conn = _FakeConn(rowcount=0)                  # content_hash already present → dup
    counters = detect_and_persist(
        SarLogRatioDetector(), _before_after_with_change(), conn,
        _LocalResolver(), _FakeEmbedder(), InMemoryDetectionCache())
    assert counters["detections"] == 1 and counters["ingested"] == 0 and counters["exact_dup"] == 1


def test_no_change_persists_nothing():
    conn = _FakeConn()
    flat = np.full((10, 10), -12.0, np.float32)
    tiles = [_tile(flat, "S1_BEFORE", T_BEFORE), _tile(flat, "S1_AFTER", T_AFTER)]
    counters = detect_and_persist(
        SarLogRatioDetector(), tiles, conn, _LocalResolver(), _FakeEmbedder(),
        InMemoryDetectionCache())
    assert counters["detections"] == 0 and counters["ingested"] == 0
    assert not [s for (s, _) in conn.executed if "INSERT INTO log.observation" in s]


def test_fetch_window_tiles_pulls_before_and_after(tmp_path):
    from ingest.imagery.tests.test_gee_s1 import FakeDownloader
    from ingest.imagery.tile_cache import TileCache
    tiles = fetch_window_tiles(
        "ua_donbas", ("2024-02-01", "2024-02-20"), ("2024-03-01", "2024-03-20"),
        downloader=FakeDownloader(), cache=TileCache(tmp_path))
    assert len(tiles) == 2          # one granule per window (before + after)
