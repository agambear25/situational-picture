"""
The synthesis Read generator — turns the grounded context (synth/context.py) + the attention
classification into a short, honest area "intelligence read": {summary, indicators, provenance}.

Two paths, same grounded input:
  • generate_fn given (the local LLM) → a 2–3 sentence narrative summary.
  • no LLM / it errors → a deterministic TEMPLATE read formatted from the context.
So the Read NEVER blocks and is always grounded (mirrors the fusion adjudicator's keep-separate
fallback). `indicators` echoes the attention status — framed as INDICATORS from the data's trend +
anomalies, never a prediction.
"""
from __future__ import annotations

import json

_TREND = {"escalating": "rising", "quieting": "falling", "steady": "steady"}


def deterministic_read(context: dict, attention: dict) -> dict:
    """Format the context into a readable, grounded read — no LLM."""
    area, n = context["area"], context["n_events"]
    status = attention.get("status", "steady")
    top_type = next(iter(context["by_type"]), "activity").replace("_", " ")
    parts = [f"{area}: {n:,} recorded events, mostly {top_type}.",
             f"Activity is {_TREND.get(status, 'steady')} "
             f"({attention.get('recent', 0)} in the last {context['window_days']} days "
             f"vs {attention.get('prior', 0)} before)."]
    if "escalation" in context["anomalies"]:
        parts.append("A new, more-severe event type has appeared here.")
    elif "activity_spike" in context["anomalies"]:
        parts.append("A recent flare-up is flagged.")
    if context["by_band"].get("High"):
        parts.append(f"{context['by_band']['High']} event(s) are multi-source confirmed.")
    return {"summary": " ".join(parts), "indicators": status,
            "provenance": context["sensors"], "generated_by": "template"}


def _prompt(context: dict, attention: dict) -> str:
    return (
        "You are an OSINT analyst writing a SHORT, factual situation read for one area. Use ONLY "
        "the JSON facts below — do not invent specifics, names, or predictions. 2-3 sentences. "
        "State what's been happening and the trend. Frame any outlook as INDICATORS from the data "
        "(escalating/steady/quieting), never as a forecast or certainty.\n\n"
        f"FACTS:\n{json.dumps({'context': context, 'attention': attention}, default=str)}\n\n"
        "Write only the read text."
    )


def llm_read(context: dict, attention: dict, generate_fn) -> dict:
    """generate_fn(prompt:str)->str is the local LLM. Falls back to the template on any failure."""
    try:
        text = (generate_fn(_prompt(context, attention)) or "").strip()
        if not text:
            raise ValueError("empty LLM response")
        return {"summary": text, "indicators": attention.get("status", "steady"),
                "provenance": context["sensors"], "generated_by": "llm"}
    except Exception:  # noqa: BLE001 — never let synthesis block; degrade to the grounded template
        return deterministic_read(context, attention)


def generate_read(context: dict, attention: dict, generate_fn=None) -> dict:
    return llm_read(context, attention, generate_fn) if generate_fn else deterministic_read(context, attention)
