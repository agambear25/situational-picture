"""
PROPAGATE — accepted 'same' edges form connected components; each component is one Event.

  - Confidence = noisy-OR over INDEPENDENT source families (echoes within a family do not
    inflate it): C = 1 − Π_f (1 − c_f), c_f = best (capped) reliability in family f.
  - 4-tier band: High (≥2 families & C≥hi) / Medium / Low / Rumored; single family ⇒ Rumored
    always (recall-first: one source is never "confirmed").
  - Status: confirmed (≥2 independent families) else candidate.
  - EVERY input observation lands in exactly one event (singletons included) or, defensively,
    in the rejection ledger if it has no cell — zero silent drops.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from fusion.config import FusionConfig
from fusion.graph import UnionFind
from fusion.types import Event, ObsRejection


def propagate(
    observations: list,
    same_edges: list[tuple[str, str]],
    degraded_obs: set[str],
    cfg: FusionConfig,
    theater_id: str,
) -> tuple[list[Event], list[ObsRejection]]:
    obs_by_id = {o.obs_id: o for o in observations}

    rejections = [ObsRejection(o.obs_id, "no_cell") for o in observations if not o.cell_id]
    rejected_ids = {r.obs_id for r in rejections}

    placed = [o for o in observations if o.obs_id not in rejected_ids]
    uf = UnionFind([o.obs_id for o in placed])
    for a, b in same_edges:
        if a in obs_by_id and b in obs_by_id and a not in rejected_ids and b not in rejected_ids:
            uf.union(a, b)

    events: list[Event] = []
    for comp in uf.components():
        members = [obs_by_id[i] for i in comp]
        events.append(_build_event(members, degraded_obs, cfg, theater_id))

    # deterministic event ordering
    events.sort(key=lambda e: (e.cell_id, e.occurred_start.isoformat(), e.created_from_obs))
    return events, rejections


def _build_event(members: list, degraded_obs: set[str], cfg: FusionConfig, theater_id: str) -> Event:
    # families → best capped reliability per family
    fam_best: dict[str, float] = {}
    for m in members:
        fam = cfg.family(m.source_id)
        c = cfg.reliability(m.source_id)
        fam_best[fam] = max(fam_best.get(fam, 0.0), c)

    n_families = len(fam_best)
    # noisy-OR over independent families
    prod = 1.0
    for c in fam_best.values():
        prod *= (1.0 - c)
    confidence = 1.0 - prod

    band = _assign_band(confidence, n_families, cfg)
    status = "confirmed" if n_families >= 2 else "candidate"

    # event type = most common obs_type (deterministic tie-break by name)
    type_counts = Counter(m.obs_type for m in members)
    event_type = sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    # representative cell = most precise member (smallest precision), tie-break by cell_id
    rep = min(members, key=lambda m: (m.geom_precision_m, m.cell_id))
    cell_id = rep.cell_id
    resolved_precision_m = min(m.geom_precision_m for m in members)

    occurred_start = min(m.occurred_start for m in members)
    occurred_end = max(m.occurred_end for m in members)

    flags = []
    if n_families == 1 and len(members) > 1:
        flags.append("echo-only")          # multiple reports, one family — no independent corroboration
    if any(m.obs_id in degraded_obs for m in members):
        flags.append("verification-needed")  # an outage forced a separation touching this event
    if band == "Rumored":
        flags.append("single-source" if n_families == 1 else "low-confidence")

    created_from = tuple(sorted(m.obs_id for m in members))
    event_id = _event_id(event_type, cell_id, created_from)

    return Event(
        event_id=event_id, theater_id=theater_id, event_type=event_type, cell_id=cell_id,
        occurred_start=occurred_start, occurred_end=occurred_end, status=status,
        confidence=round(confidence, 6), confidence_band=band,
        n_sources=len(members), n_independent_families=n_families,
        resolved_precision_m=resolved_precision_m,
        flags=tuple(sorted(set(flags))), created_from_obs=created_from,
    )


def _assign_band(confidence: float, n_families: int, cfg: FusionConfig) -> str:
    if n_families < 2 and cfg.single_source_always_rumored:
        return "Rumored"
    if confidence >= cfg.band_high_min and n_families >= cfg.band_high_min_families:
        return "High"
    if confidence >= cfg.band_medium_min:
        return "Medium"
    if confidence >= cfg.band_low_min:
        return "Low"
    return "Rumored"


def _event_id(event_type: str, cell_id: str, created_from: tuple[str, ...]) -> str:
    raw = f"{event_type}|{cell_id}|{'|'.join(created_from)}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
