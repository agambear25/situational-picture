"""
Collection-gap scoring (Phase 4c) — "where to point collection next".

NOT "every uncorroborated event" (the conflict feed is mostly single-source, that's thousands of
noise). A gap worth an analyst's time is a RECENT, HIGH-STAKES event seen by only ONE independent
source — the claims to chase a second source on. Pure + deterministic (`now` injected).
"""
from __future__ import annotations

from assess.significance import recency


def collection_gap(event: dict, now, cfg) -> dict | None:
    """Score one event as a collection gap, or None if it isn't one."""
    if int(event.get("n_independent_families") or 0) >= 2:
        return None                                      # already corroborated — no gap
    sev = cfg.severity(event["event_type"])
    if sev < cfg.gaps_min_severity:
        return None                                      # not high-stakes enough to chase
    occ = event.get("occurred_start")
    if occ is None:
        return None
    age_days = (now - occ).total_seconds() / 86400.0
    if age_days > cfg.gaps_recent_days:
        return None                                      # only recent gaps are actionable
    rec = recency(occ, now, cfg.recency_tau_days)
    rec_factor = cfg.recency_floor + (1.0 - cfg.recency_floor) * rec
    score = round(sev * rec_factor, 4)
    if score < cfg.gaps_min_score:
        return None
    etype = str(event["event_type"]).replace("_", " ")
    return {
        "score": score,
        "rationale": f"recent {etype} from a single source — needs a second independent source to confirm.",
    }
