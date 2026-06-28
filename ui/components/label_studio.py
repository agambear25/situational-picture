"""
"Add events" screen — how an analyst records known real incidents and helps the computer with
uncertain matches, WITHOUT hand-editing fixture files.

Two surfaces, both offline against the SYNTHETIC eval corpus (no live feed, Claude OFF):

  MODE 1  Add a known event   — describe ONE real incident (what happened, when, roughly where,
          how well-confirmed, and the reports behind it). Saved as an append-only row in
          label_annotation (kind="incident_label"); the eval fixtures are GENERATED from these
          rows (never hand-edited), so the example set stays an auditable function of labels.

  MODE 2  Help the computer decide — for each pair the matcher couldn't decide on its own, show
          the two reports side by side and record "same event" / "different events". Those
          decisions seed the frozen cache that stands in for the local model during replay.

The user sees plain language; the dev contracts (append-only annotations, coarsening to a 1km
cell, fixture regeneration) are unchanged and described in code comments only.
"""
from __future__ import annotations

import json
from datetime import datetime, time as _time
from functools import lru_cache
from pathlib import Path

import streamlit as st

# Plain-language confidence choices -> the canonical band the harness checks fusion against.
# (Bands mirror config/thresholds.yaml:confidence_bands.)
_CONFIDENCE_CHOICES = {
    "Very well confirmed — several independent sources": "High",
    "Fairly confirmed — a couple of sources": "Medium",
    "Weak — one source with a little support": "Low",
    "Unconfirmed — a single report": "Rumored",
}

_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"

# Fallback list if the taxonomy file can't be read; values must be real event_types.
_FALLBACK_TYPES = [
    "strike", "airstrike", "artillery_fire", "explosion", "building_damaged",
    "bridge_damaged", "fire", "flood", "other",
]

# Advanced (raw JSON) template — only shown in the "Advanced" expander for power users.
_OBS_TEMPLATE = [
    {
        "ref": "obs-1",
        "source_id": "telegram_channel_x",
        "type": "bridge_damaged",
        "time": "2026-06-20T08:30:00Z",
        "lon": 37.62,
        "lat": 48.01,
        "place": "near the rail bridge, town X",
        "text": "verbatim excerpt of the public report this observation came from",
    }
]


@lru_cache(maxsize=1)
def _event_types() -> list[str]:
    """Load valid event types from the taxonomy so the dropdown can never offer an invalid one."""
    try:
        import yaml
        data = yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8")) or {}
        types = list(data.get("event_types") or [])
        return types or _FALLBACK_TYPES
    except (OSError, ValueError):
        return _FALLBACK_TYPES


def _pretty(t: str) -> str:
    return t.replace("_", " ")


def render(client, analyst: str = "analyst") -> None:
    st.subheader("Add events")
    st.caption(
        "Tell the system about real events you already know happened. This builds the set of "
        "examples it's checked against — so it's worth being accurate. Nothing here goes live; "
        "it's all offline practice data."
    )

    mode = st.radio(
        "What do you want to do?",
        ["Add a known event", "Help the computer decide"],
        horizontal=True,
        help="‘Add a known event’ records a real incident. ‘Help the computer decide’ settles "
             "matches the system wasn't sure about.",
    )

    if mode == "Add a known event":
        _render_incident_labeller(client, analyst)
    else:
        _render_gray_adjudicator(client, analyst)


