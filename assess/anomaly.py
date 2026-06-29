"""
Per-cell anomaly detection (Phase 4a) — "where is something unusual happening".

Two deterministic signals over the chronology, per 1km cell:
  • activity_spike — a cluster of events in the recent window (a flare-up).
  • escalation     — a NEW, more-severe event type appearing in a cell that previously only saw
                     milder activity (e.g. shelling → a confirmed strike).

Pure (no DB, `now` injected). Absolute-density spikes (not baseline-relative) because the live
corpus is bimodal in time; this stays meaningful as continuous coverage fills in.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta


def cell_anomalies(events: list[dict], now: datetime, cfg) -> list[dict]:
    """Return anomaly assessments. Each event dict needs cell_id, event_type, occurred_start."""
    by_cell: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_cell[e["cell_id"]].append(e)

    recent_cut = now - timedelta(days=cfg.recent_window_days)
    out: list[dict] = []
    for cell, evs in by_cell.items():
        recent = [e for e in evs if e["occurred_start"] >= recent_cut]
        prior = [e for e in evs if e["occurred_start"] < recent_cut]

        # --- activity spike: a cluster of recent events in this cell ---
        if len(recent) >= cfg.spike_min_recent:
            score = min(1.0, len(recent) / (cfg.spike_min_recent * cfg.spike_ratio))
            out.append({
                "kind": "anomaly", "subkind": "activity_spike", "cell_id": cell,
                "score": round(score, 4), "n_recent": len(recent),
                "rationale": f"{len(recent)} events here in the last "
                             f"{int(cfg.recent_window_days)} days — a flare-up.",
            })

        # --- escalation: a new, more-severe type appears where only milder activity was seen ---
        if prior and recent:
            prior_types = {e["event_type"] for e in prior}
            prior_worst = max((cfg.severity(t) for t in prior_types), default=0.0)
            new_recent = [e for e in recent if e["event_type"] not in prior_types]
            if new_recent:
                worst = max(new_recent, key=lambda e: cfg.severity(e["event_type"]))
                sev = cfg.severity(worst["event_type"])
                if sev >= 0.6 and sev > prior_worst:
                    out.append({
                        "kind": "anomaly", "subkind": "escalation", "cell_id": cell,
                        "score": round(sev, 4), "new_type": worst["event_type"],
                        "rationale": f"new {worst['event_type'].replace('_', ' ')} where only "
                                     f"{', '.join(sorted(t.replace('_', ' ') for t in prior_types))} "
                                     f"had been seen — possible escalation.",
                    })
    out.sort(key=lambda a: a["score"], reverse=True)
    return out
