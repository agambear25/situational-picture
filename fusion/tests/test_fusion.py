"""Unit tests for the fusion stages — pure, offline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grid.mgrs_1km import to_cell_id
from ingest.contract import Observation
from fusion.block import block
from fusion.config import load_fusion_config
from fusion.score import score_pair
from fusion.propagate import propagate
from fusion.fuse import fuse
from fusion.replay import assert_bit_identical

CFG = load_fusion_config()
T0 = datetime(2024, 3, 10, 8, 0, tzinfo=timezone.utc)


def mk(obs_id, lon, lat, source_id, family, otype="strike", text="strike on town",
       minutes=0, dur=60, precision=300, place_id=None) -> Observation:
    start = T0 + timedelta(minutes=minutes)
    return Observation(
        obs_id=obs_id, theater_id="ua_donbas", source_id=source_id, source_family_id=family,
        modality="text", obs_type=otype, occurred_start=start, occurred_end=start + timedelta(minutes=dur),
        cell_id=to_cell_id(lon, lat), geom_precision_m=precision, content_hash=f"h_{obs_id}",
        place_id=place_id, text=text,
    )


class _Empty:
    def get(self, k): return None
    def put(self, k, v, **kw): pass


class _Down:
    def adjudicate(self, ctx):
        from llm.circuit_breaker import LLMUnavailable
        raise LLMUnavailable("down")


# ---- BLOCK ----

def test_block_keeps_singletons():
    a = mk("a", 37.749, 48.139, "ucdp_ged_bulk", "ucdp")
    far = mk("z", 39.0, 47.0, "gdelt_v2", "gdelt", minutes=10000)  # far in space and time
    groups = block([a, far], CFG)
    all_ids = sorted(i for g in groups for i in g.obs_ids)
    assert all_ids == ["a", "z"]  # nothing dropped
    # far obs is its own singleton group
    assert any(g.obs_ids == ("z",) for g in groups)


def test_block_overgroups_nearby():
    a = mk("a", 37.7490, 48.1390, "ucdp_ged_bulk", "ucdp")
    b = mk("b", 37.7495, 48.1396, "gdelt_v2", "gdelt", minutes=15)
    groups = block([a, b], CFG)
    assert len(groups) == 1 and set(groups[0].obs_ids) == {"a", "b"}


# ---- SCORE ----

def test_score_same_place_time_high():
    a = mk("a", 37.749, 48.139, "ucdp_ged_bulk", "ucdp", text="strike on the coke plant Avdiivka")
    b = mk("b", 37.749, 48.139, "gdelt_v2", "gdelt", minutes=5, text="strike on the coke plant Avdiivka")
    sp = score_pair(a, b, CFG)
    assert sp.band in ("same", "gray")
    assert sp.p > CFG.tau_low


def test_score_distinct_far_low():
    a = mk("a", 37.749, 48.139, "ucdp_ged_bulk", "ucdp")
    b = mk("b", 39.0, 47.0, "gdelt_v2", "gdelt", text="totally different event", minutes=600)
    sp = score_pair(a, b, CFG)
    assert sp.band == "different"


# ---- PROPAGATE: noisy-OR + bands ----

def test_two_independent_families_confirmed_high():
    a = mk("a", 37.749, 48.139, "ucdp_ged_bulk", "ucdp")
    b = mk("b", 37.749, 48.139, "gdelt_v2", "gdelt", minutes=5)
    events, rej = propagate([a, b], [("a", "b")], set(), CFG, "ua_donbas")
    assert len(events) == 1
    e = events[0]
    assert e.n_independent_families == 2
    assert e.status == "confirmed"
    assert e.confidence_band == "High"
    assert not rej


def test_single_family_echo_is_rumored_and_not_inflated():
    a = mk("a", 38.0, 48.595, "gdelt_v2", "gdelt", otype="artillery_fire")
    b = mk("b", 38.0, 48.595, "gdelt_v2", "gdelt", otype="artillery_fire", minutes=10)
    c = mk("c", 38.0, 48.595, "gdelt_v2", "gdelt", otype="artillery_fire", minutes=20)
    events, _ = propagate([a, b, c], [("a", "b"), ("b", "c")], set(), CFG, "ua_donbas")
    assert len(events) == 1
    e = events[0]
    assert e.n_independent_families == 1
    assert e.confidence_band == "Rumored"      # single family never "confirmed"
    assert "echo-only" in e.flags


def test_stranded_singleton_is_event_not_drop():
    a = mk("a", 36.25, 47.66, "gdelt_v2", "gdelt", otype="troop_move")
    events, rej = propagate([a], [], set(), CFG, "ua_donbas")
    assert len(events) == 1 and not rej
    assert events[0].confidence_band == "Rumored"


# ---- FUSE: invariants + replay ----

def test_fuse_no_silent_drop_and_replay_identical():
    obs = [
        mk("a", 37.749, 48.139, "ucdp_ged_bulk", "ucdp"),
        mk("b", 37.749, 48.139, "gdelt_v2", "gdelt", minutes=5),
        mk("c", 36.25, 47.66, "gdelt_v2", "gdelt", otype="troop_move", minutes=300),
    ]
    r1 = fuse(obs, _Empty(), _Down(), theater_id="ua_donbas")
    r2 = fuse(obs, _Empty(), _Down(), theater_id="ua_donbas")
    assert r1.no_silent_drop({o.obs_id for o in obs})
    assert assert_bit_identical(r1, r2)
