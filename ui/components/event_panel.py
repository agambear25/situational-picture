"""
Event detail panel for the analyst COP. Renders a single fused event: its confidence header,
the source-by-source evidence trail, the inherited cell context, and the append-only review
actions (confirm / split / reject).

WHY this shape:
  - The whole point of the COP is *decision support*, not targeting. So this panel leads with
    trust signals (confidence band, independent families, flags) and an auditable evidence trail
    rather than a location. The API already coarsened geometry to a 1km cell, so we never have a
    precise coordinate to leak here — we only ever show cell_id + the cell's inherited context.
  - The only mutations are append-only reviews via the API (POST /review). Buttons map 1:1 to the
    review actions the queries layer accepts (confirm/split/reject/flag); we expose the three the
    analyst uses while triaging an event. The client/API enforce who may write — this is just UI.

Resilience: every client call is wrapped so a down API surfaces as st.error, never a stack trace.
"""
from __future__ import annotations

import streamlit as st

# Band -> RGB swatch for the confidence header. Kept local because no shared style module owns it
# yet; values track the api_client BAND_COLORS suggestion so the map view and this panel agree.
# Keys match the event "confidence_band" enum exactly (High/Medium/Low/Rumored).
BAND_COLORS: dict[str, list[int]] = {
    "High": [34, 197, 94],
    "Medium": [234, 179, 8],
    "Low": [249, 115, 22],
    "Rumored": [148, 163, 184],
}
_DEFAULT_BAND_RGB = [148, 163, 184]  # unknown/missing band -> neutral grey, never a hard failure

# Flags worth shouting about: these tell the analyst the event is not yet trustworthy. Surfaced as
# warnings so they don't get lost in the metrics row.
_PROMINENT_FLAGS = ("verification-needed", "echo-only", "single-source")

# Context keys we lift into the inherited-context table, in display order. Pulled from a constant
# so the field set is one obvious thing to edit rather than scattered through render().
_CONTEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("label", "Area name"),
    ("admin_l1", "Region"),
    ("admin_l2", "District"),
    ("admin_l3", "Locality"),
    ("landcover_label", "Land type"),
    ("builtup_pct", "Built-up area %"),
    ("has_bridge", "Bridge nearby"),
    ("nearest_road_class", "Nearest road"),
)


def _band_swatch(band: str | None) -> str:
    """Inline coloured dot for the confidence band; returns an HTML span for st.markdown."""
    rgb = BAND_COLORS.get(band or "", _DEFAULT_BAND_RGB)
    color = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
    return (
        f'<span style="display:inline-block;width:0.8em;height:0.8em;border-radius:50%;'
        f'background:{color};margin-right:0.4em;vertical-align:middle;"></span>'
    )


def _obs_sort_key(obs: dict):
    """Sort observations chronologically; missing timestamps sink to the bottom (empty < ISO)."""
    return obs.get("occurred_at") or ""


def render(client, event_id, analyst: str = "analyst") -> None:
    # --- fetch ---------------------------------------------------------------
    try:
        ev = client.get_event(event_id)
    except Exception as exc:  # API may be down; this panel must degrade, not crash the app.
        st.error(f"Could not load event {event_id}: {exc}")
        return
    if ev is None:
        st.warning("Event not found.")
        return

    # --- header: type + band + status ---------------------------------------
    band = ev.get("confidence_band")
    st.markdown(
        f"### {_band_swatch(band)}{ev.get('event_type', 'event')} "
        f"&nbsp;·&nbsp; {band or 'Unknown'} &nbsp;·&nbsp; `{ev.get('status', 'unknown')}`",
        unsafe_allow_html=True,
    )
    st.caption(f"Event ID: `{ev.get('event_id', event_id)}` · Area: `{ev.get('cell_id', '—')}`")

    # --- metrics row ---------------------------------------------------------
    flags = ev.get("flags") or []
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Reports", ev.get("n_sources", 0))
    c2.metric("Independent sources", ev.get("n_independent_families", 0))
    conf = ev.get("confidence")
    c3.metric("Confidence", f"{conf:.0%}" if isinstance(conf, (int, float)) else "—")
    c4.metric("Warnings", len(flags))

    # Plain-English meaning for the flags an operator must not over-read.
    _FLAG_PLAIN = {
        "single-source": "Only one source — not independently confirmed.",
        "echo-only": "Looks like the same report repeated, not separate confirmation.",
        "verification-needed": "Needs a person to check before relying on it.",
    }
    for flag in flags:
        if flag in _PROMINENT_FLAGS:
            st.warning(f"⚑ {_FLAG_PLAIN.get(flag, flag)}")
    other_flags = [f for f in flags if f not in _PROMINENT_FLAGS]
    if other_flags:
        st.caption("Other notes: " + ", ".join(other_flags))

    # --- evidence trail ------------------------------------------------------
    st.markdown("#### Where this came from")
    st.caption("Every report behind this event, oldest first. The ‘source type’ is what matters "
               "for confidence — two different source types is stronger than one repeated.")
    observations = sorted(ev.get("observations") or [], key=_obs_sort_key)
    if not observations:
        st.caption("No reports attached to this event.")
    for obs in observations:
        # Lead with source *family* and time so the analyst reads provenance, not raw source ids;
        # the family is what independence is judged on (echo vs. genuinely separate reporting).
        when = obs.get("occurred_at") or "time unknown"
        family = obs.get("source_family_id", "unknown-family")
        modality = obs.get("modality", "?")
        obs_type = obs.get("obs_type", "?")
        header = f"**{when}** · `{family}` · {modality} / {obs_type}"
        url = obs.get("source_url")
        label = obs.get("source_label") or obs.get("source_id") or "source"
        if url:
            header += f" · [{label}]({url})"  # link out to the underlying report when we have one
        st.markdown(header)
        excerpt = obs.get("excerpt")
        if excerpt:
            st.markdown(f"> {excerpt}")

    # --- inherited context ---------------------------------------------------
    st.markdown("#### About this location")
    context = ev.get("context") or {}
    if not context:
        st.caption("No background info available for this area.")
    else:
        # Small two-column table; only show fields the cell actually has so empties don't clutter.
        rows = [
            {"Field": display, "Value": context[key]}
            for key, display in _CONTEXT_FIELDS
            if context.get(key) is not None
        ]
        if rows:
            st.table(rows)
        else:
            st.caption("No background info available for this area.")

    # --- review actions (append-only) ---------------------------------------
    st.markdown("#### Your decision")
    st.caption("**Keep** = it's one real event · **Split** = it's actually several events · "
               "**Remove** = it's not a real event. Your choice is saved as a note.")
    reason = st.text_input(
        "Note (saved with your decision)", key=f"review_reason_{event_id}",
        help="Recommended for split and remove, so there's a record of why.",
    )

    def _submit(action: str) -> None:
        # Single path for all three actions so error handling stays in one place. Reviews are
        # append-only; the API records who/what/why and never mutates the event in place.
        try:
            client.post_review(event_id, action, reason, analyst)
        except Exception as exc:
            st.error(f"Couldn't save your decision: {exc}")
            return
        st.success("Saved.")

    b1, b2, b3 = st.columns(3)
    if b1.button("Keep", key=f"confirm_{event_id}"):
        _submit("confirm")
    if b2.button("Split", key=f"split_{event_id}"):
        _submit("split")
    if b3.button("Remove", key=f"reject_{event_id}"):
        _submit("reject")
