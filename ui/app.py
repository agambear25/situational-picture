"""
Streamlit entry point and screen router for the OSINT COP (Ukraine theater).

This module owns *only* layout and wiring: it builds the one API client, captures the analyst
identity, mounts the persistent honesty header, and routes each tab to its component's render().
All data access goes through CopApiClient so geometry stays cell-only (see api/coarsen.py) and
no screen ever builds a URL or touches the DB directly. Nothing here is targeting-grade.

Resilience contract: the API is a separate process and may be down. We never want a stack trace
on screen, so the whole tab body runs inside a guard that turns any client failure into st.error.
Individual components also guard their own calls; this is the outer net for import/wiring faults.
"""
from __future__ import annotations

import os

import streamlit as st

from ui.api_client import CopApiClient
from ui.components import (
    event_panel,
    honesty_header,
    label_studio,
    map_view,
    threshold_tuner,
    verify_queue,
)

# Tab order is part of the analyst's mental model (read -> review -> tune -> audit); keep it stable.
_TABS = ["Map", "Event", "Verify Queue", "Label Studio", "Threshold Tuner", "Replay/Insights"]


def _build_client() -> CopApiClient:
    # Base URL and theater come from the environment so the same image points at dev/staging/live
    # without code edits. Defaults match the local single-theater MVP (Ukraine / Donbas).
    return CopApiClient(
        base_url=os.environ.get("API_BASE_URL", "http://127.0.0.1:8000"),
        theater_id=os.environ.get("THEATER_ID", "ua_donbas"),
    )


def _render_replay_insights(client: CopApiClient, analyst: str) -> None:
    # Replay proves the event store is append-only and bit-reproducible (the integrity guarantee).
    # It mutates nothing, but it is expensive, so gate it behind an explicit button press.
    st.subheader("Replay integrity check")
    st.caption(
        "Re-materializes events from the observation log and compares the digest. "
        "bit_identical=True means no silent drift; dropped_obs should always be empty."
    )
    if st.button("Run replay check", key="replay_run"):
        result = client.admin_replay()
        identical = result.get("bit_identical")
        # Surface the headline as a status, not raw JSON, so a green/red read is instant.
        if identical:
            st.success(f"bit_identical = True · dropped_obs = {result.get('dropped_obs', [])}")
        else:
            st.error(f"bit_identical = False · dropped_obs = {result.get('dropped_obs', [])}")
        st.json(result)

    st.divider()

    # The rejection ledger is the "no-drop" promise made visible: nothing is discarded silently,
    # every filtered observation has a recorded reason. Show the summary as the audit surface.
    st.subheader("Rejection ledger (no-drop)")
    rejections = client.get_rejections()
    summary = rejections.get("summary", {"total": 0, "by_reason": {}})
    st.metric("Total rejected (with recorded reason)", summary.get("total", 0))
    by_reason = summary.get("by_reason", {})
    if by_reason:
        st.bar_chart(by_reason)
    else:
        st.caption("No rejections recorded for this theater.")

    st.divider()

    # Honest framing: the assessment/insights layer is not built yet. Say so rather than fake it.
    st.info(
        "Insights & assessment layer arrives in Phase 4. This tab currently exposes the integrity "
        "guarantees (replay + rejection ledger) that the later analytics will build on."
    )


def main() -> None:
    st.set_page_config(page_title="OSINT COP — Ukraine", layout="wide")

    client = _build_client()

    # Analyst name attributes every append-only annotation (review/label/verdict). Default keeps
    # the MVP usable without auth; in a real deployment this would come from the session identity.
    analyst = st.sidebar.text_input("Analyst", value="analyst")

    # Persistent across every tab: the honesty header states what this tool is and is NOT (it is
    # decision-support, not targeting; geometry is 1km-cell only). It must always be in view.
    honesty_header.render(client)

    # Outer resilience net: wiring or import faults below should read as an error banner, never a
    # raw traceback. Components own their own per-call guards; this catches the rest.
    try:
        tab_map, tab_event, tab_verify, tab_label, tab_tuner, tab_replay = st.tabs(_TABS)

        with tab_map:
            map_view.render(client)

        with tab_event:
            # No global event picker exists yet, so the Event tab takes a free-text id. We only
            # render the panel once an id is supplied to avoid a spurious 404 on first load.
            event_id = st.text_input("Event ID", key="event_id_input").strip()
            if event_id:
                event_panel.render(client, event_id, analyst)
            else:
                st.caption("Enter an event ID to inspect its evidence trail.")

        with tab_verify:
            verify_queue.render(client, analyst)

        with tab_label:
            label_studio.render(client, analyst)

        with tab_tuner:
            threshold_tuner.render(client)

        with tab_replay:
            _render_replay_insights(client, analyst)
    except Exception as exc:  # noqa: BLE001 - last-resort UI guard, see resilience contract above
        st.error(f"The COP API appears to be unavailable: {exc}")


# Streamlit executes the module top-to-bottom on every rerun; calling main() here is the entry.
main()
