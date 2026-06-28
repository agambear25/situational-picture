"""
Verify queue — the significance-ranked human review list.

Surfaces the events fusion flagged for a human look (verification-needed first, then weaker
bands) and lets the analyst record an append-only verdict. Every action routes through
client.post_review → POST /review → the cop_api role's INSERT-only grant on the append-only
review_annotation table. The event itself is never mutated here: the read model is rebuilt from
the log, so a verdict is just one more immutable, replayable annotation.

Geometry stays cell-only — the API already coarsened every event before it reached this screen.
"""
from __future__ import annotations

import streamlit as st

# Confidence-band → short rationale, so the queue explains WHY each event wants a human.
_BAND_HINT = {
    "Rumored": "single-source / low confidence",
    "Low": "weak corroboration",
    "Medium": "partial corroboration",
    "High": "strong corroboration",
}


def render(client, analyst: str = "analyst") -> None:
    st.subheader("Verify queue")
    st.caption(
        "Events fusion flagged for review — verification-needed first, then weaker bands. "
        "Confirm / Split / Reject writes an append-only annotation; it never edits the event."
    )

    # The API may be down — surface it rather than crash the screen (Streamlit convention).
    try:
        events = client.get_verify_queue(limit=50).get("events", [])
    except Exception as exc:  # noqa: BLE001 — UI must stay resilient to any client error
        st.error(f"Could not load the verify queue: {exc}")
        return

    if not events:
        st.success("Verify queue is empty — nothing awaiting human verification.")
        return

    st.caption(f"{len(events)} event(s) awaiting review.")

    for ev in events:
        event_id = ev.get("event_id")
        band = ev.get("confidence_band", "?")
        etype = ev.get("event_type", "event")
        cell_id = ev.get("cell_id", "?")
        flags = ev.get("flags") or []
        flag_str = ", ".join(flags) if flags else "—"

        # verification-needed events lead the title with a marker so they stand out at a glance.
        marker = "⚠ " if "verification-needed" in flags else ""
        title = f"{marker}{etype} · {band} · {cell_id} · flags: {flag_str}"

        with st.expander(title, expanded=bool(marker)):
            c1, c2, c3 = st.columns(3)
            c1.metric("sources", ev.get("n_sources", 0))
            c2.metric("independent families", ev.get("n_independent_families", 0))
            conf = ev.get("confidence")
            c3.metric("confidence", f"{conf:.2f}" if isinstance(conf, (int, float)) else "—")
            st.caption(
                f"{_BAND_HINT.get(band, '')} · occurred "
                f"{ev.get('occurred_start')} → {ev.get('occurred_end')}"
            )

            # One reason box shared by all three actions for this event; unique key per event so
            # state stays independent across reruns.
            reason = st.text_input(
                "Reason (recorded with the verdict)",
                key=f"reason_{event_id}",
                placeholder="Why confirm / split / reject — cite the deciding evidence.",
            )

            b1, b2, b3 = st.columns(3)
            if b1.button("Confirm", key=f"confirm_{event_id}"):
                _submit(client, event_id, "confirm", reason, analyst)
            if b2.button("Split", key=f"split_{event_id}"):
                _submit(client, event_id, "split", reason, analyst)
            if b3.button("Reject", key=f"reject_{event_id}"):
                _submit(client, event_id, "reject", reason, analyst)


def _submit(client, event_id: str, action: str, reason: str, analyst: str) -> None:
    """POST one append-only review verdict, reporting success/failure inline."""
    try:
        result = client.post_review(event_id, action, reason, analyst)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not record {action}: {exc}")
        return
    st.success(f"Recorded {action} (review_id={result.get('review_id')}).")
