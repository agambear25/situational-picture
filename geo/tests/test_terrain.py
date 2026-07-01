from geo.terrain import summarize_cells


def test_landcover_mix_and_unpaved_share():
    rows = [
        {"landcover_label": "cropland", "builtup_pct": 0.0, "road_surface": "paved"},
        {"landcover_label": "cropland", "builtup_pct": 0.1, "road_surface": "unpaved"},
        {"landcover_label": "trees", "builtup_pct": 0.0, "road_surface": None},
        {"landcover_label": "built-up", "builtup_pct": 0.8, "road_surface": "unpaved"},
    ]
    out = summarize_cells(rows)
    assert out["n_cells"] == 4
    assert round(out["landcover"]["cropland"], 2) == 0.5
    assert round(out["builtup_pct"], 3) == 0.225                 # mean of builtup_pct
    assert round(out["road_unpaved_share"], 3) == round(2 / 3, 3)  # of cells with a known surface


def test_empty():
    assert summarize_cells([])["n_cells"] == 0
    assert summarize_cells([])["landcover"] == {}
