"""
Ingest UCDP GED conflict events as a DATED historical conflict layer — the continuous 2022→now
chronology the board was missing (it had only a few 2022 points, then a gap, then recent fires).

UCDP GED is authoritative, geocoded, human-coded armed-conflict data (CC-BY). The raw feed has
~18k events in the Donbas AOI 2022–2026 — far too many to fuse one-by-one (block() is O(N²)), and
finer than the 1km grid anyway. So, exactly like ingest.unosat_history, points are aggregated per
(1km cell, day) into ONE observation: "this cell saw armed conflict on this day". That is the
honest unit at a 1km-day chronology, keeps the obs volume sane, and gives every cell a real
history you can scrub through. Source family = 'ucdp' (independent of imagery/thermal → corroborates
FIRMS fires and UNOSAT damage in the same cell via noisy-OR).

Build the combined Donbas CSV from the UCDP downloads first (definitive GED v25.1 2022–2024 +
GED Candidate 2025/2026), then:
    UCDP_GED_FILE=/path/ucdp_donbas.csv python -m ingest.ucdp_history --theater ua_donbas
    python -m fusion.run --theater ua_donbas
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grid.mgrs_1km import to_cell_id
from ingest.contract import GeoRef, RawObservation
from ingest.pipeline import build_context, in_bbox, ingest_raws

logger = logging.getLogger(__name__)

# UCDP type_of_violence → taxonomy label (matches ucdp_ged._VIOLENCE_LABEL / source_type_maps).
_VIOLENCE_LABEL = {1: "Armed Conflict (Government)", 2: "Armed Conflict (Non-State)",
                   3: "One-sided violence"}


def _as_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _period_key(date: str, granularity: str) -> str:
    """Aggregation period label for a YYYY-MM-DD date."""
    if granularity == "day":
        return date
    if granularity == "month":
        return date[:7]                       # YYYY-MM
    iso = datetime.fromisoformat(date).isocalendar()   # week
    return f"{iso[0]}-W{iso[1]:02d}"


def build_raws(theater_id: str, bbox, csv_path: Path, type_map: dict,
               granularity: str = "month") -> list[RawObservation]:
    """Aggregate UCDP rows → one RawObservation per (cell, period). Default period = month, dated
    to the cell's REAL activity window that month (first→last event) so the chronology is honest;
    block()'s O(N²) makes per-event (or even per-day) infeasible at ~16k Donbas cell-days."""
    groups: dict[tuple, dict] = defaultdict(
        lambda: {"n": 0, "deaths": 0, "violence": defaultdict(int), "place": None,
                 "lon": None, "lat": None, "conflict": None, "first": None, "last": None})
    for_total = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lon, lat = float(row["longitude"]), float(row["latitude"])
            except (KeyError, ValueError, TypeError):
                continue
            if not in_bbox(lon, lat, bbox):
                continue
            date = (row.get("date_start") or "")[:10]
            if len(date) != 10:
                continue
            for_total += 1
            key = (to_cell_id(lon, lat), _period_key(date, granularity))
            g = groups[key]
            g["n"] += 1
            g["deaths"] += _as_int(row.get("best"))
            g["violence"][_as_int(row.get("type_of_violence"), 1)] += 1
            g["place"] = (row.get("adm_2") or row.get("adm_1") or row.get("where_coordinates")
                          or g["place"])
            g["conflict"] = row.get("conflict_name") or g["conflict"]
            g["lon"], g["lat"] = lon, lat   # any point in the cell snaps to the same cell on write
            g["first"] = date if g["first"] is None else min(g["first"], date)
            g["last"] = date if g["last"] is None else max(g["last"], date)
    logger.info("UCDP: %d in-AOI events → %d (cell, %s) groups", for_total, len(groups), granularity)

    raws: list[RawObservation] = []
    for (_cell, _period), g in groups.items():
        dominant = max(g["violence"], key=g["violence"].get)
        label = _VIOLENCE_LABEL.get(dominant, "_default")
        obs_type = type_map.get(label, type_map.get("_default", "other"))
        t0 = datetime.fromisoformat(g["first"] + "T00:00:00+00:00").astimezone(timezone.utc)
        t1 = datetime.fromisoformat(g["last"] + "T23:59:00+00:00").astimezone(timezone.utc)
        where = g["place"] or "the area"
        deaths, span = g["deaths"], (g["first"] if g["first"] == g["last"] else f"{g['first']}…{g['last']}")
        text = (f"UCDP: {g['n']} armed-conflict event(s) near {where} ({span})"
                + (f"; {deaths} fatalities" if deaths else "")
                + (f" — {g['conflict']}" if g["conflict"] else ""))
        # self_conf: authoritative human-coded; a touch higher when the period was deadly (clearly real).
        self_conf = 0.78 if deaths == 0 else (0.85 if deaths >= 5 else 0.82)
        raws.append(RawObservation(
            theater_id=theater_id, source_id="ucdp_ged_bulk", source_family_id="ucdp",
            modality="text", obs_type=obs_type,
            occurred_start=t0, occurred_end=t1,
            geo=GeoRef(lon=g["lon"], lat=g["lat"], precision_m=1000.0),
            text=text, lang="en", self_conf=self_conf,
            meta={"source": "ucdp_ged", "period": _period, "first": g["first"], "last": g["last"],
                  "n_events": g["n"], "fatalities": deaths, "dominant_violence": dominant,
                  "place": g["place"]},
        ))
    return raws


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m ingest.ucdp_history")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--file", default=os.environ.get("UCDP_GED_FILE"))
    p.add_argument("--granularity", choices=["day", "week", "month"], default="month")
    p.add_argument("--count-only", action="store_true", help="aggregate + report, do not ingest")
    args = p.parse_args()
    if not args.file:
        p.error("provide --file or set UCDP_GED_FILE (the combined Donbas UCDP CSV)")

    ctx = build_context(args.theater)
    type_map = ctx.taxonomy.get("source_type_maps", {}).get("ucdp_ged", {})
    raws = build_raws(args.theater, ctx.theater["bbox"], Path(args.file), type_map, args.granularity)

    dates = sorted({r.occurred_start.date().isoformat() for r in raws})
    months = sorted({d[:7] for d in dates})
    print("=" * 60)
    print("UCDP HISTORICAL CONFLICT LAYER (aggregated per cell-day)")
    print("=" * 60)
    print(f"  cell-day observations : {len(raws)}")
    if dates:
        print(f"  date range            : {dates[0]} → {dates[-1]}")
        print(f"  distinct days/months  : {len(dates)} days / {len(months)} months")
    if args.count_only:
        print("  (count-only — nothing ingested)")
        return
    counters = ingest_raws(raws, ctx)
    print(f"  ingested {counters['ingested']}, rejected {counters['rejected']} "
          f"{counters.get('by_reason') or ''}")
    print("=" * 60)
    print("Next: python -m fusion.run --theater " + args.theater)


if __name__ == "__main__":
    main()
