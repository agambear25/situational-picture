"""
Verdict schema + JSON-Schema export for constrained decoding.
parse_verdict retries-once semantics live in the backend; this raises on malformed input
so a bad parse is never silently treated as a merge/no-merge.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

# Bumping this invalidates affected cache entries (it is part of the cache key).
SCHEMA_VERSION = "v1"


class EvidenceSpan(BaseModel):
    obs_ref: str = Field(description="'a' or 'b' — which observation the span is from")
    span: str = Field(description="verbatim text fragment that drove the decision")


class Verdict(BaseModel):
    same: bool = Field(description="True if the two observations describe the same real-world incident")
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 confidence in the same/different call")
    rationale: str = Field(default="", description="one short sentence")
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)

    # provenance, filled by the backend (not asked of the model)
    tier: str = Field(default="", description="gate_3b | adjudicator_7b | escalation_14b | frozen")


class MalformedVerdict(ValueError):
    pass


def verdict_json_schema() -> dict:
    """JSON Schema passed to Ollama's `format` for constrained decoding.

    Only the model-supplied fields are constrained (tier is added by the backend).
    """
    return {
        "type": "object",
        "properties": {
            "same": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
            "evidence_spans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "obs_ref": {"type": "string", "enum": ["a", "b"]},
                        "span": {"type": "string"},
                    },
                    "required": ["obs_ref", "span"],
                },
            },
        },
        "required": ["same", "confidence"],
    }


def parse_verdict(raw: str, tier: str = "") -> Verdict:
    """Parse a model's JSON output into a Verdict. Raises MalformedVerdict on failure."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MalformedVerdict(f"not JSON: {e}: {raw[:200]!r}") from e
    try:
        v = Verdict(**data)
    except ValidationError as e:
        raise MalformedVerdict(f"schema violation: {e}") from e
    if tier:
        v = v.model_copy(update={"tier": tier})
    return v
