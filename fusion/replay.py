"""
REPLAY — rebuild the event read-model from the observation log and assert it is
bit-identical to a reference run. Because ADJUDICATE reads the version-pinned verdict
cache (frozen in CI), the rebuild reproduces the same events exactly.

The headline property: wipe the world model, rebuild it from evidence, get the same picture.
Honest caveat (eval/README): "bit-identical" holds against the FROZEN verdict + embedding
caches. A cold re-query of a live Ollama is not guaranteed identical, which is exactly why
CI always replays against verdicts_v1.json.
"""
from __future__ import annotations

from fusion.fuse import fuse
from fusion.types import FusionResult


def replay(observations: list, cache, backend, **kwargs) -> FusionResult:
    """Re-run fusion over the same observations. Same inputs + frozen cache → same digest."""
    return fuse(observations, cache, backend, **kwargs)


def assert_bit_identical(a: FusionResult, b: FusionResult) -> bool:
    """Return True iff two fusion results have identical event sets (by canonical digest)."""
    return a.digest() == b.digest()
