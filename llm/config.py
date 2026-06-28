"""
Typed, version-pinned LLM configuration loaded from config/llm.yaml.
Every version string here is a component of the adjudication cache key.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_LLM_CFG = Path(__file__).parent.parent / "config" / "llm.yaml"
_THRESH_CFG = Path(__file__).parent.parent / "config" / "thresholds.yaml"
_EMB_CFG = Path(__file__).parent.parent / "config" / "embedding.yaml"


@dataclass(frozen=True)
class LLMConfig:
    gate_model: str
    gate_model_version: str
    adjudicator_model: str
    adjudicator_model_version: str
    escalation_model: str
    escalation_model_version: str
    claude_enabled: bool
    claude_model: str
    prompt_version: str
    schema_version: str
    embedding_version: str
    temperature: float
    seed: int
    max_tokens: int
    high_salience_types: frozenset[str]
    gate_gamma: float
    escalation_gamma: float
    gate_timeout_s: float
    adjudicator_timeout_s: float
    escalation_timeout_s: float
    ollama_base_url: str
    cb_failure_threshold: int
    cb_recovery_timeout_s: float

    @property
    def composite_model_version(self) -> str:
        """All three tiers pinned together — bumping any one invalidates affected verdicts."""
        return f"{self.gate_model_version}+{self.adjudicator_model_version}+{self.escalation_model_version}"


def load_llm_config() -> LLMConfig:
    with open(_LLM_CFG) as f:
        llm = yaml.safe_load(f)["llm"]
    with open(_THRESH_CFG) as f:
        thr = yaml.safe_load(f)["llm"]
    with open(_EMB_CFG) as f:
        emb = yaml.safe_load(f)["embedding"]

    cb = llm.get("circuit_breaker", {})
    cfg = LLMConfig(
        gate_model=llm["gate_model"],
        gate_model_version=llm["gate_model_version"],
        adjudicator_model=llm["adjudicator_model"],
        adjudicator_model_version=llm["adjudicator_model_version"],
        escalation_model=llm["escalation_model"],
        escalation_model_version=llm["escalation_model_version"],
        claude_enabled=bool(llm.get("claude_enabled", False)),
        claude_model=llm.get("claude_model", ""),
        prompt_version=str(llm["prompt_version"]),
        schema_version=str(llm["schema_version"]),
        embedding_version=str(emb["embedding_version"]),
        temperature=float(llm.get("temperature", 0)),
        seed=int(llm.get("seed", 0)),
        max_tokens=int(llm.get("max_tokens", 256)),
        high_salience_types=frozenset(llm.get("high_salience_types", [])),
        gate_gamma=float(thr["gate_gamma"]),
        escalation_gamma=float(thr["escalation_gamma"]),
        gate_timeout_s=float(llm.get("gate_timeout_s", 8)),
        adjudicator_timeout_s=float(llm.get("adjudicator_timeout_s", 20)),
        escalation_timeout_s=float(llm.get("escalation_timeout_s", 60)),
        ollama_base_url=llm.get("ollama_base_url", "http://localhost:11434"),
        cb_failure_threshold=int(cb.get("failure_threshold", 5)),
        cb_recovery_timeout_s=float(cb.get("recovery_timeout_s", 60)),
    )
    assert_claude_off_by_default(cfg)
    return cfg


def assert_claude_off_by_default(cfg: LLMConfig) -> None:
    """Guard: Claude is PAID and must be OFF unless explicitly enabled.

    Claude Code halts and flags before any paid/non-local call. This assertion is the
    code-level expression of that rule: the default config must keep Claude inert.
    """
    if cfg.claude_enabled:
        raise RuntimeError(
            "claude_enabled=true in config/llm.yaml. Claude is a PAID, non-local dependency "
            "and is OFF by default for the MVP. Enable it only with explicit intent; the "
            "terminal tier is the local 14B model."
        )
