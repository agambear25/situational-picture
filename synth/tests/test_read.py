"""Offline gate for the synthesis Read (pure: context-builder + Read generator, no LLM/DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from synth.context import build_context
from synth.read import deterministic_read, generate_read

NOW = datetime(2024, 3, 10, tzinfo=timezone.utc)


def _ev(t, band, days, place, fams=1):
    return {"event_type": t, "confidence_band": band, "occurred_start": NOW - timedelta(days=days),
            "place_label": place, "n_independent_families": fams}


EVENTS = [_ev("strike", "High", 5, "Bakhmut", 2), _ev("strike", "Rumored", 10, "Bakhmut"),
          _ev("fire", "Rumored", 60, "near Soledar")]
FAMS = ["ucdp", "copernicus_sar", "nasa_firms"]


def test_context_rejects_precise_coords():
    with pytest.raises(AssertionError):
        build_context("X", [{"event_type": "strike", "lon": 37.0, "occurred_start": NOW}], [], [], NOW)


def test_context_shape():
    c = build_context("Bakhmut sector", EVENTS, [{"subkind": "escalation"}], FAMS, NOW, window_days=45)
    assert c["n_events"] == 3 and c["by_type"]["strike"] == 2 and c["by_band"]["High"] == 1
    assert c["recent"] == 2 and c["prior"] == 1
    assert "escalation" in c["anomalies"]
    assert {"satellite radar", "news/reports", "thermal"} <= set(c["sensors"])
    assert c["top_events"][0]["band"] == "High"


def test_deterministic_read_is_grounded():
    c = build_context("Bakhmut sector", EVENTS, [{"subkind": "escalation"}], FAMS, NOW)
    r = deterministic_read(c, {"status": "escalating", "recent": 2, "prior": 1})
    assert r["generated_by"] == "template" and r["indicators"] == "escalating"
    assert "Bakhmut sector" in r["summary"] and "rising" in r["summary"]
    assert "satellite radar" in r["provenance"]


def test_llm_read_uses_the_model():
    c = build_context("Bakhmut sector", EVENTS, [], FAMS, NOW)
    r = generate_read(c, {"status": "steady"}, generate_fn=lambda p: "Bakhmut is active.")
    assert r["generated_by"] == "llm" and r["summary"] == "Bakhmut is active." and r["indicators"] == "steady"


def test_llm_read_falls_back_and_never_blocks():
    c = build_context("Bakhmut sector", EVENTS, [], FAMS, NOW)
    def boom(_p):
        raise RuntimeError("ollama down")
    r = generate_read(c, {"status": "steady", "recent": 2, "prior": 1}, generate_fn=boom)
    assert r["generated_by"] == "template"
