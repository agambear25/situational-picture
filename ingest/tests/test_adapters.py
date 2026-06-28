"""Pure, offline tests for the Phase-2 adapter parsing (no network, no DB)."""
from __future__ import annotations

from ingest.text.ucdp_ged import parse_ucdp_row, iter_observations
from ingest.thermal.firms import parse_firms_row, FIRMS_SOURCES

UCDP_MAP = {
    "Armed Conflict (Government)": "strike",
    "Armed Conflict (Non-State)": "strike",
    "One-sided violence": "other",
    "_default": "other",
}
DONBAS_BBOX = [36.0, 46.8, 39.5, 49.5]


# ---- UCDP GED ----

def test_ucdp_state_based_is_strike_text_obs():
    raw = parse_ucdp_row({
        "id": 1, "type_of_violence": 1, "latitude": "48.13", "longitude": "37.74",
        "date_start": "2024-03-10", "date_end": "2024-03-10",
        "conflict_name": "Government of Ukraine - Russia", "best": "3", "adm_1": "Donetsk",
    }, UCDP_MAP)
    assert raw.obs_type == "strike"
    assert raw.modality == "text"
    assert raw.source_id == "ucdp_ged_bulk" and raw.source_family_id == "ucdp"
    assert raw.geo.lat == 48.13 and raw.geo.lon == 37.74
    assert raw.occurred_start.tzinfo is not None


def test_ucdp_one_sided_maps_to_other():
    raw = parse_ucdp_row({"id": 2, "type_of_violence": 3, "latitude": "48", "longitude": "37",
                          "date_start": "2024-01-01"}, UCDP_MAP)
    assert raw.obs_type == "other"


def test_ucdp_place_name_only_is_not_dropped():
    raw = parse_ucdp_row({"id": 3, "type_of_violence": 1, "date_start": "2024-01-01",
                          "adm_2": "Bakhmut", "where_coordinates": "Bakhmut"}, UCDP_MAP)
    assert raw.geo.place_name and raw.geo.lon is None  # resolver will place it via gazetteer


def test_ucdp_no_geo_and_no_date_return_none():
    assert parse_ucdp_row({"id": 4, "type_of_violence": 1, "date_start": "2024-01-01"}, UCDP_MAP) is None
    assert parse_ucdp_row({"id": 5, "type_of_violence": 1, "latitude": "48", "longitude": "37"}, UCDP_MAP) is None


def test_ucdp_bbox_filter_keeps_only_in_aoi():
    rows = [
        {"id": 1, "type_of_violence": 1, "latitude": "48.1", "longitude": "37.7", "date_start": "2024-03-10"},
        {"id": 2, "type_of_violence": 1, "latitude": "10.0", "longitude": "100.0", "date_start": "2024-03-10"},
    ]
    out = list(iter_observations(rows, UCDP_MAP, "ua_donbas", bbox=DONBAS_BBOX))
    assert len(out) == 1 and out[0].meta["ucdp_id"] == 1


# ---- FIRMS ----

def test_firms_detection_is_thermal_fire():
    raw = parse_firms_row({
        "latitude": "48.5", "longitude": "38.0", "acq_date": "2024-03-10", "acq_time": "1230",
        "satellite": "N", "instrument": "VIIRS", "confidence": "nominal", "frp": "12.3",
    }, "firms_viirs_snpp", "nasa_firms")
    assert raw.obs_type == "fire" and raw.modality == "thermal"
    assert raw.source_family_id == "nasa_firms"
    assert raw.occurred_start.hour == 12 and raw.occurred_start.minute == 30
    assert raw.occurred_start.tzinfo is not None
    assert raw.self_conf == 0.6


def test_firms_frp_folded_into_text_for_distinct_hash():
    base = {"latitude": "48.5", "longitude": "38.0", "acq_date": "2024-03-10",
            "acq_time": "1230", "satellite": "N", "confidence": "n"}
    a = parse_firms_row({**base, "frp": "12.3"}, "firms_viirs_snpp", "nasa_firms")
    b = parse_firms_row({**base, "frp": "99.9"}, "firms_viirs_snpp", "nasa_firms")
    assert a.text != b.text  # distinct FRP → distinct content_hash within a cell-hour


def test_firms_modis_numeric_confidence_and_midnight_time():
    raw = parse_firms_row({"latitude": "48.5", "longitude": "38.0", "acq_date": "2024-03-10",
                           "acq_time": "0005", "confidence": "80", "frp": "5"},
                          "firms_modis", "nasa_modis")
    assert abs(raw.self_conf - 0.8) < 1e-9
    assert raw.occurred_start.hour == 0 and raw.occurred_start.minute == 5


def test_firms_missing_coords_returns_none():
    assert parse_firms_row({"acq_date": "2024-03-10", "acq_time": "1230"},
                           "firms_viirs_snpp", "nasa_firms") is None


def test_firms_source_families_viirs_shared_modis_independent():
    fams = {sid: fam for sid, fam in FIRMS_SOURCES.values()}
    assert fams["firms_viirs_snpp"] == "nasa_firms" == fams["firms_viirs_noaa20"]
    assert fams["firms_modis"] == "nasa_modis"  # MODIS corroborates VIIRS independently
