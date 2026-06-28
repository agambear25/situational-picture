"""
The Phase-1 gate as a pytest, plus the chaos test: an Ollama outage must cause only
visible fragmentation (more events, flagged verification-needed), NEVER a silent drop.
"""
from __future__ import annotations

from llm.circuit_breaker import LLMUnavailable
from fusion.fuse import fuse
from eval.harness import run_gate


def test_gate_passes():
    ok, report = run_gate(verbose=False)
    assert ok, f"eval gate failed: {report['checks']}"


def test_no_silent_drop_hard(observations):
    from eval.harness import load_ground_truth
    from llm.cache import FrozenVerdictCache
    from llm.backend import FrozenBackend
    from pathlib import Path

    gt = load_ground_truth()
    cache = FrozenVerdictCache(Path(__file__).parent.parent / "fixtures" / "verdicts_v1.json")
    result = fuse(observations, cache, FrozenBackend(), theater_id=gt["theater_id"])
    assert result.no_silent_drop({o.obs_id for o in observations})


# ---- chaos: Ollama down for the whole gray band ----

class _EmptyCache:
    def get(self, key):
        return None

    def put(self, key, verdict, **kw):
        pass


class _AlwaysDownBackend:
    def adjudicate(self, ctx):
        raise LLMUnavailable("ollama down (chaos test)")


def test_chaos_ollama_down_still_no_drop(observations, ground_truth, frozen_cache, frozen_backend):
    input_ids = {o.obs_id for o in observations}

    healthy = fuse(observations, frozen_cache, frozen_backend, theater_id=ground_truth["theater_id"])
    chaos = fuse(observations, _EmptyCache(), _AlwaysDownBackend(), theater_id=ground_truth["theater_id"])

    # 1. Outage NEVER drops an observation.
    assert chaos.no_silent_drop(input_ids), "OUTAGE CAUSED A SILENT DROP — unacceptable"

    # 2. Outage only fragments (≥ as many events), never merges away a distinct one.
    assert len(chaos.events) >= len(healthy.events)

    # 3. Every gray pair that couldn't be adjudicated was KEPT SEPARATE and flagged, not merged.
    assert chaos.counters["degraded_keep_separate"] > 0
    flagged = [e for e in chaos.events if "verification-needed" in e.flags]
    assert flagged, "degraded separations must surface as verification-needed flags"

    # 4. No must-not-merge pair was ever fused under outage (no silent over-merge).
    from eval.metrics import must_not_merge_violations
    assert must_not_merge_violations(chaos, ground_truth) == []
