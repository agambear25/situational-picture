"""
ADJUDICATE — gray band ONLY. For each gray-band pair:
  cache lookup (order-independent key, all versions pinned)  →  hit: use it
                                                              →  miss: LLM cascade, then cache it
On the LLM being unavailable/malformed (circuit open, timeout, bad parse) the pair is
KEPT SEPARATE and flagged 'verification-needed' — never silently merged (PRD §ADJUDICATE,
Cross-check #4). Fusion never blocks on the model.
"""
from __future__ import annotations

from llm.backend import PairContext
from llm.cache import PairKey
from llm.circuit_breaker import LLMUnavailable
from llm.config import LLMConfig
from llm.schema import MalformedVerdict
from fusion.types import EdgeDecision


def adjudicate(
    gray_pairs: list[tuple[str, str]],
    obs_by_id: dict,
    cache,
    backend,
    llm_cfg: LLMConfig,
    counters,
    ctx_lookup: dict | None = None,
) -> list[EdgeDecision]:
    ctx_lookup = ctx_lookup or {}
    decisions: list[EdgeDecision] = []

    for a_id, b_id in gray_pairs:
        a, b = obs_by_id[a_id], obs_by_id[b_id]
        key = PairKey.build(a.content_hash, b.content_hash, a.obs_type, b.obs_type, llm_cfg)

        verdict = cache.get(key)
        if verdict is not None:
            counters.cache_hits += 1
            decisions.append(_decision(a_id, b_id, verdict.same, "cache", verdict.confidence))
            continue

        # cache miss → LLM cascade (may be the frozen-backend raise in CI)
        try:
            pc = _pair_context(a, b, ctx_lookup)
            verdict = backend.adjudicate(pc)
        except (LLMUnavailable, MalformedVerdict):
            counters.degraded_keep_separate += 1
            decisions.append(EdgeDecision(
                obs_a=a_id, obs_b=b_id, same=False,
                source="degraded_keep_separate", confidence=0.0,
                flags=("verification-needed",),
            ))
            continue

        # persist verdict for replay determinism (FrozenVerdictCache.put raises by design;
        # in eval all gray pairs are cache hits so this line is never reached there)
        try:
            cache.put(key, verdict)
        except RuntimeError:
            pass
        decisions.append(_decision(a_id, b_id, verdict.same, "verdict", verdict.confidence))

    return decisions


def _decision(a_id, b_id, same, source, confidence) -> EdgeDecision:
    # A low-confidence "different" verdict is honored (kept separate) but flagged so a human
    # sees it; a low-confidence "same" is also flagged so over-merges surface in the queue.
    flags = () if confidence >= 0.5 else ("verification-needed",)
    return EdgeDecision(obs_a=a_id, obs_b=b_id, same=same, source=source,
                        confidence=confidence, flags=flags)


def _pair_context(a, b, ctx_lookup: dict) -> PairContext:
    ca = ctx_lookup.get(a.obs_id, {})
    cb = ctx_lookup.get(b.obs_id, {})
    return PairContext(
        hash_a=a.content_hash, hash_b=b.content_hash,
        type_a=a.obs_type, type_b=b.obs_type,
        time_a=_fmt_range(a), time_b=_fmt_range(b),
        cell_a=a.cell_id, cell_b=b.cell_id,
        label_a=ca.get("label", a.cell_id), label_b=cb.get("label", b.cell_id),
        context_a=ca.get("context", ""), context_b=cb.get("context", ""),
        text_a=a.text, text_b=b.text,
    )


def _fmt_range(o) -> str:
    return f"{o.occurred_start.isoformat()}..{o.occurred_end.isoformat()}"
