"""
GEE acquisition — Phase 3b.

GEE is a DATA TAP ONLY: delivers server-side-preprocessed imagery and nothing more.
  Sentinel-1 GRD  — σ0 thermal-noise-removed, radiometrically calibrated, terrain-corrected
                    (Range-Doppler RTC); VV + VH polarisations in dB.
  Sentinel-2 SR   — Level-2A surface reflectance, cloud-masked via SCL.

We clip each image to the theater AOI, download as a float32 numpy array serialised to
.npy bytes, and wrap in a Tile. The Tile is cached locally so replay never re-hits GEE.
No analysis here — detectors run offline on cached tiles.

Auth:  OAuth2 credentials at ~/.config/earthengine/credentials.
       Run 'earthengine authenticate' once in a terminal (opens browser, auto-writes the file).
       No billing project for noncommercial/research use — GEE_PROJECT may be empty.

'ee' is imported LAZILY so this module is importable in offline tests without the package.
"""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

import yaml

from ingest.imagery.framework import Tile
from ingest.imagery.tile_cache import TileCache

log = logging.getLogger(__name__)

_S1_COLLECTION = "COPERNICUS/S1_GRD"
_S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
S2_BANDS_DEFAULT = ["B2", "B3", "B4", "B8", "B11"]  # blue, green, red, NIR, SWIR1

