"""Build the local-LLM generate_fn for the synthesis Read (Ollama), or None if unavailable."""
from __future__ import annotations


def ollama_generate_fn(model: str | None = None):
    """Return generate_fn(prompt:str)->str backed by the configured local Ollama model, or None
    (→ the Read falls back to its deterministic template). Lazy + defensive: any setup error → None."""
    try:
        from llm.backend import OllamaClient
        from llm.config import load_llm_config
        cfg = load_llm_config()
        client = OllamaClient(cfg)
        m, timeout = (model or cfg.adjudicator_model), cfg.adjudicator_timeout_s

        def gen(prompt: str) -> str:
            return client.generate(m, prompt, timeout)

        return gen
    except Exception:  # noqa: BLE001 — no local LLM configured → deterministic Read
        return None
