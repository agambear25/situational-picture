"""
BLOCK — candidate generation. Deliberately over-groups (high recall) so SCORE/ADJUDICATE
never face O(N²) across the whole log and never miss a true co-membership.

Pure-Python reference implementation over coarsened observations: an obs pair is linked
when its types are compatible AND their time windows are within the per-type window AND
they share a cell, a place_id, or fall within the per-type spatial radius (cell-centroid
distance). Connected components of that link graph are the candidate groups. Singletons
(DBSCAN "noise") are kept as their own one-member group — NEVER discarded.

Under-grouping here is unrecoverable, so the predicate errs toward linking. The production
path can push this into PostGIS (ST_DWithin + ST_ClusterDBSCAN); this is the semantics it
must reproduce, and what the eval gate validates.
"""
from __future__ import annotations

from itertools import combinations

from fusion.config import FusionConfig
from fusion.geo import cell_distance_m, temporal_gap_s
from fusion.graph import UnionFind
from fusion.types import CandidateGroup


def block(observations: list, cfg: FusionConfig) -> list[CandidateGroup]:
    obs_ids = [o.obs_id for o in observations]
    uf = UnionFind(obs_ids)

    for a, b in combinations(observations, 2):
        if _links(a, b, cfg):
            uf.union(a.obs_id, b.obs_id)

    comps = uf.components()
    return [CandidateGroup(group_id=i, obs_ids=tuple(members))
            for i, members in enumerate(comps)]


def _links(a, b, cfg: FusionConfig) -> bool:
    # 1. type compatibility — incompatible pairs never block
    if cfg.type_compat(a.obs_type, b.obs_type) <= 0:
        return False

    # 2. temporal — within the larger of the two per-type windows
    window = max(cfg.block_window_s(a.obs_type), cfg.block_window_s(b.obs_type))
    if temporal_gap_s(a.occurred_start, a.occurred_end, b.occurred_start, b.occurred_end) > window:
        return False

    # 3. spatial OR place OR same-cell
    if a.cell_id == b.cell_id:
        return True
    if a.place_id is not None and a.place_id == b.place_id:
        return True
    radius = max(cfg.block_radius_m(a.obs_type), cfg.block_radius_m(b.obs_type))
    return cell_distance_m(a.cell_id, b.cell_id) <= radius
