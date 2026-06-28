"""Offline gate for the UNOSAT ground-truth scorer (no DB, no GEE)."""
from __future__ import annotations

from grid.mgrs_1km import to_cell_id
from eval.unosat import truth_cells, score, recall_by_grade, REAL_DAMAGE_GRADES

BBOX = (36.0, 46.8, 39.5, 49.5)   # Donbas AOI

# Three well-separated points inside the AOI → three distinct cells.
P1 = (37.50, 48.10)   # destroyed
P2 = (37.80, 48.30)   # severe
P3 = (38.10, 47.50)   # moderate
OUTSIDE = (40.0, 50.0)  # outside the AOI → must be ignored
CELL1, CELL2, CELL3 = to_cell_id(*P1), to_cell_id(*P2), to_cell_id(*P3)


def _feat(lon, lat, damage, city):
    return {"properties": {"damage": damage, "city": city},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def _features():
    return [
        _feat(*P1, 4, "Avdiivka"),
        _feat(*P1, 3, "Avdiivka"),     # second point in the same cell → still one truth cell
        _feat(*P2, 3, "Bakhmut"),
        _feat(*P3, 2, "Mariupol"),
        _feat(*OUTSIDE, 4, "Kyiv"),    # outside AOI
        _feat(37.6, 48.0, 1, "Slight"),  # grade 1 → not "real damage", excluded by default
    ]


def test_truth_cells_align_and_filter():
    truth = truth_cells(_features(), BBOX, REAL_DAMAGE_GRADES)
    assert set(truth) == {CELL1, CELL2, CELL3}     # 3 cells; outside + grade-1 excluded
    assert truth[CELL1]["worst_grade"] == 4        # max grade in the cell
    assert truth[CELL1]["n_points"] == 2
    assert truth[CELL2]["cities"] == {"Bakhmut"}


def test_perfect_detection_scores_one():
    truth = truth_cells(_features(), BBOX)
    r = score({CELL1, CELL2, CELL3}, truth)
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_partial_detection_precision_and_recall():
    truth = truth_cells(_features(), BBOX)
    # detect 2 of 3 truth cells + 1 spurious cell
    spurious = to_cell_id(36.5, 47.0)
    r = score({CELL1, CELL2, spurious}, truth)
    assert r["recall"] == round(2 / 3, 4)          # found 2 of 3
    assert r["precision"] == round(2 / 3, 4)       # 2 of 3 detections were real
    assert r["false_positives"] == 1
    assert CELL3 in r["missed_truth"]


def test_empty_detection_is_zero_recall_not_crash():
    truth = truth_cells(_features(), BBOX)
    r = score(set(), truth)
    assert r["recall"] == 0.0 and r["precision"] == 0.0 and r["n_truth"] == 3


def test_buffer_matches_a_near_miss():
    truth = truth_cells(_features(), BBOX)
    # a detection ~1 cell away from CELL1 should miss at exact match but hit with a 1.5km buffer
    near = to_cell_id(P1[0] + 0.012, P1[1])        # ~0.9km east, likely the adjacent cell
    if near == CELL1:
        near = to_cell_id(P1[0] + 0.02, P1[1])
    assert score({near}, truth, buffer_m=0)["true_positives"] == 0
    assert score({near}, truth, buffer_m=2000)["recall"] > 0


def test_recall_by_grade_breaks_out_destroyed():
    truth = truth_cells(_features(), BBOX)
    # detect only the destroyed cell (CELL1, worst grade 4)
    by_grade = recall_by_grade({CELL1}, truth)
    assert by_grade[4]["recall"] == 1.0            # the one grade-4 cell was found
    assert by_grade[4]["label"] == "Destroyed"
    assert by_grade[2]["recall"] == 0.0            # the moderate cell was not detected
