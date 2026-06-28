"""
Offline grid tests — no DB, no mgrs network calls for CI.
Tests snap determinism, coarsening invariants, and local_seq stability.
"""
from __future__ import annotations

import pytest

from grid.local_seq import assign_local_seqs, build_label
from grid.types import Cell, CellResolution, GeoPrecision, is_valid_cell_id


# ---------------------------------------------------------------------------
# local_seq
# ---------------------------------------------------------------------------

def test_local_seq_deterministic():
    cells = [
        {"cell_id": "37UDB000000", "admin_l3": "Avdiivka"},
        {"cell_id": "37UDB000001", "admin_l3": "Avdiivka"},
        {"cell_id": "37UDB000002", "admin_l3": "Donetsk"},
    ]
    seqs = assign_local_seqs(cells)
    # Same call twice must produce same result
    assert seqs == assign_local_seqs(cells)
    # Two Avdiivka cells get seqs 1 and 2
    avdiivka_seqs = {c["cell_id"]: seqs[c["cell_id"]] for c in cells if c["admin_l3"] == "Avdiivka"}
    assert sorted(avdiivka_seqs.values()) == [1, 2]
    # Donetsk cell gets seq 1 in its own group
    assert seqs["37UDB000002"] == 1


def test_local_seq_order_independence():
    """Seq should be the same regardless of input order (sorting is internal)."""
    cells_a = [
        {"cell_id": "37UDB000000", "admin_l3": "Test"},
        {"cell_id": "37UDB000001", "admin_l3": "Test"},
    ]
    cells_b = list(reversed(cells_a))
    assert assign_local_seqs(cells_a) == assign_local_seqs(cells_b)


def test_build_label():
    assert build_label("Avdiivka", 16) == "Avdiivka-16"
    assert build_label(None, 3) == "Unknown-3"
    assert build_label("  Donetsk  ", 1) == "Donetsk-1"


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------

def test_cell_rejects_bad_id():
    with pytest.raises(ValueError):
        Cell(cell_id="NOTMGRS", theater_id="ua_donbas", label="x")


def test_cell_resolution_no_precise_coord():
    """CellResolution must not expose the precise input coordinate."""
    cell = Cell(cell_id="37UDB123456", theater_id="ua_donbas", label="Test-1")
    res = CellResolution(cell=cell, precision=GeoPrecision.PRECISE)
    # The resolution has no 'lon', 'lat', or 'geom' field
    assert not hasattr(res, "lon")
    assert not hasattr(res, "lat")
    assert not hasattr(res, "geom")


def test_place_only_flagged():
    cell = Cell(cell_id="37UDB123456", theater_id="ua_donbas", label="Test-1")
    res = CellResolution(
        cell=cell,
        precision=GeoPrecision.PLACE_ONLY,
        non_precise=True,
        flags=("place_name_only",),
    )
    assert res.non_precise is True
    assert "place_name_only" in res.flags
