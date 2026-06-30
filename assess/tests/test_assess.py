"""Offline gate for the Phase-4a assessment scorers (pure: no DB, no clock)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assess.config import load_assessment_config
from assess.significance import significance, recency, novelty
from assess.anomaly import cell_anomalies
from assess.exposure import exposure, nearest_settlement
from assess.gaps import collection_gap

NOW = datetime(2024, 3, 10, 12, 0, tzinfo=timezone.utc)
CFG = load_assessment_config()
SETTLEMENTS = [{"name": "Mariupol", "lon": 37.60, "lat": 47.10},
               {"name": "Bakhmut", "lon": 38.00, "lat": 48.60}]


# --------------------------------------------------------------------------- significance

def test_recency_decays():
    assert recency(NOW, NOW, 14.0) == 1.0
    one_tau = recency(NOW - timedelta(days=14), NOW, 14.0)
    assert 0.34 < one_tau < 0.38            # exp(-1) ≈ 0.368


def test_novelty_first_is_one_repeats_decay():
    assert novelty("strike", {"strike": 1}) == 1.0
    assert novelty("fire", {"fire": 5}) == 0.2


def test_significance_is_the_product_with_rationale():
    ev = {"event_type": "airstrike", "confidence": 0.9, "occurred_start": NOW,
          "n_independent_families": 2}
    s = significance(ev, NOW, CFG, {"airstrike": 1})
    assert abs(s["score"] - 0.90 * 0.9 * 1.0 * 1.0) < 0.01   # sev .90 × conf .9 × rec 1 × nov 1
    assert "airstrike" in s["rationale"] and "confirmed by several sources" in s["rationale"]


def test_confirmed_recent_strike_outranks_old_repeated_fire():
    strike = significance({"event_type": "strike", "confidence": 0.9, "occurred_start": NOW,
                           "n_independent_families": 2}, NOW, CFG, {"strike": 1})
    fire = significance({"event_type": "fire", "confidence": 0.6,
                         "occurred_start": NOW - timedelta(days=20), "n_independent_families": 1},
                        NOW, CFG, {"fire": 4})
    assert strike["score"] > fire["score"]


# --------------------------------------------------------------------------- anomaly

def _ev(cell, etype, dt):
    return {"cell_id": cell, "event_type": etype, "occurred_start": dt}


def test_activity_spike_flagged():
    evs = [_ev("C1", "fire", NOW - timedelta(days=i)) for i in range(4)]   # 4 recent (≥3)
    spikes = [a for a in cell_anomalies(evs, NOW, CFG) if a["subkind"] == "activity_spike"]
    assert spikes and spikes[0]["cell_id"] == "C1" and spikes[0]["n_recent"] == 4


def test_quiet_cell_is_not_a_spike():
    evs = [_ev("C3", "fire", NOW - timedelta(days=1))]                     # 1 recent (< 3)
    assert not [a for a in cell_anomalies(evs, NOW, CFG) if a["subkind"] == "activity_spike"]


def test_escalation_flagged_when_new_severe_type_appears():
    evs = [_ev("C2", "fire", NOW - timedelta(days=60)),                    # prior: just fire
           _ev("C2", "strike", NOW - timedelta(days=1))]                   # recent: new + severe
    esc = [a for a in cell_anomalies(evs, NOW, CFG) if a["subkind"] == "escalation"]
    assert esc and esc[0]["new_type"] == "strike"


def test_no_escalation_for_milder_new_type():
    evs = [_ev("C4", "strike", NOW - timedelta(days=60)),
           _ev("C4", "fire", NOW - timedelta(days=1))]                     # new but milder
    assert not [a for a in cell_anomalies(evs, NOW, CFG) if a["subkind"] == "escalation"]


# --------------------------------------------------------------------------- exposure (4c)

def test_exposure_high_near_city():
    x = exposure({"event_type": "strike", "lon": 37.61, "lat": 47.11}, SETTLEMENTS, CFG)
    assert x and x["settlement"] == "Mariupol" and x["distance_km"] < 2 and x["score"] > 0.7
    assert "Mariupol" in x["rationale"]


def test_exposure_none_when_far():
    assert exposure({"event_type": "strike", "lon": 40.0, "lat": 50.0}, SETTLEMENTS, CFG) is None


def test_exposure_none_without_coords():
    assert exposure({"event_type": "strike", "lon": None, "lat": None}, SETTLEMENTS, CFG) is None


# --------------------------------------------------------------------------- collection-gap (4c)

def test_gap_flags_recent_highsev_singlesource():
    g = collection_gap({"event_type": "strike", "n_independent_families": 1,
                        "occurred_start": NOW - timedelta(days=10)}, NOW, CFG)
    assert g and "single source" in g["rationale"] and g["score"] > 0


def test_gap_skips_corroborated():
    assert collection_gap({"event_type": "strike", "n_independent_families": 2,
                           "occurred_start": NOW}, NOW, CFG) is None


def test_gap_skips_low_severity():
    assert collection_gap({"event_type": "fire", "n_independent_families": 1,
                           "occurred_start": NOW}, NOW, CFG) is None


def test_gap_skips_old():
    assert collection_gap({"event_type": "strike", "n_independent_families": 1,
                           "occurred_start": NOW - timedelta(days=400)}, NOW, CFG) is None
