"""
Control router — the coarse, own-curated 'who held what' backdrop for the chronology.

GET /control?date=YYYY-MM-DD  → each settlement's controlling side as of that date (+ caveat).

License-clean: served from config/control_ua_donbas.json (authored from public knowledge), never
ISW/DeepStateMap. City-level + approximate; the UI shows the caveat. Read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.control import control_as_of

router = APIRouter()


@router.get("/control")
def get_control(date: str | None = Query(default=None)) -> dict:
    """Controlling side per settlement as of `date` (None = latest). Coarse + illustrative."""
    return control_as_of(date)
