"""Offline LLM tests — no Ollama, no DB. Circuit breaker, schema, cache key, cascade tiering."""
from __future__ import annotations

import pytest

from llm.circuit_breaker import CircuitBreaker, State, LLMUnavailable
from llm.schema import parse_verdict, MalformedVerdict, Verdict, verdict_json_schema
from llm.cache import PairKey
from llm.backend import PairContext, OllamaBackend
from llm.config import LLMConfig


# ---- circuit breaker (clock injected, deterministic) ----

def test_breaker_opens_after_threshold():
    t = {"now": 0.0}
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10, clock=lambda: t["now"])
    assert cb.allow()
    for _ in range(3):
        cb.record_failure()
    assert cb.state == State.OPEN
    assert not cb.allow()


def test_breaker_half_opens_after_recovery():
    t = {"now": 0.0}
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10, clock=lambda: t["now"])
    cb.record_failure()
    assert cb.state == State.OPEN
    t["now"] = 11.0
    assert cb.allow()  # transitions to HALF_OPEN
    assert cb.state == State.HALF_OPEN
    cb.record_success()
    assert cb.state == State.CLOSED


# ---- schema ----

def test_parse_good_verdict():
    v = parse_verdict('{"same": true, "confidence": 0.9, "rationale": "same town, same hour"}', "gate_3b")
    assert v.same and v.confidence == 0.9 and v.tier == "gate_3b"


def test_parse_rejects_garbage():
    with pytest.raises(MalformedVerdict):
        parse_verdict("not json at all", "gate_3b")


def test_parse_rejects_out_of_range_confidence():
    with pytest.raises(MalformedVerdict):
        parse_verdict('{"same": true, "confidence": 1.5}', "gate_3b")


def test_schema_export_shape():
    s = verdict_json_schema()
    assert s["required"] == ["same", "confidence"]


# ---- cache key order-independence ----

def _cfg():
    return LLMConfig(
        gate_model="g", gate_model_version="gv", adjudicator_model="a", adjudicator_model_version="av",
        escalation_model="e", escalation_model_version="ev", claude_enabled=False, claude_model="",
        prompt_version="v1", schema_version="v1", embedding_version="emb1",
        temperature=0, seed=0, max_tokens=256, high_salience_types=frozenset({"bridge_destroyed"}),
        gate_gamma=0.72, escalation_gamma=0.68, gate_timeout_s=8, adjudicator_timeout_s=20,
        escalation_timeout_s=60, ollama_base_url="http://x", cb_failure_threshold=5, cb_recovery_timeout_s=60,
    )


def test_pair_key_order_independent():
    cfg = _cfg()
    k1 = PairKey.build("hashA", "hashB", "strike", "fire", cfg)
    k2 = PairKey.build("hashB", "hashA", "fire", "strike", cfg)
    assert k1.digest() == k2.digest()


# ---- cascade tiering (mock client) ----

class _MockClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, model, prompt, timeout):
        self.calls.append(model)
        return self.responses.pop(0)


def _ctx(type_a="strike", type_b="strike"):
    return PairContext(
        hash_a="a", hash_b="b", type_a=type_a, type_b=type_b,
        time_a="t", time_b="t", cell_a="c", cell_b="c", label_a="L", label_b="L",
        text_a="x", text_b="y",
    )


def test_gate_short_circuits_on_high_confidence():
    cfg = _cfg()
    client = _MockClient(['{"same": true, "confidence": 0.95}'])
    be = OllamaBackend(cfg, client=client)
    v = be.adjudicate(_ctx())
    assert v.tier == "gate_3b"
    assert client.calls == [cfg.gate_model]   # only the gate was called


def test_low_confidence_escalates_through_tiers():
    cfg = _cfg()
    client = _MockClient([
        '{"same": false, "confidence": 0.3}',   # gate, low
        '{"same": false, "confidence": 0.5}',   # adjudicator, still below escalation_gamma
        '{"same": true, "confidence": 0.8}',    # escalation terminal
    ])
    be = OllamaBackend(cfg, client=client)
    v = be.adjudicate(_ctx())
    assert v.tier == "escalation_14b"
    assert client.calls == [cfg.gate_model, cfg.adjudicator_model, cfg.escalation_model]


def test_high_salience_always_escalates():
    cfg = _cfg()
    client = _MockClient([
        '{"same": true, "confidence": 0.99}',   # gate very confident...
        '{"same": true, "confidence": 0.99}',   # ...but salience forces adjudicator...
        '{"same": true, "confidence": 0.99}',   # ...and escalation (terminal).
    ])
    be = OllamaBackend(cfg, client=client)
    v = be.adjudicate(_ctx(type_a="bridge_destroyed"))
    assert v.tier == "escalation_14b"
    assert client.calls == [cfg.gate_model, cfg.adjudicator_model, cfg.escalation_model]


def test_malformed_then_retry_succeeds():
    cfg = _cfg()
    client = _MockClient([
        "garbage",                              # gate malformed
        '{"same": true, "confidence": 0.95}',   # retry good
    ])
    be = OllamaBackend(cfg, client=client)
    v = be.adjudicate(_ctx())
    assert v.same and v.tier == "gate_3b"
