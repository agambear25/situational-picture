"""
Per-area attention classifier (AOR workflow) — escalating / steady / quieting.

The signal that ranks a watched area on the "My Watch" home screen: does this area need the
analyst now? Pure + deterministic (no DB, no LLM, `now` injected) — it reads the area's events plus
the anomaly assessments already scoped to its cells, and compares recent activity to the prior
window. An escalation anomaly or a clear upward trend ⇒ escalating; a clear drop ⇒ quieting.
"""
from __future__ import annotations

from datetime import timedelta


def classify_attention(events: list[dict], anomalies: list[dict], now, *,
                       window_days: float = 45.0, escalate_ratio: float = 1.5,
                       quiet_ratio: float = 0.6, min_recent_new: int = 3) -> dict:
    """`events` = the area's events (need occurred_start, n_independent_families). `anomalies` =
    the anomaly assessments scoped to the area's cells (subkind activity_spike|escalation)."""
    recent_cut = now - timedelta(days=window_days)
    prior_cut = now - timedelta(days=2 * window_days)
    recent = prior = confirmed = 0
    for e in events:
        if int(e.get("n_independent_families") or 0) >= 2:
            confirmed += 1
        occ = e.get("occurred_start")
        if occ is None:
            continue
        if occ >= recent_cut:
            recent += 1
        elif occ >= prior_cut:
            prior += 1

    has_escalation = any(a.get("subkind") == "escalation" for a in anomalies)
    has_spike = any(a.get("subkind") == "activity_spike" for a in anomalies)

    if has_escalation or (prior and recent >= prior * escalate_ratio) or (not prior and recent >= min_recent_new):
        status = "escalating"
    elif (prior and recent <= prior * quiet_ratio) or (recent == 0 and prior):
        status = "quieting"
    else:
        status = "steady"

    score = round(recent / prior, 2) if prior else float(recent)
    return {"status": status, "recent": recent, "prior": prior, "confirmed": confirmed,
            "trend_score": score, "has_spike": has_spike, "has_escalation": has_escalation}


# Sort key for ranking the watch list: escalating first, then by how much activity / how confirmed.
_RANK = {"escalating": 0, "steady": 1, "quieting": 2}


def attention_sort_key(att: dict, n_events: int) -> tuple:
    return (_RANK.get(att["status"], 1), -att.get("recent", 0), -n_events)
