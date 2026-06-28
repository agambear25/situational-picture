"""
Seed a few real, well-documented Donbas incidents as multi-source observations.

Purpose: give the board (and the application layer) rich, named, HIGH-confidence events to work
with so we can build/demo without waiting on the hand-labelling pass. Each incident is a real,
publicly-reported event entered as several observations from DISTINCT source families in the same
1km cell + hour, so fusion corroborates them (noisy-OR over independent families → lifts the
confidence band) and auto-merges them (texts are similar enough to clear the high threshold without
the LLM, so this works with `fusion.run`'s default keep-separate adjudicator).

These are flagged meta.seeded=true for transparency. Run:
    python -m ingest.seed_incidents --theater ua_donbas
    python -m fusion.run --theater ua_donbas      # project them into events
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from ingest.contract import GeoRef, RawObservation
from ingest.pipeline import build_context, ingest_raws

logger = logging.getLogger(__name__)

_MODALITY = {"ucdp": "text", "gdelt": "text", "copernicus_sar": "imagery", "nasa_firms": "thermal"}
_SOURCE_ID = {"ucdp": "ucdp_ged_bulk", "gdelt": "gdelt_v2",
              "copernicus_sar": "sentinel1_sar_logratio", "nasa_firms": "firms_viirs_snpp"}

# Each incident: place + coords + time, and observations as (family, obs_type, self_conf, text).
# Texts deliberately share the place name + event so they clear the auto-merge threshold offline.
INCIDENTS = [
    {
        # Cross-modal showcase: two news families + Sentinel-1 satellite all agree → 3 sources.
        "place": "Avdiivka", "lon": 37.7486, "lat": 48.1384, "when": "2024-02-17T06:00:00Z",
        "obs": [
            ("ucdp", "building_damaged", 0.9,
             "The Avdiivka coke plant was destroyed; Russian forces levelled the plant during the assault on Avdiivka"),
            ("gdelt", "building_damaged", 0.7,
             "Avdiivka coke plant destroyed in the Russian assault; the Avdiivka plant reduced to rubble"),
            ("copernicus_sar", "building_damaged", 0.8,
             "Sentinel-1 satellite radar shows the Avdiivka coke plant destroyed — major structural collapse"),
        ],
    },
    {
        # Cross-modal: news report + Sentinel-1 satellite agree on building destruction.
        "place": "Mariupol", "lon": 37.5997, "lat": 47.1045, "when": "2022-03-16T10:00:00Z",
        "obs": [
            ("ucdp", "building_damaged", 0.9,
             "Russian airstrike destroyed a building in central Mariupol; widespread destruction across Mariupol"),
            ("copernicus_sar", "building_damaged", 0.8,
             "Sentinel-1 satellite radar over central Mariupol shows a building destroyed, consistent with the airstrike"),
        ],
    },
    {
        "place": "Kramatorsk", "lon": 37.5547, "lat": 48.7324, "when": "2022-04-08T06:30:00Z",
        "obs": [
            ("ucdp", "strike", 0.9,
             "Missile strike on Kramatorsk railway station killed dozens awaiting evacuation in Kramatorsk"),
            ("gdelt", "strike", 0.7,
             "Kramatorsk railway station hit by a missile strike; mass casualties at the Kramatorsk station"),
        ],
    },
    {
        "place": "Sievierodonetsk", "lon": 38.4849, "lat": 48.9508, "when": "2022-06-10T12:00:00Z",
        "obs": [
            ("ucdp", "artillery_fire", 0.85,
             "Intense Russian shelling of Sievierodonetsk; artillery fire pounds Sievierodonetsk day and night"),
            ("gdelt", "artillery_fire", 0.7,
             "Sievierodonetsk under sustained artillery fire; Russian shelling of Sievierodonetsk residential areas"),
        ],
    },
    {
        # Cross-modal: news + satellite agree on near-total destruction.
        "place": "Bakhmut", "lon": 37.9989, "lat": 48.5949, "when": "2023-05-20T09:00:00Z",
        "obs": [
            ("ucdp", "building_damaged", 0.9,
             "Bakhmut reduced to ruins; near-total destruction of buildings across Bakhmut after months of fighting"),
            ("copernicus_sar", "building_damaged", 0.8,
             "Sentinel-1 satellite radar shows extensive building destruction across Bakhmut"),
        ],
    },
]


def build_raws(theater_id: str) -> list[RawObservation]:
    raws: list[RawObservation] = []
    for inc in INCIDENTS:
        t0 = datetime.fromisoformat(inc["when"].replace("Z", "+00:00")).astimezone(timezone.utc)
        for family, obs_type, conf, text in inc["obs"]:
            raws.append(RawObservation(
                theater_id=theater_id, source_id=_SOURCE_ID[family], source_family_id=family,
                modality=_MODALITY[family], obs_type=obs_type,
                occurred_start=t0, occurred_end=t0 + timedelta(hours=1),
                geo=GeoRef(lon=inc["lon"], lat=inc["lat"], precision_m=1000.0),
                text=text, lang="en", self_conf=conf,
                meta={"seeded": True, "place": inc["place"]},
            ))
    return raws


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m ingest.seed_incidents")
    p.add_argument("--theater", default="ua_donbas")
    args = p.parse_args()

    ctx = build_context(args.theater)
    counters = ingest_raws(build_raws(args.theater), ctx)
    print("=" * 60)
    print("SEEDED DEMO INCIDENTS")
    print("=" * 60)
    for inc in INCIDENTS:
        fams = ", ".join(sorted({o[0] for o in inc["obs"]}))
        print(f"  {inc['place']:<18} {len(inc['obs'])} obs · sources: {fams}")
    print("-" * 60)
    print(f"  ingested: {counters['ingested']}  rejected: {counters['rejected']} {counters.get('by_reason') or ''}")
    print("=" * 60)
    print("Next: python -m fusion.run --theater " + args.theater + "   (project into events)")


if __name__ == "__main__":
    main()
