"""
Persistent honesty header for every Streamlit screen. Two jobs, both about managing the
operator's trust calibration rather than showing data:

  1. A standing reminder that this is analytical OSINT — incomplete, lagged, and
     deception-prone — and is decision-support, NOT a targeting product. This is the human
     half of the analytical-not-targeting invariant: the API already strips coordinates to
     1km cells, and this banner makes that limitation legible so nobody over-reads the map.
  2. A multi-tempo "as-of / next update" strip so the operator knows each feed answers a
     *different* clock. A FIRMS hotspot is ~3h fresh; UCDP is a week lagged. Mixing them
     without showing tempo invites false "nothing is happening" / "it just happened" reads.

Feed cadences live in config/feeds.yaml (single source of truth shared with the ingest side),
so we never hardcode lags here. Phase-3 feeds (phase == 3) are shown greyed and tagged "(P3)"
because they are gated off until the live-feed gate is green — the operator should see they
exist but know they are not yet contributing.

Dependency-light: only streamlit + pyyaml (already in the UI stack). The `client` arg is
accepted for signature parity with sibling components but is unused — this banner is static
config, not a live API read, so it renders even when the API is down.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

# config/feeds.yaml relative to this file: ui/components/ -> ../../config/feeds.yaml.
# Resolved once at import-style call time; kept as a constant so the path math is not a
# magic string buried in the loader.
_FEEDS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "feeds.yaml"

# Marker the config uses to flag a feed as Phase 3 (gated-off). Reading it from one named
# constant keeps the "what does phase==3 mean" decision in exactly one place.
_PHASE_GATED = 3

_BANNER_TEXT = (
    "**Analytical OSINT — incomplete, lagged, and deception-prone.** "
    "Decision-support, **NOT** targeting. All geometry is 1km-cell only."
)


@lru_cache(maxsize=1)
def _load_feeds() -> list[dict[str, Any]]:
    """Load and normalise the feed cadence list from config/feeds.yaml.

    Cached because the banner renders on every page/rerun and the config is static for the
    process lifetime. Returns a flat list of dicts (insertion order preserved) so the caller
    does not need to know the YAML shape. Resilient: any read/parse failure yields an empty
    list rather than crashing the header that frames the whole app.
    """
    try:
        import yaml  # lazy: keeps importing this component cheap for unit tests

        raw = yaml.safe_load(_FEEDS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        # Missing/unreadable/malformed config: degrade to "no tempo strip" — the standing
        # warning banner above is the load-bearing part and must still show.
        return []

    feeds: list[dict[str, Any]] = []
    for feed_id, spec in (raw.get("feeds") or {}).items():
        spec = spec or {}
        feeds.append(
            {
                "id": feed_id,
                "label": spec.get("label", feed_id),
                # Fall back to a neutral marker rather than inventing a cadence we don't know.
                "next_update_display": spec.get("next_update_display", "—"),
                "phase": spec.get("phase"),
            }
        )
    return feeds


def render(client: Any = None) -> None:  # noqa: ARG001 — `client` is for signature parity
    """Render the persistent honesty banner and the multi-tempo as-of strip.

    `client` is accepted but unused: this header is static config and must render even when
    the API is unreachable, so it never makes a network call.
    """
    # Standing limitation banner — st.warning so it reads as a caution, not decoration, and
    # stays visually distinct from the data below it.
    st.warning(_BANNER_TEXT)

    feeds = _load_feeds()
    if not feeds:
        # Config absent/unreadable: the warning above already did the safety-critical work.
        return

    st.caption("Feed tempo — each source answers a different clock:")

    # One column per feed so the tempos sit side by side; the operator can scan "which clock"
    # at a glance instead of reading a paragraph. st.columns tolerates many narrow columns.
    columns = st.columns(len(feeds))
    for column, feed in zip(columns, feeds):
        gated = feed.get("phase") == _PHASE_GATED
        with column:
            if gated:
                # Phase-3: greyed + "(P3)" tag so it is visibly present-but-not-contributing.
                # Markdown grey keeps it readable without implying it is a live tempo.
                st.markdown(
                    f"<span style='color:#94a3b8'>{feed['label']} (P3)<br>"
                    f"{feed['next_update_display']}</span>",
                    unsafe_allow_html=True,
                )
            else:
                # Live feeds: label + "next update" cadence as a quiet caption pair.
                st.caption(f"**{feed['label']}**")
                st.caption(f"next: {feed['next_update_display']}")
