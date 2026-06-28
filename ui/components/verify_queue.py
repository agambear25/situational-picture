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

# Confidence-band → short plain-English reason this event wants a human look.
_BAND_HINT = {
    "Rumored": "only one source — not confirmed",
    "Low": "weak support",
    "Medium": "partly confirmed",
    "High": "strongly confirmed",
}


def render(client, analyst: str = "analyst") -> None:
    st.subheader("Needs review")
    st.caption(
        "Events the system wants a person to look at — least-certain ones first. "
        "Your decision (keep / split / remove) is saved as a note; the event itself is never overwritten."
    )

    # The API may be down — surface it rather than crash the screen (Streamlit convention).
    try:
        events = client.get_verify_queue(limit=50).get("events", [])
    except Exception as exc:  # noqa: BLE001 — UI must stay resilient to any client error
        st.error(f"Couldn't load the review list: {exc}")
        return

    if not events:
        st.success("All clear — nothing waiting for review.")
        return

    st.caption(f"{len(events)} event(s) waiting for review.")

    for ev in events:
        event_id = ev.get("event_id")
        band = ev.get("confidence_band", "?")
        etype = ev.get("event_type", "event")
        cell_id = ev.get("cell_id", "?")
        flags = ev.get("flags") or []
        flag_str = ", ".join(flags) if flags else "—"

        # verification-needed events lead the title with a marker so they stand out at a glance.
        marker = "⚠ " if "verification-needed" in flags else ""
        title = f"{marker}{etype} · {band} · area {cell_id}"

        with st.expander(title, expanded=bool(marker)):
            c1, c2, c3 = st.columns(3)
            c1.metric("reports", ev.get("n_sources", 0))
            c2.metric("independent sources", ev.get("n_independent_families", 0))
            conf = ev.get("confidence")
            c3.metric("confidence", f"{conf:.0%}" if isinstance(conf, (int, float)) else "—")
            st.caption(
                f"{_BAND_HINT.get(band, '')} · happened "
                f"{ev.get('occurred_start')} → {ev.get('occurred_end')}"
            )

            # One reason box shared by all three actions for this event; unique key per event so
            # state stays independent across reruns.
            reason = st.text_input(
                "Note (saved with your decision)",
                key=f"reason_{event_id}",
                placeholder="Why you're keeping, splitting, or removing it.",
            )

            st.caption("**Keep** = it's one real event · **Split** = it's actually several · "
                       "**Remove** = it's not a real event.")
            b1, b2, b3 = st.columns(3)
            if b1.button("Keep", key=f"confirm_{event_id}"):
                _submit(client, event_id, "confirm", reason, analyst)
            if b2.button("Split", key=f"split_{event_id}"):
                _submit(client, event_id, "split", reason, analyst)
            if b3.button("Remove", key=f"reject_{event_id}"):
                _submit(client, event_id, "reject", reason, analyst)


_ACTION_WORD = {"confirm": "Kept", "split": "Marked for splitting", "reject": "Removed"}


def _submit(client, event_id: str, action: str, reason: str, analyst: str) -> None:
    """POST one append-only review verdict, reporting success/failure inline."""
    try:
        client.post_review(event_id, action, reason, analyst)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't save your decision: {exc}")
        return
    st.success(f"{_ACTION_WORD.get(action, action)}. Saved.")
