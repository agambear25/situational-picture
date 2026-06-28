"""
UCDP GED adapter — the MVP text feed (open, CC-BY 4.0, no login).

UCDP Georeferenced Event Dataset: authoritative, geocoded, post-hoc (latency ~weeks). Each
GED event → one text Observation. Pure parsing (parse_ucdp_row / iter_observations) is offline
-testable; run() does the gated network + DB I/O.

modality='text'. obs_type via config/taxonomy.yaml source_type_maps.ucdp_ged, keyed off
type_of_violence (1=state-based, 2=non-state, 3=one-sided).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from geo.control import assert_source_permitted
from ingest.contract import GeoRef, RawObservation
from ingest.pipeline import build_context, in_bbox, ingest_raws

logger = logging.getLogger(__name__)

SOURCE_ID = "ucdp_ged_bulk"
FAMILY_ID = "ucdp"

# UCDP type_of_violence code → the taxonomy key in source_type_maps.ucdp_ged
_VIOLENCE_LABEL = {
    1: "Armed Conflict (Government)",   # state-based
    2: "Armed Conflict (Non-State)",    # non-state
    3: "One-sided violence",
}


def _obs_type(type_of_violence, type_map: dict) -> str:
    label = _VIOLENCE_LABEL.get(_as_int(type_of_violence), "_default")
    return type_map.get(label, type_map.get("_default", "other"))


def parse_ucdp_row(row: dict, type_map: dict, theater_id: str = "ua_donbas") -> Optional[RawObservation]:
    """One UCDP GED record → RawObservation. Returns None only if it has no usable geo at all
    (the caller logs that; a malformed row is never a silent drop)."""
    lat = _as_float(row.get("latitude"))
    lon = _as_float(row.get("longitude"))
    place_name = (row.get("where_coordinates") or row.get("adm_2")
                  or row.get("adm_1") or row.get("where_description") or None)
    if lat is None and lon is None and not place_name:
        return None

    start = _parse_date(row.get("date_start") or row.get("date_end"))
    end = _parse_date(row.get("date_end") or row.get("date_start")) or start
    if start is None:
        return None

    obs_type = _obs_type(row.get("type_of_violence"), type_map)
    best = row.get("best")
    conflict = row.get("conflict_name") or row.get("dyad_name") or "armed conflict"
    where = place_name or "unknown location"
    src_art = (row.get("source_article") or row.get("source_headline") or "").strip()
    text = f"{conflict} — {where}"
    if best not in (None, "", "0"):
        text += f"; best fatality estimate {best}"
    if src_art:
        text += f"; {src_art[:160]}"

    geo = GeoRef(
        lon=lon, lat=lat,
        precision_m=float(row.get("precision_m", 1000.0)) if (lat is not None) else 0.0,
        place_name=place_name,
    )
    return RawObservation(
        theater_id=theater_id, source_id=SOURCE_ID, source_family_id=FAMILY_ID,
        modality="text", obs_type=obs_type,
        occurred_start=start, occurred_end=end, geo=geo,
        text=text, lang=None, self_conf=None,
        meta={"ucdp_id": row.get("id"), "country": row.get("country"),
              "type_of_violence": row.get("type_of_violence")},
    )


def iter_observations(rows: Iterable[dict], type_map: dict, theater_id: str, bbox=None):
    """Parse + (optionally) bbox-filter a stream of UCDP rows into RawObservations."""
    for row in rows:
        raw = parse_ucdp_row(row, type_map, theater_id)
        if raw is None:
            continue
        if bbox is not None and raw.geo.lon is not None and raw.geo.lat is not None:
            if not in_bbox(raw.geo.lon, raw.geo.lat, bbox):
                continue
        yield raw


# --------------------------------------------------------------------- fetching (I/O)

def _fetch_rows(theater: dict) -> list[dict]:
    """Fetch UCDP GED rows. A local cached file (UCDP_GED_FILE, CSV or JSON) overrides the API
    so a run can be fully offline; otherwise the public UCDP API is paged by country."""
    import os

    local = os.environ.get("UCDP_GED_FILE")
    if local:
        return _read_local(local)

    import httpx  # lazy
    version = os.environ.get("UCDP_GED_VERSION", "24.1")
    base = f"https://ucdpapi.pcr.uu.se/api/gedevents/{version}"
    country = os.environ.get("UCDP_COUNTRY", "Ukraine")
    rows: list[dict] = []
    page = 0
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(base, params={"pagesize": 1000, "page": page, "Country": country})
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("Result", [])
            rows.extend(batch)
            if not batch or page >= int(data.get("TotalPages", 1)) - 1:
                break
            page += 1
    logger.info("UCDP GED: fetched %d rows for %s", len(rows), country)
    return rows


def _read_local(path: str) -> list[dict]:
    import csv
    import json
    if path.endswith(".json"):
        data = json.loads(open(path, encoding="utf-8").read())
        return data.get("Result", data) if isinstance(data, dict) else data
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(theater_id: str = "ua_donbas") -> dict:
    """Gated entry point (dispatched by ingest.run only after Gate 1 + live_feeds_enabled)."""
    assert_source_permitted(SOURCE_ID)  # defense in depth (never ISW/DSM)
    ctx = build_context(theater_id)
    type_map = ctx.taxonomy.get("source_type_maps", {}).get("ucdp_ged", {})
    bbox = ctx.theater["bbox"]
    rows = _fetch_rows(ctx.theater)
    raws = list(iter_observations(rows, type_map, theater_id, bbox=bbox))
    logger.info("UCDP GED: %d rows → %d in-AOI observations", len(rows), len(raws))
    return ingest_raws(raws, ctx)


# --------------------------------------------------------------------------- helpers

def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _parse_date(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
