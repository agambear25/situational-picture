"""
Ties the synthesis pieces together: attention + grounded context + Read, with a stable input hash
for caching. Pure given (area_ctx, now, generate_fn) — the only impurity is the optional LLM call
inside generate_fn. Used by the runner (synth/run.py) and the read-only API fallback.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from assess.attention import classify_attention
from synth.context import build_context
from synth.read import generate_read


def synthesize_area(area_ctx: dict, now=None, generate_fn=None) -> dict:
    """`area_ctx` = api.queries.gather_area_context output (label, events, anomalies, families)."""
    now = now or datetime.now(timezone.utc)
    att = classify_attention(area_ctx["events"], area_ctx["anomalies"], now)
    context = build_context(area_ctx["label"], area_ctx["events"], area_ctx["anomalies"],
                            area_ctx["families"], now)
    read = generate_read(context, att, generate_fn)
    return {"attention": att, "context": context, "read": read, "input_hash": _input_hash(context, att)}


def _input_hash(context: dict, att: dict) -> str:
    """Stable hash of the facts that determine the Read — so it regenerates only when they change."""
    key = {"n": context["n_events"], "t": context["by_type"], "b": context["by_band"],
           "r": context["recent"], "p": context["prior"], "a": context["anomalies"], "s": att["status"]}
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]
