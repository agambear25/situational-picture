"""
Significance scoring (Phase 4a) — "what to look at first".

significance = severity × confidence × recency × novelty, each in [0,1], so the product is a
calibrated [0,1] priority. Pure + deterministic (no DB, no clock — `now` is injected), so it is
unit-tested directly. Every score carries its components and a plain rationale, because the point
of the assessment layer is EXPLAINABLE prioritisation, not a black-box number.
"""
from __future__ import annotations

import math
from datetime import datetime


def recency(occurred_start: datetime, now: datetime, tau_days: float) -> float:
    """exp(-age_days / tau): a fresh event scores ~1, an old one decays toward 0."""
    age_days = max(0.0, (now - occurred_start).total_seconds() / 86400.0)
    return math.exp(-age_days / tau_days) if tau_days > 0 else (1.0 if age_days == 0 else 0.0)


def novelty(event_type: str, cell_type_counts: dict[str, int]) -> float:
    """How unusual this event type is in its cell. First-of-its-kind → 1.0; the Nth repeat → 1/N.
    So a lone strike in a quiet cell outranks the 5th fire in a fire-prone one."""
    count = max(1, cell_type_counts.get(event_type, 1))   # count includes this event
    return 1.0 / count


def significance(event: dict, now: datetime, cfg, cell_type_counts: dict[str, int]) -> dict:
    """Score one event. `event` needs event_type, confidence, occurred_start (tz-aware),
    n_independent_families. `cell_type_counts` is {event_type: count} over the event's cell."""
    sev = cfg.severity(event["event_type"])
    conf = float(event.get("confidence") or 0.0)
    rec = recency(event["occurred_start"], now, cfg.recency_tau_days)
    rec_factor = cfg.recency_floor + (1.0 - cfg.recency_floor) * rec   # floor keeps history alive
    nov = novelty(event["event_type"], cell_type_counts)
    score = sev * conf * rec_factor * nov
    return {
        "score": round(score, 4),
        "components": {"severity": round(sev, 3), "confidence": round(conf, 3),
                       "recency": round(rec_factor, 3), "novelty": round(nov, 3)},
        "rationale": _rationale(event, sev, conf, rec, nov),   # raw rec drives the wording
    }


def _rationale(event: dict, sev: float, conf: float, rec: float, nov: float) -> str:
    parts = []
    etype = str(event.get("event_type", "event")).replace("_", " ")
    parts.append(f"{'high' if sev >= 0.75 else 'moderate' if sev >= 0.5 else 'low'}-severity {etype}")
    fams = int(event.get("n_independent_families") or 0)
    parts.append("confirmed by several sources" if fams >= 2 else "single-source (unconfirmed)")
    parts.append("very recent" if rec >= 0.7 else "recent" if rec >= 0.3 else "older")
    if nov >= 0.99:
        parts.append("first of its kind in this area")
    elif nov <= 0.34:
        parts.append("one of many similar here")
    return "; ".join(parts) + "."
