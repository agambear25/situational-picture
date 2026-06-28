"""
SCORE — pairwise similarity, p = s_geo · s_time · s_text · s_type · s_indep,
with a land-cover plausibility gate. Two thresholds split each pair into
same / gray / different. Per-factor breakdown is retained for audit + the
threshold tuner.

Text scoring blends embedding cosine and character-trigram similarity. When either
observation lacks an embedding (the eval corpus runs model-free), s_text degrades
gracefully to trigram-only — the design's stated fallback.
"""
from __future__ import annotations

import math
import re

from fusion.config import FusionConfig
from fusion.geo import cell_distance_m, temporal_gap_s, temporal_overlap_ratio
from fusion.types import ScoredPair

_TOKEN = re.compile(r"[A-Za-zА-Яа-яЇїІіЄєҐґ]{3,}")
_NUM = re.compile(r"\d+")


def score_pair(a, b, cfg: FusionConfig, landcover_a=None, landcover_b=None) -> ScoredPair:
    s_geo = _s_geo(a, b, cfg)
    s_time = _s_time(a, b, cfg)
    s_text = _s_text(a, b, cfg)
    s_type = cfg.type_compat(a.obs_type, b.obs_type)

    # weighted geometric mean — see config/weights.yaml for why (not a raw product).
    # s_indep is deliberately excluded; independence affects confidence, not the merge.
    p = _wgeomean(
        [(s_geo, cfg.w_geo), (s_time, cfg.w_time), (s_text, cfg.w_text), (s_type, cfg.w_type)]
    )

    # land-cover plausibility gate (penalize implausible event/landcover combos)
    lc_mult = min(
        cfg.landcover_penalty(a.obs_type, landcover_a),
        cfg.landcover_penalty(b.obs_type, landcover_b),
    )
    p *= lc_mult

    band = "same" if p >= cfg.tau_high else "different" if p <= cfg.tau_low else "gray"

    factors = (
        ("s_geo", round(s_geo, 4)), ("s_time", round(s_time, 4)),
        ("s_text", round(s_text, 4)), ("s_type", round(s_type, 4)),
        ("landcover_mult", round(lc_mult, 4)),
    )
    return ScoredPair(obs_a=a.obs_id, obs_b=b.obs_id, p=round(p, 6), band=band, factors=factors)


def _wgeomean(pairs: list[tuple[float, float]]) -> float:
    """Weighted geometric mean: prod(s_i ** w_i) ** (1/sum_w). A zero factor → 0."""
    total_w = sum(w for _, w in pairs) or 1.0
    log_sum = 0.0
    for s, w in pairs:
        if s <= 0:
            return 0.0
        log_sum += w * math.log(s)
    return math.exp(log_sum / total_w)


def _s_geo(a, b, cfg: FusionConfig) -> float:
    d = cell_distance_m(a.cell_id, b.cell_id)
    pa = max(a.geom_precision_m, 1.0)
    pb = max(b.geom_precision_m, 1.0)
    sigma = cfg.sigma_k * math.sqrt(pa * pa + pb * pb)
    return math.exp(-d / sigma) if sigma > 0 else (1.0 if d == 0 else 0.0)


def _s_time(a, b, cfg: FusionConfig) -> float:
    overlap = temporal_overlap_ratio(a.occurred_start, a.occurred_end, b.occurred_start, b.occurred_end)
    if overlap > 0:
        return overlap
    gap = temporal_gap_s(a.occurred_start, a.occurred_end, b.occurred_start, b.occurred_end)
    return math.exp(-gap / cfg.tau_time_s) if cfg.tau_time_s > 0 else 0.0


def _s_text(a, b, cfg: FusionConfig) -> float:
    lexical = _lexical_sim(a.text, b.text)
    if a.embedding is not None and b.embedding is not None:
        cos = _cosine(a.embedding, b.embedding)
        base = cfg.alpha_text * cos + (1 - cfg.alpha_text) * lexical
    else:
        base = lexical  # model-free fallback (eval corpus)
    # shared-toponym (via resolved place_id) and shared-number bonuses
    bonus = 0.0
    if a.place_id is not None and a.place_id == b.place_id:
        bonus += cfg.toponym_bonus
    if _shared_numbers(a.text, b.text):
        bonus += cfg.number_bonus
    return min(1.0, base + bonus)


# minimal English/Ukrainian stopword set so token overlap reflects content, not glue words
_STOP = frozenset({
    "the", "a", "an", "and", "of", "in", "on", "at", "to", "by", "near", "this", "that",
    "with", "for", "from", "into", "over", "was", "were", "is", "are", "be", "been",
    "reported", "seen", "area", "several", "among", "later", "toward", "past", "through",
})


def _lexical_sim(x: str, y: str) -> float:
    """Blend char-trigram Jaccard with stopword-filtered token Jaccard."""
    return 0.5 * _trigram_sim(x, y) + 0.5 * _token_jaccard(x, y)


def _token_jaccard(x: str, y: str) -> float:
    tx = {t for t in _TOKEN.findall((x or "").lower()) if t not in _STOP}
    ty = {t for t in _TOKEN.findall((y or "").lower()) if t not in _STOP}
    if not tx and not ty:
        return 0.0
    inter = len(tx & ty)
    union = len(tx | ty)
    return inter / union if union else 0.0


def _trigram_sim(x: str, y: str) -> float:
    gx, gy = _trigrams(x), _trigrams(y)
    if not gx and not gy:
        return 0.0
    inter = len(gx & gy)
    union = len(gx | gy)
    return inter / union if union else 0.0


def _trigrams(s: str) -> set[str]:
    s = " ".join(_TOKEN.findall((s or "").lower()))
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()


def _cosine(u, v) -> float:
    dot = sum(ui * vi for ui, vi in zip(u, v))
    nu = math.sqrt(sum(ui * ui for ui in u))
    nv = math.sqrt(sum(vi * vi for vi in v))
    if nu == 0 or nv == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (nu * nv)))


def _shared_numbers(x: str, y: str) -> bool:
    return bool(set(_NUM.findall(x or "")) & set(_NUM.findall(y or "")))
