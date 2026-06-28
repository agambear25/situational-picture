"""
NASA FIRMS active-fire adapter — the MVP thermal feed (Public Domain, free MAP_KEY).

VIIRS (SNPP + NOAA-20) and MODIS active-fire detections → one 'fire' Observation each,
modality='thermal'. Pure parsing (parse_firms_row / iter_observations) is offline-testable;
run() does the gated network + DB I/O and needs a free FIRMS_MAP_KEY (env).

Per-detection distinctness: sensor + acq time + FRP are folded into the observation text so the
contract's content_hash (normalized text + snapped cell + hour bucket) separates detections that
share a cell-hour — matching the intended (sensor, acq_dt, cell, frp) dedup key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from geo.control import assert_source_permitted
from ingest.contract import GeoRef, RawObservation
from ingest.pipeline import build_context, in_bbox, ingest_raws

logger = logging.getLogger(__name__)

# FIRMS API "source" → (our source_id, family_id). VIIRS sensors share one family; MODIS is its own.
FIRMS_SOURCES = {
    "VIIRS_SNPP_NRT": ("firms_viirs_snpp", "nasa_firms"),
    "VIIRS_NOAA20_NRT": ("firms_viirs_noaa20", "nasa_firms"),
    "MODIS_NRT": ("firms_modis", "nasa_modis"),
}

# FIRMS confidence → source-asserted confidence in [0,1]
_CONF = {"l": 0.3, "low": 0.3, "n": 0.6, "nominal": 0.6, "h": 0.9, "high": 0.9}


def _self_conf(raw_conf) -> Optional[float]:
    if raw_conf is None or raw_conf == "":
        return None
    s = str(raw_conf).strip().lower()
    if s in _CONF:
        return _CONF[s]
    try:  # MODIS gives a 0..100 integer
        return max(0.0, min(1.0, float(s) / 100.0))
    except ValueError:
        return None


def parse_firms_row(row: dict, source_id: str, family_id: str,
                    theater_id: str = "ua_donbas") -> Optional[RawObservation]:
    """One FIRMS CSV record → a 'fire' RawObservation. None only if it lacks coordinates/time."""
    lat = _as_float(row.get("latitude"))
    lon = _as_float(row.get("longitude"))
    if lat is None or lon is None:
        return None
    acq = _parse_acq(row.get("acq_date"), row.get("acq_time"))
    if acq is None:
        return None

    frp = row.get("frp")
    sat = row.get("satellite") or row.get("instrument") or source_id
    conf = row.get("confidence")
    # sensor + acq time + frp in the text → distinct content_hash per detection in a cell-hour
    text = (f"active fire detection ({sat}) at {acq.isoformat()}; "
            f"FRP {frp} MW; confidence {conf}")

    return RawObservation(
        theater_id=theater_id, source_id=source_id, source_family_id=family_id,
        modality="thermal", obs_type="fire",
        occurred_start=acq, occurred_end=acq, geo=GeoRef(lon=lon, lat=lat, precision_m=375.0),
        text=text, lang="en", self_conf=_self_conf(conf),
        meta={"satellite": sat, "frp": frp, "confidence": conf,
              "bright_ti4": row.get("bright_ti4"), "daynight": row.get("daynight")},
    )


def iter_observations(rows: Iterable[dict], source_id: str, family_id: str,
                      theater_id: str, bbox=None):
    for row in rows:
        raw = parse_firms_row(row, source_id, family_id, theater_id)
        if raw is None:
            continue
        if bbox is not None and not in_bbox(raw.geo.lon, raw.geo.lat, bbox):
            continue
        yield raw


# --------------------------------------------------------------------- fetching (I/O)

def _fetch_csv(source: str, bbox, day_range: int, map_key: str) -> list[dict]:
    """FIRMS area CSV API. A local file (FIRMS_FILE) overrides the network for offline runs."""
    import csv
    import io
    import os

    local = os.environ.get("FIRMS_FILE")
    if local:
        with open(local, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    import httpx  # lazy
    w, s, e, n = bbox
    area = f"{w},{s},{e},{n}"
    url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
           f"{map_key}/{source}/{area}/{day_range}")
    with httpx.Client(timeout=120) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
    if text.lstrip().lower().startswith(("invalid", "error")):
        raise RuntimeError(f"FIRMS API error for {source}: {text[:200]}")
    return list(csv.DictReader(io.StringIO(text)))


def run(theater_id: str = "ua_donbas") -> dict:
    """Gated entry point (dispatched by ingest.run only after Gate 1 + live_feeds_enabled).

    Needs FIRMS_MAP_KEY in the environment (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/).
    """
    import os

    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not map_key and not os.environ.get("FIRMS_FILE"):
        raise RuntimeError(
            "FIRMS_MAP_KEY is not set. Get a free key at "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/ and export it (never commit it)."
        )

    day_range = int(os.environ.get("FIRMS_DAY_RANGE", "1"))
    ctx = build_context(theater_id)
    bbox = ctx.theater["bbox"]

    totals = {"seen": 0, "ingested": 0, "rejected": 0, "by_reason": {}}
    for source, (source_id, family_id) in FIRMS_SOURCES.items():
        try:
            rows = _fetch_csv(source, bbox, day_range, map_key or "")
        except Exception as exc:  # one sensor down must not abort the others (fail loud, continue)
            assert_source_permitted(source_id)
            logger.warning("FIRMS source %s failed: %s", source, exc)
            continue
        assert_source_permitted(source_id)  # defense in depth
        raws = list(iter_observations(rows, source_id, family_id, theater_id, bbox=bbox))
        logger.info("FIRMS %s: %d rows → %d in-AOI", source, len(rows), len(raws))
        c = ingest_raws(raws, ctx)
        for k in ("seen", "ingested", "rejected"):
            totals[k] += c[k]
        for reason, n in c["by_reason"].items():
            totals["by_reason"][reason] = totals["by_reason"].get(reason, 0) + n
    return totals


# --------------------------------------------------------------------------- helpers

def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_acq(acq_date, acq_time) -> Optional[datetime]:
    if not acq_date:
        return None
    t = str(acq_time or "0").zfill(4)  # FIRMS acq_time is HHMM UTC, sometimes unpadded
    try:
        hh, mm = int(t[:-2] or 0), int(t[-2:])
        d = datetime.fromisoformat(str(acq_date).strip())
        return d.replace(hour=hh, minute=mm, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
