"""
Ingest UNOSAT damage points as a DATED historical damage layer (the 2022-onwards chronology).

The board was missing history: only the last few days of FIRMS fire and a handful of seeded
incidents. UNOSAT's 13.5k Donbas damage points each carry an assessment date across 2022, so they
make a real time series of war damage — scrub the timeline and you watch Mariupol, Sievierodonetsk,
Rubizhne, Avdiivka light up as the front moved. Points are aggregated per (1km cell, date) into one
'building_damaged' observation dated to that assessment, so the volume stays sane and each cell gets
a real history. Source family = 'unosat' (authoritative human analysis).

Run:
    python -m ingest.unosat_history --theater ua_donbas
    python -m fusion.run --theater ua_donbas
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grid.mgrs_1km import to_cell_id
from ingest.contract import GeoRef, RawObservation
from ingest.pipeline import build_context, in_bbox, ingest_raws

logger = logging.getLogger(__name__)

_GROUND_TRUTH = Path(__file__).resolve().parents[1] / "data" / "ground_truth" / "unosat_labels.geojson"

# UNOSAT damage grade → plain label + a banded source confidence (higher grade = more certain damage).
_GRADE = {2: ("moderate damage", 0.6), 3: ("severe damage", 0.8), 4: ("destroyed", 0.9)}


def build_raws(theater_id: str, bbox, labels_path: Path) -> list[RawObservation]:
    feats = json.loads(labels_path.read_text(encoding="utf-8")).get("features", [])
    # group points by (cell, assessment-date)
    groups: dict[tuple, dict] = defaultdict(lambda: {"grades": [], "city": None, "lon": None, "lat": None, "n": 0})
    for f in feats:
        grade = f.get("properties", {}).get("damage")
        if grade not in _GRADE:
            continue
        coords = (f.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not in_bbox(lon, lat, bbox):
            continue
        date = str(f["properties"].get("date", ""))[:10]
        if not date:
            continue
        key = (to_cell_id(lon, lat), date)
        g = groups[key]
        g["grades"].append(grade)
        g["city"] = f["properties"].get("city") or g["city"]
        g["lon"], g["lat"] = lon, lat   # any point in the cell snaps to the same cell at write time
        g["n"] += 1

    raws: list[RawObservation] = []
    for (_cell, date), g in groups.items():
        worst = max(g["grades"])
        label, conf = _GRADE[worst]
        t0 = datetime.fromisoformat(date + "T00:00:00+00:00").astimezone(timezone.utc)
        where = g["city"] or "the area"
        text = (f"UNOSAT satellite assessment: {g['n']} damaged structure(s) ({label}) "
                f"in {where} as of {date}")
        raws.append(RawObservation(
            theater_id=theater_id, source_id="unosat", source_family_id="unosat",
            modality="imagery", obs_type="building_damaged",
            occurred_start=t0, occurred_end=t0 + timedelta(hours=23, minutes=59),
            geo=GeoRef(lon=g["lon"], lat=g["lat"], precision_m=1000.0),
            text=text, lang="en", self_conf=conf,
            meta={"source": "unosat", "date": date, "worst_grade": worst,
                  "n_structures": g["n"], "city": g["city"]},
        ))
    return raws


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m ingest.unosat_history")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--labels", default=str(_GROUND_TRUTH))
    args = p.parse_args()

    ctx = build_context(args.theater)
    bbox = ctx.theater["bbox"]
    raws = build_raws(args.theater, bbox, Path(args.labels))
    logger.info("UNOSAT history: %d dated cell-damage observations to ingest", len(raws))
    counters = ingest_raws(raws, ctx)
    print("=" * 60)
    print("UNOSAT HISTORICAL DAMAGE LAYER")
    print("=" * 60)
    dates = sorted({r.occurred_start.date().isoformat() for r in raws})
    print(f"  observations : {len(raws)}  (ingested {counters['ingested']}, "
          f"rejected {counters['rejected']} {counters.get('by_reason') or ''})")
    if dates:
        print(f"  date range   : {dates[0]} → {dates[-1]}  ({len(dates)} distinct dates)")
    print("=" * 60)
    print("Next: python -m fusion.run --theater " + args.theater)


if __name__ == "__main__":
    main()
