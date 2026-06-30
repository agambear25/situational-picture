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

Scaling: rather than enumerate all O(N²) pairs, observations are bucketed into a uniform
grid whose cell size is the largest blocking radius across the types present. Any pair that
could link by distance (centroid distance ≤ max per-type radius) then lands in the same or
an adjacent bucket, so comparing each bucket to its 3×3 neighbourhood examines a *superset*
of all spatially-linkable pairs. `_links()` still makes the final decision, so the grouping
is bit-identical to brute force while the work drops to ~linear on spread-out boards.
"""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations

from fusion.config import FusionConfig
from fusion.geo import cell_centroid, cell_distance_m, temporal_gap_s
from fusion.graph import UnionFind
from fusion.types import CandidateGroup

# Half of the 8-neighbourhood. Pairing each bucket with these four offsets (plus its own
# within-bucket pairs) visits every adjacent bucket-pair exactly once; the reverse four
# offsets are covered when the neighbour bucket is itself the current one.
_FORWARD_NEIGHBORS = ((1, -1), (1, 0), (1, 1), (0, 1))

# Conservative metres-per-degree of latitude (true value 110_574–111_694). Under-estimating
# makes each degree-sized bucket span *more* ground than nominal, so the coverage guarantee
# below never under-reaches. _SAFETY further enlarges buckets to absorb haversine-vs-planar
# error and cos(lat) variation across a board — correctness only needs a superset, and larger
# buckets only cost a few extra (correctly rejected) comparisons.
_M_PER_DEG = 110_000.0
_SAFETY = 1.5


def block(observations: list, cfg: FusionConfig) -> list[CandidateGroup]:
    obs_ids = [o.obs_id for o in observations]
    uf = UnionFind(obs_ids)

    if len(observations) >= 2:
        _grid_union(observations, cfg, uf)

    comps = uf.components()
    return [CandidateGroup(group_id=i, obs_ids=tuple(members))
            for i, members in enumerate(comps)]


def _grid_union(observations: list, cfg: FusionConfig, uf: UnionFind) -> None:
    # Grid cell = largest blocking radius among the types actually present (handles the
    # _default fallback via block_radius_m). Only present types can appear in a pair, and
    # _links uses max(radius_a, radius_b) ≤ this, so this bounds every spatial link.
    radius = max(cfg.block_radius_m(o.obs_type) for o in observations)

    centroids = {o.obs_id: cell_centroid(o.cell_id) for o in observations}
    lat_ref = max(abs(lat) for _, lat in centroids.values())
    cos_ref = max(math.cos(math.radians(lat_ref)), 1e-6)
    cell_deg_lat = radius * _SAFETY / _M_PER_DEG
    cell_deg_lon = radius * _SAFETY / (_M_PER_DEG * cos_ref)

    grid: dict[tuple[int, int], list] = defaultdict(list)
    for o in observations:
        lon, lat = centroids[o.obs_id]
        key = (int(math.floor(lat / cell_deg_lat)), int(math.floor(lon / cell_deg_lon)))
        grid[key].append(o)

    for (row, col), members in grid.items():
        for a, b in combinations(members, 2):
            if _links(a, b, cfg):
                uf.union(a.obs_id, b.obs_id)
        for drow, dcol in _FORWARD_NEIGHBORS:
            other = grid.get((row + drow, col + dcol))
            if not other:
                continue
            for a in members:
                for b in other:
                    if _links(a, b, cfg):
                        uf.union(a.obs_id, b.obs_id)

    # place_id links are not distance-bounded — two obs can share a place_id while being
    # kilometres apart, so the spatial grid alone would miss them. Index by place_id and
    # compare all pairs within each shared place (groups are small in practice); _links
    # still applies the type/temporal checks, so the same-cell/distance paths are unaffected.
    by_place: dict = defaultdict(list)
    for o in observations:
        if o.place_id is not None:
            by_place[o.place_id].append(o)
    for members in by_place.values():
        if len(members) < 2:
            continue
        for a, b in combinations(members, 2):
            if _links(a, b, cfg):
                uf.union(a.obs_id, b.obs_id)


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