# --------------------------------------------------------------------------------------------
# MODE 1 — Add a known event (guided form; raw JSON available under "Advanced")
# --------------------------------------------------------------------------------------------
def _render_incident_labeller(client, analyst: str) -> None:
    st.markdown("#### Describe one real event")
    st.caption(
        "Fill in what happened, when, roughly where, and paste the reports you have about it "
        "(one per line). Each line is treated as a separate source. The location is rounded to "
        "a 1km area when saved — exact coordinates are never stored."
    )

    types = _event_types()
    # Default the dropdown to a common type if present.
    default_idx = types.index("building_damaged") if "building_damaged" in types else 0

    with st.form("incident_label_form", clear_on_submit=False):
        incident_name = st.text_input(
            "Short name for this event",
            placeholder="e.g. avdiivka-coke-plant-strike-2024-03-10",
            help="Just a label to find it later. Lowercase with dashes works well.",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            type_label = st.selectbox(
                "What happened?", [_pretty(t) for t in types], index=default_idx,
                help="The kind of event. Pick the closest match.")
            event_type = types[[_pretty(t) for t in types].index(type_label)]
        with col_b:
            confidence_label = st.selectbox(
                "How well-confirmed is it?", list(_CONFIDENCE_CHOICES.keys()),
                help="Your honest read of how solid this event is, based on the reports you have.")

        col_d, col_t = st.columns(2)
        with col_d:
            on_date = st.date_input("Date it happened")
        with col_t:
            at_time = st.time_input("Approx. time (UTC)", value=_time(12, 0))

        st.markdown("**Where** — give a place name, or coordinates, or both:")
        place = st.text_input("Place name", placeholder="e.g. Avdiivka coke plant")
        col_lon, col_lat = st.columns(2)
        with col_lon:
            lon_str = st.text_input("Longitude (optional)", placeholder="e.g. 37.75")
        with col_lat:
            lat_str = st.text_input("Latitude (optional)", placeholder="e.g. 48.14")

        st.markdown("**The reports** — paste what was reported, one per line:")
        reports_text = st.text_area(
            "Reports (one per line)",
            height=160,
            label_visibility="collapsed",
            placeholder="Russian strike hit the plant, large fire reported\n"
                        "Satellite image shows new damage to the main building",
            help="Each line counts as one source. Two lines from two different sources = a "
                 "better-confirmed event than one line.")

        submitted = st.form_submit_button("Save this event")

    if submitted:
        ok, payload_or_msg = _build_incident_payload(
            incident_name, event_type, confidence_label, on_date, at_time,
            place, lon_str, lat_str, reports_text)
        if not ok:
            st.error(payload_or_msg)
        else:
            _save_incident(client, payload_or_msg, analyst)

    # Power-user escape hatch: the original raw-JSON entry, kept but out of the way.
    with st.expander("Advanced: enter as raw JSON instead"):
        st.caption("For multiple locations/times per event. Same result as the form above.")
        with st.form("incident_json_form", clear_on_submit=False):
            adv_name = st.text_input("Short name for this event", key="adv_name")
            adv_band = st.selectbox("Confidence band", ["High", "Medium", "Low", "Rumored"])
            adv_families = st.number_input("Independent sources expected", min_value=0, step=1, value=2)
            adv_obs = st.text_area("Observations (JSON list)",
                                   value=json.dumps(_OBS_TEMPLATE, indent=2), height=260)
            adv_submit = st.form_submit_button("Save (JSON)")
        if adv_submit:
            if not adv_name.strip():
                st.error("Please give the event a short name.")
            else:
                try:
                    observations = json.loads(adv_obs)
                except json.JSONDecodeError as exc:
                    st.error(f"The JSON isn't valid: {exc}")
                else:
                    if not isinstance(observations, list) or not observations:
                        st.error("Observations must be a non-empty list.")
                    else:
                        _save_incident(client, {
                            "incident_id": adv_name.strip(),
                            "expect": {"band": adv_band, "n_families": int(adv_families)},
                            "must_not_merge_with": [],
                            "observations": observations,
                        }, analyst)

    st.divider()
    st.markdown("#### Update the example set")
    st.caption(
        "After adding events, click this to fold them into the set the system is checked against. "
        "Safe to run any time."
    )
    if st.button("Update examples from everything I've added"):
        try:
            result = client.regenerate_fixtures()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Couldn't update the examples: {exc}")
            return
        st.success("Examples updated.")
        with st.expander("Technical detail"):
            st.json(result)


def _build_incident_payload(name, event_type, confidence_label, on_date, at_time,
                            place, lon_str, lat_str, reports_text):
    """Turn the guided form fields into the incident-label payload, or return (False, message)."""
    if not name.strip():
        return False, "Please give the event a short name."

    reports = [ln.strip() for ln in (reports_text or "").splitlines() if ln.strip()]
    if not reports:
        return False, "Add at least one report (one line) describing what was seen."

    lon = lat = None
    if lon_str.strip() or lat_str.strip():
        try:
            lon = float(lon_str); lat = float(lat_str)
        except ValueError:
            return False, "Longitude and latitude must be numbers (or leave both blank and use a place name)."
    if lon is None and not place.strip():
        return False, "Give a place name or coordinates so the event has a location."

    iso_time = datetime.combine(on_date, at_time).strftime("%Y-%m-%dT%H:%M:%SZ")
    observations = []
    for i, line in enumerate(reports, start=1):
        obs = {"ref": f"obs-{i}", "source_id": f"source-{i}", "type": event_type,
               "time": iso_time, "text": line}
        if place.strip():
            obs["place"] = place.strip()
        if lon is not None:
            obs["lon"] = lon; obs["lat"] = lat
        observations.append(obs)

    return True, {
        "incident_id": name.strip(),
        # Each report line = one source, so expected independent sources = number of reports.
        "expect": {"band": _CONFIDENCE_CHOICES[confidence_label], "n_families": len(observations)},
        "must_not_merge_with": [],
        "observations": observations,
    }


def _save_incident(client, payload: dict, analyst: str) -> None:
    try:
        result = client.post_label("incident_label", payload, analyst)
    except Exception as exc:  # noqa: BLE001 — UI must stay resilient to any client error
        st.error(f"Couldn't save the event: {exc}")
        return
    st.success(f"Saved “{payload['incident_id']}”. (Click ‘Update examples’ below when you're done adding.)")


# --------------------------------------------------------------------------------------------
# MODE 2 — Help the computer decide (was: gray-band adjudicator)
# --------------------------------------------------------------------------------------------
def _render_gray_adjudicator(client, analyst: str) -> None:
    st.markdown("#### Decide the close calls")
    st.caption(
        "These are pairs of reports the system couldn't confidently call ‘same event’ or "
        "‘different events’. Read both and decide. Your answers teach it for next time."
    )

    try:
        snap = client.get_gray_band("synthetic_v1")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't load the pairs to review: {exc}")
        return

    pairs = snap.get("gray_pairs", [])
    if not pairs:
        st.info("Nothing to decide right now — the system was confident about everything.")
        return

    run_id = snap.get("run_id")
    st.caption(f"{len(pairs)} pair(s) to decide.")

    for pair in pairs:
        a = pair.get("a", {})
        b = pair.get("b", {})
        key = f"{pair.get('obs_a')}__{pair.get('obs_b')}"

        with st.container(border=True):
            p = pair.get("p")
            if isinstance(p, (int, float)):
                st.markdown(f"**How similar the system thought these were: {p:.0%}**")

            col_a, col_b = st.columns(2)
            _render_obs_column(col_a, "Report A", a)
            _render_obs_column(col_b, "Report B", b)

            factors = pair.get("factors") or {}
            if factors:
                with st.expander("Why the system was unsure (details)"):
                    st.json(factors)

            if pair.get("same_incident") is not None:
                with st.expander("Reveal the answer (practice data only)"):
                    truth = "the SAME event" if pair["same_incident"] else "DIFFERENT events"
                    st.write(f"These are actually **{truth}**.")

            choice = st.radio(
                "Your call", ["Same event", "Different events"],
                horizontal=True, key=f"choice_{key}")
            conf = st.slider(
                "How sure are you?", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                key=f"conf_{key}", help="0 = a guess, 1 = certain.")
            rationale = st.text_input(
                "Why? (optional)", key=f"rat_{key}",
                placeholder="e.g. same place and time, both describe the same building")

            if st.button("Save my decision", key=f"save_{key}"):
                try:
                    result = client.post_gray_verdict(
                        content_hash_a=a.get("content_hash"),
                        content_hash_b=b.get("content_hash"),
                        obs_type_a=a.get("obs_type"),
                        obs_type_b=b.get("obs_type"),
                        same=(choice == "Same event"),
                        confidence=conf,
                        analyst=analyst,
                        rationale=rationale,
                        run_id=run_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Couldn't save your decision: {exc}")
                else:
                    st.success("Saved. Thanks — that helps.")


def _render_obs_column(col, side: str, obs: dict) -> None:
    """Render one report's safe (cell-level) fields. No coordinates ever appear here —
    the API only returns content_hash + cell_id for observations."""
    with col:
        st.markdown(f"**{side}**")
        st.write(obs.get("text", ""))
        st.caption(
            f"type: {obs.get('obs_type')} · area: {obs.get('cell_id')} · "
            f"when: {obs.get('occurred_start')}"
        )
