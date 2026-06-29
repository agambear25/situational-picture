"""
fuse() — the pure §17 orchestrator: BLOCK → SCORE → ADJUDICATE → PROPAGATE.

Deterministic by construction: observations are processed in a fixed sort order, no
now()/random is used, and the gray band is resolved through the version-pinned verdict
cache. Given the same log + same frozen caches, fuse() is bit-identical across runs
(that is what fusion/replay.py asserts).
"""
from __future__ import annotations

from itertools import combinations

from llm.config import LLMConfig, load_llm_config
from llm.runlog import RunCounters
from fusion.adjudicate import adjudicate
from fusion.block import block
from fusion.config import FusionConfig, load_fusion_config
from fusion.propagate import propagate
from fusion.score import score_pair
from fusion.types import FusionResult


def fuse(
    observations: list,
    cache,
    backend,
    cfg: FusionConfig | None = None,
    llm_cfg: LLMConfig | None = None,
    theater_id: str = "ua_donbas",
    landcover_by_obs: dict | None = None,
    ctx_lookup: dict | None = None,
) -> FusionResult:
    cfg = cfg or load_fusion_config()
    llm_cfg = llm_cfg or load_llm_config()
    landcover_by_obs = landcover_by_obs or {}
    counters = RunCounters()

    # fixed, deterministic processing order
    observations = sorted(observations, key=lambda o: (o.occurred_start.isoformat(), o.obs_id))
    obs_by_id = {o.obs_id: o for o in observations}
    input_ids = set(obs_by_id)

    # 1. BLOCK
    groups = block(observations, cfg)

    # 2. SCORE (within each candidate group only)
    scored = []
    for g in groups:
        for a_id, b_id in combinations(g.obs_ids, 2):
            a, b = obs_by_id[a_id], obs_by_id[b_id]
            scored.append(score_pair(
                a, b, cfg,
                landcover_a=landcover_by_obs.get(a_id),
                landcover_b=landcover_by_obs.get(b_id),
            ))
    counters.pairs_seen = len(scored)

    # Deterministic CO-LOCATED corroboration: two observations from DIFFERENT source families in
    # the SAME 1km cell with a compatible type (and within-window — guaranteed by blocking) are
    # independent sensors agreeing on the same place. That is corroboration by GEOMETRY, not a
    # "do the texts match?" question — so we merge them directly and keep them OUT of the gray
    # band. This both lifts cross-modal damage to multi-source AND stops a dense imagery field
    # from generating O(N^2) LLM adjudications (the gray band stays sparse: only ambiguous
    # cross-cell pairs reach the model).
    same_edges, gray_pairs = [], []
    for sp in scored:
        if sp.band == "same":
            same_edges.append((sp.obs_a, sp.obs_b))
            continue
        a, b = obs_by_id[sp.obs_a], obs_by_id[sp.obs_b]
        coloc = (a.cell_id == b.cell_id and a.source_family_id != b.source_family_id
                 and cfg.is_persistent_type(a.obs_type) and cfg.is_persistent_type(b.obs_type))
        if coloc:
            same_edges.append((sp.obs_a, sp.obs_b))      # co-located independent corroboration
        elif sp.band == "gray":
            gray_pairs.append((sp.obs_a, sp.obs_b))

    # 3. ADJUDICATE (gray band only)
    decisions = adjudicate(gray_pairs, obs_by_id, cache, backend, llm_cfg, counters, ctx_lookup)
    degraded_obs: set[str] = set()
    for d in decisions:
        if d.same:
            same_edges.append((d.obs_a, d.obs_b))
        if "verification-needed" in d.flags:
            degraded_obs.add(d.obs_a)
            degraded_obs.add(d.obs_b)

    # 4. PROPAGATE
    events, rejections = propagate(observations, same_edges, degraded_obs, cfg, theater_id)

    result = FusionResult(
        events=events, rejections=rejections,
        scored_pairs=scored, gray_pairs=gray_pairs,
        counters=counters.summary(),
    )

    # hard invariant: zero silent drops — every input obs is in exactly one event or rejected
    if not result.no_silent_drop(input_ids):
        cov = result.coverage(input_ids)
        raise AssertionError(f"SILENT DROP detected — coverage audit failed: {cov}")

    return result