_INITIALIZED = False


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def initialize(project: Optional[str] = None) -> None:
    """Initialize GEE (idempotent). project=None → noncommercial research, no billing.

    Reads OAuth credentials from $GEE_CREDENTIALS_PATH or the default location
    ~/.config/earthengine/credentials (written by 'earthengine authenticate').
    If you hit 'invalid_scope': edit that credentials file and change 'scopes' to
    ["https://www.googleapis.com/auth/earthengine"] (drop the devstorage scope).
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    import ee  # lazy — not imported at module level so offline tests can load this file

    creds_override = os.environ.get("GEE_CREDENTIALS_PATH")
    if creds_override:
        import json
        with open(creds_override) as f:
            info = json.load(f)
        credentials = ee.ServiceAccountCredentials(
            info.get("client_email", ""), key_data=info.get("private_key", "")
        )
        ee.Initialize(credentials=credentials, project=project or None)
    else:
        # Personal OAuth2 (default path, written by 'earthengine authenticate')
        ee.Initialize(project=project or None)

    _INITIALIZED = True
    log.info("GEE initialised (project=%s)", project or "none/noncommercial")


def reset_for_tests() -> None:
    """Allow tests to re-initialise. Do NOT call in production code."""
    global _INITIALIZED
    _INITIALIZED = False


# ---------------------------------------------------------------------------
# Theater AOI
# ---------------------------------------------------------------------------

def _load_bbox(theater_id: str) -> tuple[float, float, float, float]:
    """Returns (west, south, east, north) from config/theaters.yaml."""
    cfg = yaml.safe_load(
        (Path(__file__).parents[2] / "config" / "theaters.yaml").read_text()
    )
    t = cfg["theaters"].get(theater_id)
    if t is None:
        raise ValueError(f"unknown theater_id {theater_id!r}")
    w, s, e, n = t["bbox"]
    return float(w), float(s), float(e), float(n)


# ---------------------------------------------------------------------------
# Downloader protocol (real GEE vs. mock in tests)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GranuleInfo:
    granule_id: str    # e.g. "S1A_IW_GRDH_1SDV_20240310T..."
    acq_start: str     # ISO-8601 with tz
    acq_end: str       # ISO-8601 with tz


@runtime_checkable
class TileDownloader(Protocol):
    """Abstracts GEE calls so tests can inject a fake without an EE account."""

    def list_granules(
        self,
        collection: str,
        aoi_bbox: tuple[float, float, float, float],
        start: str,
        end: str,
        extra_filters: dict,
    ) -> list[GranuleInfo]: ...

    def download_tile_bytes(
        self,
        granule_id: str,
        collection: str,
        aoi_bbox: tuple[float, float, float, float],
        bands: list[str],
        scale_m: int,
    ) -> bytes: ...


# ---------------------------------------------------------------------------
# Real GEE downloader
# ---------------------------------------------------------------------------

class GeeDownloader:
    """Talks to the live GEE API. Requires initialize() to have been called."""

    def list_granules(self, collection, aoi_bbox, start, end, extra_filters) -> list[GranuleInfo]:
        import ee
        w, s, e, n = aoi_bbox
        aoi = ee.Geometry.BBox(w, s, e, n)
        col = ee.ImageCollection(collection).filterBounds(aoi).filterDate(start, end)
        for filt in extra_filters.get("ee_filters", []):
            col = col.filter(filt)
        ids = col.aggregate_array("system:index").getInfo()
        starts = col.aggregate_array("system:time_start").getInfo()  # ms epoch
        ends = col.aggregate_array("system:time_end").getInfo()
        granules = []
        for gid, ts, te in zip(ids, starts, ends):
            def _ms(v):
                return datetime.fromtimestamp(v / 1000, tz=timezone.utc).isoformat()
            granules.append(GranuleInfo(
                granule_id=str(gid),
                acq_start=_ms(ts),
                acq_end=_ms(te) if te else _ms(ts),
            ))
        return granules

    def download_tile_bytes(self, granule_id, collection, aoi_bbox, bands, scale_m) -> bytes:
        """Download one granule clipped to AOI. Returns canonical float32 .npy bytes.

        GEE NPY format returns a structured array (named fields per band); we normalise to
        a plain float32 array shape (num_bands, H, W) so np.save gives deterministic bytes.
        """
        import ee
        import numpy as np

        w, s, e, n = aoi_bbox
        aoi = ee.Geometry.BBox(w, s, e, n)
        img = ee.Image(f"{collection}/{granule_id}").select(bands)
        url = img.getDownloadURL({
            "format": "NPY",
            "region": aoi,
            "scale": scale_m,
            "bands": [{"id": b} for b in bands],
        })
        import urllib.request
        with urllib.request.urlopen(url) as resp:
            raw = resp.read()

        arr = np.load(io.BytesIO(raw))
        # GEE NPY → structured array with named band fields; flatten to (bands, H, W) float32
        if arr.dtype.names:
            arr = np.stack([arr[name].astype(np.float32) for name in arr.dtype.names])
        else:
            arr = arr.astype(np.float32)

        buf = io.BytesIO()
        np.save(buf, arr)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Acquisition: S1
# ---------------------------------------------------------------------------

def fetch_s1_tiles(
    theater_id: str,
    start: str,
    end: str,
    *,
    polarization: str = "VV",
    include_vh: bool = True,
    downloader: Optional[TileDownloader] = None,
    cache: Optional[TileCache] = None,
    scale_m: Optional[int] = None,
    max_granules: int = 50,
) -> list[Tile]:
    """Fetch Sentinel-1 GRD tiles for the theater AOI, caching on first download.

    Args:
        theater_id:   must exist in config/theaters.yaml
        start / end:  ISO date strings, e.g. "2024-03-01" / "2024-03-15"
        polarization: primary polarisation to download ("VV" or "VH")
        include_vh:   also include VH alongside the primary band
        downloader:   inject a fake for offline tests; uses real GEE if None
        cache:        inject an in-memory/tmp cache for tests; uses GEE_TILE_CACHE_DIR if None
        scale_m:      metres per pixel (default: GEE_SCALE_M env var or 500)
        max_granules: safety cap to prevent runaway downloads

    Returns list[Tile] with modality='sar' (imagery). Tile.data = float32 .npy bytes,
    bands stored in Tile.meta['bands']. Tile.bbox = (west, south, east, north).
    """
    bands = [polarization, "VH"] if include_vh and polarization != "VH" else [polarization]
    source_prefix = "sentinel1_sar_logratio"
    return _fetch(
        theater_id=theater_id,
        start=start,
        end=end,
        collection=_S1_COLLECTION,
        bands=bands,
        source_prefix=source_prefix,
        extra_filters={
            "ee_filters": _s1_ee_filters(polarization),
        },
        downloader=downloader,
        cache=cache,
        scale_m=scale_m,
        max_granules=max_granules,
    )


def _s1_ee_filters(polarization: str) -> list:
    """Return ee.Filter objects for S1 IW GRD in the requested polarisation.

    Returns [] when ee is not installed (offline tests with a FakeDownloader).
    FakeDownloader ignores extra_filters entirely, so the empty list is safe.
    """
    try:
        import ee
        return [
            ee.Filter.eq("instrumentMode", "IW"),
            ee.Filter.listContains("transmitterReceiverPolarisation", polarization),
            ee.Filter.eq("orbitProperties_pass", "ASCENDING"),  # consistent geometry
        ]
    except ImportError:
        return []


# ---------------------------------------------------------------------------
# Acquisition: S2
# ---------------------------------------------------------------------------

def fetch_s2_tiles(
    theater_id: str,
    start: str,
    end: str,
    *,
    max_cloud_pct: float = 20.0,
    bands: Optional[list[str]] = None,
    downloader: Optional[TileDownloader] = None,
    cache: Optional[TileCache] = None,
    scale_m: Optional[int] = None,
    max_granules: int = 50,
) -> list[Tile]:
    """Fetch Sentinel-2 SR tiles for the theater AOI.

    S2 is cloud-limited (max_cloud_pct filter applied). Bands default to
    B2/B3/B4/B8/B11 (enough for NDVI, NBR, MNDWI — the spectral indices the
    Phase-3f detector uses). source_prefix = 'sentinel2_optical_index'.
    """
    _bands = bands or S2_BANDS_DEFAULT
    return _fetch(
        theater_id=theater_id,
        start=start,
        end=end,
        collection=_S2_COLLECTION,
        bands=_bands,
        source_prefix="sentinel2_optical_index",
        extra_filters={"cloud_pct": max_cloud_pct},
        downloader=downloader,
        cache=cache,
        scale_m=scale_m,
        max_granules=max_granules,
    )


# ---------------------------------------------------------------------------
# Shared fetch core
# ---------------------------------------------------------------------------

def _fetch(
    theater_id: str,
    start: str,
    end: str,
    collection: str,
    bands: list[str],
    source_prefix: str,
    extra_filters: dict,
    downloader: Optional[TileDownloader],
    cache: Optional[TileCache],
    scale_m: Optional[int],
    max_granules: int,
) -> list[Tile]:
    bbox = _load_bbox(theater_id)
    _dl = downloader if downloader is not None else GeeDownloader()
    _cache = cache if cache is not None else _default_cache()
    _scale = scale_m or int(os.environ.get("GEE_SCALE_M", "500"))

    # Cloud filter is an ee-side filter for S2 only; pass through extra_filters so GeeDownloader
    # can apply it. Fake downloaders receive it too and may ignore it — that's fine for tests.
    if "cloud_pct" in extra_filters:
        cp = extra_filters["cloud_pct"]
        extra_filters = dict(extra_filters)  # don't mutate caller's dict
        try:
            import ee
            extra_filters["ee_filters"] = extra_filters.get("ee_filters", []) + [
                ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cp)
            ]
        except ImportError:
            pass  # offline test — downloader mock handles it

    granules = _dl.list_granules(collection, bbox, start, end, extra_filters)[:max_granules]
    log.info("GEE %s: %d granules found (%s → %s)", collection, len(granules), start, end)

    tiles = []
    for g in granules:
        if _cache.has(g.granule_id, source_prefix):
            tile = _cache.get(g.granule_id, source_prefix)
            log.debug("cache hit  %s", g.granule_id)
        else:
            data = _dl.download_tile_bytes(g.granule_id, collection, bbox, bands, _scale)
            tile = Tile(
                granule_id=g.granule_id,
                acq_start=datetime.fromisoformat(g.acq_start),
                acq_end=datetime.fromisoformat(g.acq_end),
                data=data,
                bbox=bbox,
                meta={
                    "source_prefix": source_prefix,
                    "collection": collection,
                    "bands": bands,
                    "scale_m": _scale,
                    "theater_id": theater_id,
                },
            )
            _cache.put(tile, source_prefix)
            log.debug("cache miss %s → downloaded", g.granule_id)
        tiles.append(tile)

    return tiles


def _default_cache() -> TileCache:
    cache_dir = os.environ.get("GEE_TILE_CACHE_DIR", ".tile_cache")
    return TileCache(cache_dir)
