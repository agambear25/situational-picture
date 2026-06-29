"""
Coarse, own-curated control timeline (who held each place, at city level, over time).

Strictly license-clean: this is authored from general public knowledge (config/control_ua_donbas.json),
NOT ingested from ISW or DeepStateMap (which are forbidden by the project's standing rule). It is a
rough backdrop for the chronology — city-level, approximate dates — never a live frontline.

control_as_of(date) resolves each settlement's controlling side at a date by replaying its changes.
The API serves it so the UI can tint the map by control for the date on the time slider.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_PATH = Path(__file__).resolve().parents[1] / "config" / "control_ua_donbas.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default_side": "UA", "settlements": [], "sides": {}, "_note": ""}


def control_as_of(date: Optional[str] = None) -> dict:
    """Each settlement's controlling side as of `date` (ISO 'YYYY-MM-DD'; None = latest/now).

    Replays each settlement's dated changes, applying the last one on/before the date. Returns the
    list plus the honest caveat so the UI can show it. Coords are public city points (general
    knowledge), not event/observation geometry — the analytical-not-targeting boundary is about
    precise event locations, which this is not.
    """
    data = _data()
    default = data.get("default_side", "UA")
    cutoff = date  # 'YYYY-MM-DD' compares lexically with the change dates
    out = []
    for s in data.get("settlements", []):
        side = default
        for ch in sorted(s.get("changes", []), key=lambda c: c["date"]):
            if cutoff is None or ch["date"] <= cutoff:
                side = ch["side"]
        out.append({"name": s["name"], "lon": s["lon"], "lat": s["lat"], "side": side})
    return {
        "as_of": date or "now",
        "sides": data.get("sides", {}),
        "caveat": data.get("_note", ""),
        "settlements": out,
    }
