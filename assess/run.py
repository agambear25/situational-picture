"""
Assessment runner (Phase 4a) — the insights engine.

Reads world.event, scores significance per event + per-cell anomalies, materialises
world.assessment. Read-only over the read models; never touches the evidence log. Idempotent
(truncate + rebuild), like fusion.run. Run it after fusion.run:

    python -m assess.run --theater ua_donbas
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from assess.config import load_assessment_config
from assess.significance import significance
from assess.anomaly import cell_anomalies
from assess.exposure import exposure
from assess.gaps import collection_gap

logger = logging.getLogger(__name__)

# Exposure / gaps are priority lists, not exhaustive logs — keep the most actionable per kind.
_TOP_N = 80
_SIG_TOP_N = 250     # significance is the primary feed → store a deeper top-N than exposure/gaps


def _load_settlements(theater_id: str) -> list[dict]:
    import json
    from pathlib import Path
    p = Path(__file__).parent.parent / "config" / f"places_{theater_id}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("places", [])


def run(theater_id: str, now: datetime | None = None) -> dict:
    import psycopg2
    from assess.db import load_events, cell_type_counts, write_assessments

    now = now or datetime.now(timezone.utc)
    cfg = load_assessment_config()
    conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
    try:
        events = load_events(conn, theater_id)
        counts = cell_type_counts(events)

        # Significance: keep the top-N events by score (above a low noise floor) rather than an
        # absolute cutoff. Top-N is robust to score-scale shifts (e.g. the recency rebalance) and
        # gives every theater a proportional feed — a sparse theater still surfaces ITS most notable.
        assessments: list[dict] = []
        sig_scored = [(e, significance(e, now, cfg, counts[e["cell_id"]])) for e in events]
        sig_scored = [(e, s) for e, s in sig_scored if s["score"] >= cfg.min_significance]
        sig_scored.sort(key=lambda t: t[1]["score"], reverse=True)
        for e, s in sig_scored[:_SIG_TOP_N]:
            assessments.append({
                "kind": "significance", "subkind": None, "event_id": e["event_id"],
                "cell_id": e["cell_id"], "score": s["score"],
                "components": s["components"], "rationale": s["rationale"],
            })
        n_sig = len(assessments)

        anomalies = cell_anomalies(events, now, cfg)
        for a in anomalies:
            assessments.append({
                "kind": "anomaly", "subkind": a["subkind"], "event_id": None,
                "cell_id": a["cell_id"], "score": a["score"],
                "components": {k: a[k] for k in ("n_recent", "new_type") if k in a},
                "rationale": a["rationale"],
            })

        # 4c — exposure (events near populated places) + collection-gap (recent single-source).
        settlements = _load_settlements(theater_id)
        exp = sorted(((e, exposure(e, settlements, cfg)) for e in events),
                     key=lambda t: (t[1] or {}).get("score", 0), reverse=True)
        exp = [(e, x) for e, x in exp if x][:_TOP_N]
        for e, x in exp:
            assessments.append({
                "kind": "exposure", "subkind": None, "event_id": e["event_id"],
                "cell_id": e["cell_id"], "score": x["score"],
                "components": {"settlement": x["settlement"], "distance_km": x["distance_km"]},
                "rationale": x["rationale"],
            })

        gaps = sorted(((e, collection_gap(e, now, cfg)) for e in events),
                      key=lambda t: (t[1] or {}).get("score", 0), reverse=True)
        gaps = [(e, g) for e, g in gaps if g][:_TOP_N]
        for e, g in gaps:
            assessments.append({
                "kind": "gaps", "subkind": None, "event_id": e["event_id"],
                "cell_id": e["cell_id"], "score": g["score"],
                "components": {}, "rationale": g["rationale"],
            })

        write_assessments(conn, theater_id, assessments, now)
        return {"theater_id": theater_id, "events": len(events), "significance": n_sig,
                "anomalies": len(anomalies), "exposure": len(exp), "gaps": len(gaps)}
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m assess.run")
    p.add_argument("--theater", default="ua_donbas")
    args = p.parse_args()
    s = run(args.theater)
    print("=" * 56)
    print(f"ASSESSMENTS — {s['theater_id']}")
    print("=" * 56)
    print(f"  events scored      : {s['events']}")
    print(f"  significance rows  : {s['significance']}")
    print(f"  anomalies flagged  : {s['anomalies']}")
    print(f"  exposure flags     : {s['exposure']}")
    print(f"  collection gaps    : {s['gaps']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
