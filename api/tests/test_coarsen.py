"""The coarsening boundary is the analytical-not-targeting enforcement — test it hard, offline."""
from __future__ import annotations

import pytest

from grid.mgrs_1km import to_cell_id
from api.coarsen import (
    CoarseningViolation, assert_no_person, assert_no_precise_coords,
    cell_geometry, coarsen_cell, coarsen_event, coarsen_observation,
)

CELL = to_cell_id(37.749, 48.139)  # a real 1km MGRS cell in the Donbas AOI


def test_clean_row_passes():
    assert_no_precise_coords({"cell_id": CELL, "event_type": "strike"})


@pytest.mark.parametrize("key", ["lon", "lat", "longitude", "latitude", "easting", "northing", "x", "y"])
def test_precise_coord_field_raises(key):
    with pytest.raises(CoarseningViolation):
        assert_no_precise_coords({"cell_id": CELL, key: 37.7})


def test_precise_coord_none_value_is_ok():
    # a null coordinate field is not a leak
    assert_no_precise_coords({"cell_id": CELL, "lon": None, "lat": None})


def test_assert_no_person_raises_on_person_kinds():
    for row in ({"kind": "person"}, {"entity_kind": "Individual"}, {"entity_type": "people"}):
        with pytest.raises(CoarseningViolation):
            assert_no_person(row)


def test_assert_no_person_allows_real_entity_kinds():
    for k in ("formation", "site", "vessel", "vehicle", "unit", "installation"):
        assert_no_person({"kind": k})  # must not raise


def test_cell_geometry_centroid_and_polygon():
    c = cell_geometry(CELL, "cell_centroid")
    assert c["type"] == "Point" and len(c["coordinates"]) == 2
    p = cell_geometry(CELL, "cell_polygon")
    assert p["type"] == "Polygon" and len(p["coordinates"][0]) == 5  # closed ring


def test_coarsen_event_attaches_cell_only_geometry():
    out = coarsen_event({"event_id": "e1", "cell_id": CELL, "event_type": "strike",
                         "confidence_band": "High"})
    assert out["cell_id"] == CELL
    assert out["centroid"]["type"] == "Point"
    assert out["geometry"]["type"] == "Polygon"


def test_coarsen_event_rejects_precise_coord_leak():
    with pytest.raises(CoarseningViolation):
        coarsen_event({"event_id": "e1", "cell_id": CELL, "lat": 48.139, "lon": 37.749})


def test_coarsen_event_rejects_person_entity():
    with pytest.raises(CoarseningViolation):
        coarsen_event({"event_id": "e1", "cell_id": CELL, "kind": "person"})


def test_coarsen_cell_and_observation():
    assert coarsen_cell({"cell_id": CELL, "label": "Avdiivka-16"})["geometry"]["type"] == "Polygon"
    obs = coarsen_observation({"cell_id": CELL, "modality": "thermal", "obs_type": "fire"})
    assert obs["centroid"]["type"] == "Point"
