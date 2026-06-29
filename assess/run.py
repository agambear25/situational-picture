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

logger = logging.getLogger(__name__)


def run(theater_id: str, now: datetime | None = None) -> dict:
    import psycopg2
    from assess.db import load_events, cell_type_counts, write_assessments

    now = now or datetime.now(timezone.utc)
    cfg = load_assessment_config()
    conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
    try:
        events = load_events(conn, theater_id)
        counts = cell_type_counts(events)

        assessments: list[dict] = []
        for e in events:
            s = significance(e, now, cfg, counts[e["cell_id"]])
            if s["score"] >= cfg.min_significance:
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

        write_assessments(conn, theater_id, assessments, now)
        return {"theater_id": theater_id, "events": len(events),
                "significance": n_sig, "anomalies": len(anomalies)}
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
    print("=" * 56)


if __name__ == "__main__":
    main()
