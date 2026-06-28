"""Versioned prompt loader. The VERSION file feeds the cache key."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def prompt_version() -> str:
    return (_DIR / "VERSION").read_text().strip()


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    return (_DIR / f"{name}.md").read_text()


def render(name: str, **fields) -> str:
    """Render a prompt template by replacing only the known {field} placeholders.

    Explicit replacement (not str.format) so literal braces in the prompt prose — e.g.
    the JSON shape {obs_ref:"a"|"b", span} — pass through untouched.
    """
    out = _template(name)
    for key, val in fields.items():
        out = out.replace("{" + key + "}", str(val))
    return out
