"""Differential tests for BLOCK's spatial pre-filter.

The grid-bucketed `block()` must be BIT-IDENTICAL to the O(N²) brute-force
reference (same UnionFind partition → same CandidateGroup list, since
`UnionFind.components()` is order-independent). These tests pin that equivalence
on adversarial layouts and on randomized boards, so any pair the pre-filter
fails to examine shows up immediately as a partition difference.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from itertools import combinations

from grid.mgrs_1km import to_cell_id
from ingest.contract import Observation
from fusion.block import block, _links
from fusion.config import load_fusion_config
from fusion.graph import UnionFind
from fusion.types import CandidateGroup

CFG = load_fusion_config()
T0 = datetime(2024, 3, 10, 8, 0, tzinfo=timezone.utc)


def _bruteforce_block(observations, cfg) -> list[CandidateGroup]:
    """The original O(N²) reference — the golden master the pre-filter must match."""
    obs_ids = [o.obs_id for o in observations]
    uf = UnionFind(obs_ids)
    for a, b in combinations(observations, 2):
        if _links(a, b, cfg):
            uf.union(a.obs_id, b.obs_id)
    comps = uf.components()
    return [CandidateGroup(group_id=i, obs_ids=tuple(members))
            for i, members in enumerate(comps)]


def mk(obs_id, lon, lat, otype="strike", minutes=0, dur=60, place_id=None) -> Observation:
    start = T0 + timedelta(minutes=minutes)
    return Observation(
        obs_id=obs_id, theater_id="ua_donbas", source_id="src", source_family_id="fam",
        modality="text", obs_type=otype, occurred_start=start,
        occurred_end=start + timedelta(minutes=dur),
        cell_id=to_cell_id(lon, lat), geom_precision_m=300, content_hash=f"h_{obs_id}",
        place_id=place_id, text="t",
    )


# ---- adversarial layouts ----

def test_empty_and_singleton_match_bruteforce():
    assert block([], CFG) == _bruteforce_block([], CFG)
    one = [mk("a", 37.75, 48.14)]
    assert block(one, CFG) == _bruteforce_block(one, CFG)


def test_clustered_within_radius_match_bruteforce():
    obs = [
        mk("a", 37.7490, 48.1390),
        mk("b", 37.7495, 48.1396, minutes=15),
        mk("c", 37.7501, 48.1402, minutes=30),
    ]
    assert block(obs, CFG) == _bruteforce_block(obs, CFG)


def test_far_apart_distinct_match_bruteforce():
    obs = [mk("a", 37.75, 48.14), mk("z", 39.0, 47.0, minutes=10000)]
    assert block(obs, CFG) == _bruteforce_block(obs, CFG)


def test_same_place_far_apart_links_like_bruteforce():
    """place_id links are NOT distance-bounded: two obs sharing a place_id but
    kilometres apart still block. A spatial-only grid would miss this pair —
    this is the case that forces a separate place index."""
    obs = [
        mk("a", 37.75, 48.14, otype="building_damaged", place_id=42),
        mk("b", 38.40, 48.60, otype="building_damaged", place_id=42, minutes=120),
    ]
    bf = _bruteforce_block(obs, CFG)
    # sanity: the reference really does merge them via place_id
    assert len(bf) == 1 and set(bf[0].obs_ids) == {"a", "b"}
    assert block(obs, CFG) == bf


def test_boundary_pairs_straddling_buckets_match_bruteforce():
    """Pairs near the radius boundary, nudged across likely bucket edges."""
    base_lon, base_lat = 37.75, 48.14
    obs = [mk("a", base_lon, base_lat, otype="explosion")]  # radius 1500m
    for i, dlon in enumerate([0.005, 0.010, 0.015, 0.020, 0.025, 0.030]):
        obs.append(mk(f"x{i}", base_lon + dlon, base_lat, otype="explosion", minutes=i))
        obs.append(mk(f"y{i}", base_lon, base_lat + dlon, otype="explosion", minutes=i))
    assert block(obs, CFG) == _bruteforce_block(obs, CFG)


# ---- randomized property test ----

def test_randomized_boards_match_bruteforce():
    types = ["explosion", "strike", "fire", "building_damaged", "troop_move"]
    for seed in range(40):
        rng = random.Random(seed)
        n = rng.randint(0, 60)
        obs = []
        for i in range(n):
            # tight cluster so links actually form across grid buckets
            lon = 37.6 + rng.uniform(0.0, 0.4)
            lat = 48.0 + rng.uniform(0.0, 0.4)
            otype = rng.choice(types)
            minutes = rng.randint(0, 6 * 60)
            # ~25% share one of a few place_ids (some will be far apart)
            place_id = rng.choice([None, None, None, 1, 2, 3]) if rng.random() < 0.5 else None
            obs.append(mk(f"o{i}", lon, lat, otype=otype, minutes=minutes, place_id=place_id))
        assert block(obs, CFG) == _bruteforce_block(obs, CFG), f"seed={seed} n={n}"
