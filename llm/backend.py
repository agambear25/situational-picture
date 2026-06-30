"""
The tiered adjudication backend: 3B gate → 7B adjudicator → 14B escalation (terminal).
Claude is present but inert under the default config (claude_enabled=false).

Design properties:
  - one HTTP call per tier; escalation only fires on low local confidence OR high salience.
  - retry-once on a malformed verdict, then surface LLMUnavailable so the caller keeps the
    pair SEPARATE and flags it (never a silent merge).
  - circuit breaker means a flapping/absent Ollama degrades fusion to keep-separate, never blocks.
  - httpx is imported lazily so the eval harness (frozen verdicts, no model) never needs it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from llm.circuit_breaker import CircuitBreaker, LLMUnavailable
from llm.config import LLMConfig
from llm.prompts import render
from llm.runlog import RunCounters
from llm.schema import MalformedVerdict, Verdict, parse_verdict, verdict_json_schema


@dataclass(frozen=True)
class PairContext:
    """Everything a tier prompt and the cache key need for one observation pair."""
    hash_a: str
    hash_b: str
    type_a: str
    type_b: str
    time_a: str
    time_b: str
    cell_a: str
    cell_b: str
    label_a: str
    label_b: str
    text_a: str
    text_b: str
    context_a: str = ""
    context_b: str = ""

    def is_high_salience(self, salience: frozenset[str]) -> bool:
        return self.type_a in salience or self.type_b in salience

    def render_fields(self) -> dict:
        return {
            "type_a": self.type_a, "type_b": self.type_b,
            "time_a": self.time_a, "time_b": self.time_b,
            "cell_a": self.cell_a, "cell_b": self.cell_b,
            "label_a": self.label_a, "label_b": self.label_b,
            "context_a": self.context_a, "context_b": self.context_b,
            "text_a": self.text_a, "text_b": self.text_b,
        }


class AdjudicatorBackend(Protocol):
    def adjudicate(self, ctx: PairContext) -> Verdict: ...


class FrozenBackend:
    """Eval backend: it never calls a model. Every gray-band pair must already be in the
    frozen verdict cache (checked upstream); if adjudicate() is reached, the fixtures and
    verdicts are out of sync — fail loudly so verdicts_v1.json gets regenerated."""

    def adjudicate(self, ctx: PairContext) -> Verdict:
        raise LLMUnavailable(
            "FrozenBackend reached for an un-frozen gray-band pair "
            f"({ctx.hash_a[:8]}~{ctx.hash_b[:8]}). Regenerate eval/fixtures/verdicts_v1.json."
        )


class OllamaClient:
    """Low-level Ollama HTTP caller with circuit breaker + retry. Lazy httpx import."""

    def __init__(self, cfg: LLMConfig, breaker: Optional[CircuitBreaker] = None):
        self._cfg = cfg
        self._cb = breaker or CircuitBreaker(cfg.cb_failure_threshold, cfg.cb_recovery_timeout_s)

    def generate(self, model: str, prompt: str, timeout: float) -> str:
        """Adjudicator path: constrained decoding to the Verdict JSON schema."""
        return self._post(model, prompt, timeout, fmt=verdict_json_schema())

    def generate_text(self, model: str, prompt: str, timeout: float) -> str:
        """Free-form path (e.g. the synthesis Read prose): no schema constraint."""
        return self._post(model, prompt, timeout, fmt=None)

    def _post(self, model: str, prompt: str, timeout: float, *, fmt) -> str:
        if not self._cb.allow():
            raise LLMUnavailable("circuit open")
        import httpx  # lazy/heavy
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self._cfg.temperature,
                "seed": self._cfg.seed,
                "num_predict": self._cfg.max_tokens,
            },
        }
        if fmt is not None:                       # constrained decoding only when a schema is given
            body["format"] = fmt
        try:
            resp = httpx.post(f"{self._cfg.ollama_base_url}/api/generate", json=body, timeout=timeout)
            resp.raise_for_status()
            text = resp.json()["response"]
            self._cb.record_success()
            return text
        except LLMUnavailable:
            raise
        except Exception as e:
            self._cb.record_failure()
            raise LLMUnavailable(f"ollama call failed: {e}") from e


class OllamaBackend:
    """The 3-tier cascade."""

    def __init__(self, cfg: LLMConfig, client: Optional[OllamaClient] = None,
                 counters: Optional[RunCounters] = None):
        self._cfg = cfg
        self._client = client or OllamaClient(cfg)
        self._counters = counters or RunCounters()

    def adjudicate(self, ctx: PairContext) -> Verdict:
        cfg = self._cfg
        high = ctx.is_high_salience(cfg.high_salience_types)

        # Tier 1 — gate (3B)
        v = self._call_tier(cfg.gate_model, "gate", ctx, "gate_3b", cfg.gate_timeout_s)
        if v.confidence >= cfg.gate_gamma and not high:
            return v

        # Tier 2 — adjudicator (7B)
        v = self._call_tier(cfg.adjudicator_model, "adjudicator", ctx, "adjudicator_7b",
                            cfg.adjudicator_timeout_s)
        if v.confidence >= cfg.escalation_gamma and not high:
            return v

        # Tier 3 — escalation (14B), terminal
        return self._call_tier(cfg.escalation_model, "escalation", ctx, "escalation_14b",
                              cfg.escalation_timeout_s)

    def _call_tier(self, model: str, prompt_name: str, ctx: PairContext,
                   tier: str, timeout: float) -> Verdict:
        self._counters.record_call(tier)
        prompt = render(prompt_name, **ctx.render_fields())
        raw = self._client.generate(model, prompt, timeout)
        try:
            return parse_verdict(raw, tier)
        except MalformedVerdict:
            self._counters.malformed_retries += 1
            raw = self._client.generate(model, prompt, timeout)  # retry once
            return parse_verdict(raw, tier)  # second failure → MalformedVerdict → caller keeps separate
