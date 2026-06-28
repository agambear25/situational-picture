"""
Imagery ingest runner (Phase 3e+) — the live path for change detectors.

Pipeline:  GEE before/after S1 tiles → registered Detector (via the determinism cache) →
coarsened imagery Observations → append-only log. Then `python -m fusion.run` projects them
into world.event, where a SAR change in the same cell as a text/thermal event corroborates it
(second independent family → noisy-OR lift above Rumored).

Gated by `config/runtime.yaml live_feeds_enabled`, exactly like ingest/run.py — imagery is just
another feed. The detection logic is pure + unit-tested (test_sar_logratio.py); this module is
the I/O wiring (GEE + DB), kept dependency-injected so its assembly is testable offline.

Usage:
    python -m ingest.imagery.run --detector sar_logratio \
        --before 2024-02-01 2024-02-20 --after 2024-03-01 2024-03-20
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def fetch_window_tiles(theater_id, before, after, *, downloader=None, cache=None):
    """Fetch S1 tiles for a 'before' window and an 'after' window and return them combined.
    `before`/`after` are (start, end) ISO date pairs. downloader/cache are injectable for tests."""
    from ingest.imagery.gee_s1 import fetch_s1_tiles
    b0, b1 = before
    a0, a1 = after
    tiles = fetch_s1_tiles(theater_id, b0, b1, downloader=downloader, cache=cache)
    tiles += fetch_s1_tiles(theater_id, a0, a1, downloader=downloader, cache=cache)
    return tiles


def detect_and_persist(detector, tiles, conn, resolver, embedder, det_cache, *, bus=None) -> dict:
    """Run a detector over a tile stack through the determinism cache and append the resulting
    coarsened Observations to the log. Returns counters; nothing is dropped silently."""
    from ingest.imagery.caches import cached_detect_multi, detections_to_observations
    from ingest.contract import persist_observation

    cds, rejected = cached_detect_multi(detector, tiles, det_cache, resolver, detector.theater_id)
    obs = detections_to_observations(
        cds, detector.source_id, detector.family_id, detector.theater_id, embedder=embedder)

    counters = {"detections": len(cds), "ingested": 0, "exact_dup": 0,
                "rejected": len(rejected), "by_reason": {}}
    for r in rejected:
        counters["by_reason"][r] = counters["by_reason"].get(r, 0) + 1
    for o in obs:
        obs_id, reason = persist_observation(conn, o, bus=bus)
        if obs_id is not None:
            counters["ingested"] += 1
        else:
            counters["exact_dup"] += 1
    logger.info("imagery ingest complete: %s", counters)
    return counters


def run(theater_id, detector_name, before, after) -> dict:
    """Live path: GEE fetch → detect → persist. Needs the full stack + GEE auth on the user Mac."""
    # Import the detector module so it self-registers, then look it up.
    import ingest.imagery.sar_logratio  # noqa: F401 — registers 'sar_logratio'
    from ingest.imagery.framework import registry
    from ingest.imagery.caches import PgDetectionCache
    from ingest.pipeline import build_context

    detector = registry.get(detector_name)
    ctx = build_context(theater_id)
    tiles = fetch_window_tiles(theater_id, before, after)
    logger.info("fetched %d S1 tiles (before+after) for %s", len(tiles), theater_id)
    return detect_and_persist(
        detector, tiles, ctx.conn, ctx.resolver, ctx.embedder, PgDetectionCache(ctx.conn),
        bus=ctx.bus)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m ingest.imagery.run")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--detector", default="sar_logratio")
    p.add_argument("--before", nargs=2, metavar=("START", "END"), required=True,
                   help="baseline window, ISO dates e.g. 2024-02-01 2024-02-20")
    p.add_argument("--after", nargs=2, metavar=("START", "END"), required=True,
                   help="event window, ISO dates e.g. 2024-03-01 2024-03-20")
    p.add_argument("--force", action="store_true", help="override the live-feed gate (NOT for CI)")
    args = p.parse_args()

    from ingest.run import live_feeds_enabled
    if not live_feeds_enabled() and not args.force:
        print("REFUSED: live_feeds_enabled is false in config/runtime.yaml. Run the eval gate first.",
              file=sys.stderr)
        sys.exit(2)

    summary = run(args.theater, args.detector, tuple(args.before), tuple(args.after))
    print("=" * 60)
    print(f"IMAGERY INGEST — {args.detector} @ {args.theater}")
    print("=" * 60)
    print(f"  detections (coarsened) : {summary['detections']}")
    print(f"  written to log         : {summary['ingested']}")
    print(f"  exact dups             : {summary['exact_dup']}")
    print(f"  rejected (no cell)     : {summary['rejected']} {summary['by_reason'] or ''}")
    print("=" * 60)
    print("Next: python -m fusion.run --theater %s   (project log → events)" % args.theater)


if __name__ == "__main__":
    main()
