"""
Read context-builder (synthesis layer) — the grounded, coarse, structured facts the per-area Read
reasons over. This is what both the local LLM and the deterministic fallback consume, so the Read
is always grounded in the same numbers.

PURE. Reads only type / band / date / place-label / family / n_families — NEVER a precise
coordinate or a person — so the analytical-not-targeting boundary holds even inside the synthesis
input. Unit-tested.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

# Friendly sensor names for provenance in the Read.
_SENSOR = {
    "ucdp": "news/reports", "gdelt": "news/reports", "nasa_firms": "thermal", "nasa_modis": "thermal",
    "unosat": "damage assessment", "copernicus_sar": "satellite radar",
    "copernicus_optical": "satellite optical",
}
_BAND_RANK = {"High": 0, "Medium": 1, "Low": 2, "Rumored": 3}


def _place(e) -> str | None:
    p = e.get("place")
    if isinstance(p, dict):
        return p.get("label")
    return e.get("place_label")


def build_context(area_label: str, events: list[dict], anomalies: list[dict],
                  families, now, *, window_days: float = 45.0, top_n: int = 5) -> dict:
    """Assemble the Read context. `events` need event_type / confidence_band / occurred_start and a
    place label (dict `place` or `place_label`); raw coords must NOT be present (asserted)."""
    for e in events:
        assert "lon" not in e and "lat" not in e and "geom" not in e, \
            "precise coordinates must not enter the synthesis context"

    by_type = Counter(e["event_type"] for e in events)
    by_band = Counter(e.get("confidence_band") or "Rumored" for e in events)
    recent_cut = now - timedelta(days=window_days)
    prior_cut = now - timedelta(days=2 * window_days)
    recent = sum(1 for e in events if e.get("occurred_start") and e["occurred_start"] >= recent_cut)
    prior = sum(1 for e in events
                if e.get("occurred_start") and prior_cut <= e["occurred_start"] < recent_cut)

    top = sorted(events, key=lambda e: (_BAND_RANK.get(e.get("confidence_band"), 3),
                 -(e["occurred_start"].timestamp() if e.get("occurred_start") else 0)))[:top_n]
    top_events = [{"type": e["event_type"], "band": e.get("confidence_band"), "place": _place(e),
                   "date": e["occurred_start"].date().isoformat() if e.get("occurred_start") else None}
                  for e in top]

    dates = [e["occurred_start"] for e in events if e.get("occurred_start")]
    span = ({"first": min(dates).date().isoformat(), "last": max(dates).date().isoformat()}
            if dates else None)

    return {
        "area": area_label,
        "n_events": len(events),
        "by_type": dict(by_type.most_common()),
        "by_band": {b: by_band.get(b, 0) for b in ("High", "Medium", "Low", "Rumored")},
        "recent": recent, "prior": prior, "window_days": int(window_days),
        "top_events": top_events, "date_span": span,
        "anomalies": sorted({a.get("subkind") for a in anomalies if a.get("subkind")}),
        "sensors": sorted({_SENSOR.get(f, f) for f in families}),
    }
