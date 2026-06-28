"""
THE coarsening boundary — the single analytical-not-targeting enforcement point.

Every geometry the API emits is reduced to its 1km MGRS cell (centroid or cell polygon),
derived purely from `cell_id`. Two hard rules, enforced here and nowhere else:

  1. NO precise coordinate ever leaves the API. The read model does not even store one
     (the ingest contract discards it at write time), but we still assert defensively:
     any precise-coordinate field on a row is a `CoarseningViolation`.
  2. NO 'person' entity. The entity enum has no 'person' value by design; `assert_no_person`
     is the runtime expression of that — a row claiming a person/individual is rejected.

This module is dependency-light (shapely + the grid MGRS helper + the YAML config) so it is
unit-testable offline with no FastAPI, DB, or network.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from grid.mgrs_1km import cell_id_to_polygon

_CFG = Path(__file__).parent.parent / "config" / "coarsening.yaml"

# Keys that would leak a precise input coordinate. The cell centroid we DERIVE from cell_id
# is a coarse, public reference and is emitted under explicit keys ('centroid'/'geometry'),
# never these.
_PRECISE_COORD_KEYS = frozenset({
    "lon", "lat", "longitude", "latitude",
    "precise_geom", "exact_geom", "exact_lon", "exact_lat",
    "x", "y", "easting", "northing",
})

# Tokens that would indicate person-level targeting. 'person' is NOT in the entity enum.
_PERSON_TOKENS = frozenset({"person", "individual", "people"})


class CoarseningViolation(Exception):
    """Raised when a row would expose a precise coordinate or a person entity through the API."""


@lru_cache(maxsize=1)
def _coarsening_cfg() -> dict:
    with open(_CFG) as f:
        return yaml.safe_load(f)["coarsening"]


def assert_no_precise_coords(row: dict) -> None:
    """Fail loud if a row carries any precise-coordinate field with a non-null value."""
    for k, v in row.items():
        if k.lower() in _PRECISE_COORD_KEYS and v is not None:
            raise CoarseningViolation(
                f"precise coordinate field {k!r} present in API payload — "
                "the API emits cell-only geometry (analytical-not-targeting)."
            )


def assert_no_person(row: dict) -> None:
    """Fail loud if a row references a person/individual entity.

    Honors config/coarsening.yaml `assert_no_person` (always true for the MVP). Checks the
    declared entity kind and any 'kind'/'entity_kind' field — the schema has no 'person' enum
    value, so this should never fire; it is the belt-and-braces runtime guard.
    """
    if not _coarsening_cfg().get("assert_no_person", True):
        return
    for field in ("kind", "entity_kind", "entity_type"):
        val = row.get(field)
        if isinstance(val, str) and val.strip().lower() in _PERSON_TOKENS:
            raise CoarseningViolation(
                f"{field}={val!r} — 'person' is not a permitted entity kind "
                "(analytical-not-targeting: no person-level entities)."
            )


def cell_geometry(cell_id: str, mode: str | None = None) -> dict:
    """Derive GeoJSON geometry for a cell from its cell_id ALONE.

    mode 'cell_centroid' → Point at the cell centroid; 'cell_polygon' → the 1km cell box.
    Defaults to the configured `default_mode`. No precise coordinate is ever consulted.
    """
    mode = mode or _coarsening_cfg().get("default_mode", "cell_centroid")
    poly = cell_id_to_polygon(cell_id)
    if mode == "cell_polygon":
        ring = [[round(x, 6), round(y, 6)] for x, y in poly.exterior.coords]
        return {"type": "Polygon", "coordinates": [ring]}
    c = poly.centroid
    return {"type": "Point", "coordinates": [round(c.x, 6), round(c.y, 6)]}


def _mode_for_modality(modality: str | None) -> str:
    cfg = _coarsening_cfg()
    if modality and modality in cfg.get("per_modality", {}):
        return cfg["per_modality"][modality]
    return cfg.get("default_mode", "cell_centroid")


def coarsen_event(row: dict) -> dict:
    """Reduce a read-model event row to a cell-only, API-safe dict.

    Strips nothing the read model holds (it holds no precise coord), asserts the two hard
    rules, then attaches centroid + cell polygon derived from cell_id.
    """
    assert_no_precise_coords(row)
    assert_no_person(row)
    cell_id = row["cell_id"]
    out = dict(row)
    out["cell_id"] = cell_id
    centroid = cell_geometry(cell_id, "cell_centroid")
    out["centroid"] = centroid
    out["geometry"] = cell_geometry(cell_id, "cell_polygon")
    # Human-readable place name from the (already-coarse) centroid — "Avdiivka", not a cell code.
    out["place"] = _place_label(centroid)
    return out


def _place_label(centroid: dict) -> dict | None:
    """Nearest-settlement label for a cell centroid Point geometry (best-effort, never fatal)."""
    try:
        from api.places import nearest_place
        lon, lat = centroid["coordinates"]
        return nearest_place(lon, lat)
    except Exception:  # noqa: BLE001 — a place label is a nicety; never break the event payload
        return None


def coarsen_cell(row: dict) -> dict:
    """Reduce a cell-context row to an API-safe dict (centroid + cell polygon)."""
    assert_no_precise_coords(row)
    assert_no_person(row)
    cell_id = row["cell_id"]
    out = dict(row)
    out["centroid"] = cell_geometry(cell_id, "cell_centroid")
    out["geometry"] = cell_geometry(cell_id, "cell_polygon")
    return out


def coarsen_observation(row: dict) -> dict:
    """Reduce an evidence-trail observation row to cell-only.

    The observation log stores only cell_id + uncertainty radius (no precise coord), so this is
    mostly an assertion; geometry is derived from the cell at the configured per-modality mode.
    """
    assert_no_precise_coords(row)
    assert_no_person(row)
    cell_id = row.get("cell_id")
    out = dict(row)
    if cell_id:
        out["centroid"] = cell_geometry(cell_id, _mode_for_modality(row.get("modality")))
    return out
