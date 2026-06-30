"""Offline gate for the AOR attention classifier (pure: no DB, no LLM)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assess.attention import attention_sort_key, classify_attention

NOW = datetime(2024, 3, 10, tzinfo=timezone.utc)


def _ev(days_ago, fams=1):
    return {"occurred_start": NOW - timedelta(days=days_ago), "n_independent_families": fams}


def test_escalating_from_trend():
    evs = [_ev(10) for _ in range(8)] + [_ev(60) for _ in range(2)]
    a = classify_attention(evs, [], NOW, window_days=45)
    assert a["status"] == "escalating" and a["recent"] == 8 and a["prior"] == 2


def test_escalating_from_anomaly_even_if_quiet():
    a = classify_attention([_ev(10), _ev(10)], [{"subkind": "escalation"}], NOW)
    assert a["status"] == "escalating" and a["has_escalation"]


def test_quieting():
    evs = [_ev(10) for _ in range(2)] + [_ev(60) for _ in range(8)]
    assert classify_attention(evs, [], NOW, window_days=45)["status"] == "quieting"


def test_steady():
    evs = [_ev(10) for _ in range(5)] + [_ev(60) for _ in range(5)]
    assert classify_attention(evs, [], NOW, window_days=45)["status"] == "steady"


def test_confirmed_counts_multi_family():
    a = classify_attention([_ev(10, 2), _ev(10, 1), _ev(10, 3)], [], NOW)
    assert a["confirmed"] == 2


def test_sort_puts_escalating_first():
    esc = classify_attention([_ev(10) for _ in range(5)], [{"subkind": "escalation"}], NOW)
    steady = classify_attention([_ev(10) for _ in range(5)] + [_ev(60) for _ in range(5)], [], NOW)
    assert attention_sort_key(esc, 5) < attention_sort_key(steady, 5)
