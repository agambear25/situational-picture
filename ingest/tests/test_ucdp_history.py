"""Offline gate for the UCDP (cell, period) aggregator — pure build_raws over a tiny CSV."""
from __future__ import annotations

import csv
from pathlib import Path

from ingest.ucdp_history import build_raws

BBOX = (36.0, 46.8, 39.5, 49.5)
TYPE_MAP = {"Armed Conflict (Government)": "strike", "One-sided violence": "other", "_default": "other"}
_COLS = ["latitude", "longitude", "date_start", "date_end", "type_of_violence", "best",
         "conflict_name", "adm_1", "adm_2", "where_coordinates"]


def _csv(rows, tmp: Path) -> Path:
    p = tmp / "ucdp.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _COLS})
    return p


def test_aggregates_per_cell_month_with_real_activity_window(tmp_path):
    base = dict(latitude="48.14", longitude="37.75", type_of_violence="1",
                conflict_name="War in Ukraine", adm_2="Avdiivka")
    rows = [
        {**base, "date_start": "2022-03-05", "date_end": "2022-03-05", "best": "3"},
        {**base, "date_start": "2022-03-20", "date_end": "2022-03-20", "best": "2"},
        {**base, "date_start": "2022-04-01", "date_end": "2022-04-01", "best": "0"},
    ]
    raws = build_raws("ua_donbas", BBOX, _csv(rows, tmp_path), TYPE_MAP, "month")
    assert len(raws) == 2                                  # two distinct (cell, month) groups
    march = next(r for r in raws if r.meta["period"] == "2022-03")
    assert march.meta["n_events"] == 2 and march.meta["fatalities"] == 5
    assert march.occurred_start.date().isoformat() == "2022-03-05"   # first activity that month
    assert march.occurred_end.date().isoformat() == "2022-03-20"     # last activity that month
    assert march.obs_type == "strike" and march.source_family_id == "ucdp"
    assert march.modality == "text" and march.self_conf and march.self_conf > 0.7


def test_out_of_aoi_dropped(tmp_path):
    rows = [dict(latitude="0.0", longitude="0.0", date_start="2022-03-05", date_end="2022-03-05",
                 type_of_violence="1", best="1", conflict_name="x")]
    assert build_raws("ua_donbas", BBOX, _csv(rows, tmp_path), TYPE_MAP, "month") == []


def test_day_granularity_keeps_dates_separate(tmp_path):
    base = dict(latitude="48.14", longitude="37.75", type_of_violence="1", best="1", conflict_name="w")
    rows = [{**base, "date_start": d, "date_end": d} for d in ("2022-03-05", "2022-03-06")]
    raws = build_raws("ua_donbas", BBOX, _csv(rows, tmp_path), TYPE_MAP, "day")
    assert len(raws) == 2                                  # same cell, two days → two obs
