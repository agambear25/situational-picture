"""
Label Studio — the analyst tool that replaces hand-editing the fixture YAML.

Two append-only labelling surfaces, both running offline against the SYNTHETIC eval corpus
(no live feed, Claude OFF) so tuning is useful BEFORE the live gate goes green:

  MODE 1  Incident labeller     — define ONE ground-truth real incident (its expected
          confidence band + independent-family count + member observations). The submitted
          payload becomes an append-only row in label_annotation (kind="incident_label").
          The realworld_ua_v1.yaml / verdicts_v1.json fixtures are GENERATED from these rows
          via /fixtures/regenerate — never hand-edited — so the eval corpus stays an
          auditable function of analyst labels, not a file someone tweaked.

  MODE 2  Gray-band adjudicator — for each pair fuse() left in the gray band at the current
          thresholds, show both observations side-by-side with the score breakdown and let
          the analyst record same/different. Those verdicts seed the frozen verdict cache that
          stands in for the local LLM during replay, so what is adjudicated here is what ships.

Why this exists: editing YAML by hand silently drifts the eval corpus from reality and breaks
bit-identical replay. Routing every label through the append-only annotation tables keeps the
corpus reproducible and the provenance intact.

Analytical-not-targeting: the API only ever returns cell_id + content_hash for observations —
never precise coordinates — so nothing here can leak a location. The free-text observation
form below DOES let an analyst type a lon/lat for a *known real incident* they are defining as
ground truth; that is an offline label of public reporting, not a live targeting feed, and it
is coarsened to a 1km cell before it ever reaches an event payload.
"""
from __future__ import annotations

import json

import streamlit as st

# Band vocabulary mirrors config/thresholds.yaml:confidence_bands (High/Medium/Low/Rumored).
# Kept as a named constant (not an inline literal) so the option list has a single source of
# truth and a comment pointing at the config that defines the cutoffs. The labeller records the
# band an analyst EXPECTS a real incident to land in; the harness then checks fusion against it.
EXPECTED_BANDS = ["High", "Medium", "Low", "Rumored"]

# Documented shape for one observation in the incident labeller's JSON text area. Shown to the
# analyst as a template so the free-text entry stays parseable by /fixtures/regenerate.
_OBS_TEMPLATE = [
    {
        "ref": "obs-1",
        "source_id": "telegram_channel_x",
        "type": "bridge_damaged",
        "time": "2026-06-20T08:30:00Z",
        # Provide EITHER lon/lat (decimal degrees) OR a place name — the fixture builder
        # geocodes/coarsens to a 1km cell. Never a street address; ground-truth granularity
        # is the cell, same as every event payload.
        "lon": 37.62,
        "lat": 48.01,
        "place": "near the rail bridge, town X",
        "text": "verbatim excerpt of the public report this observation came from",
    }
]


def render(client, analyst: str = "analyst") -> None:
    st.subheader("Label Studio")
    st.caption(
        "Append-only labelling on the synthetic eval corpus (offline, Claude OFF). "
        "Fixtures are generated from these labels — not hand-edited."
    )

    mode = st.radio("Mode", ["Incident labeller", "Gray-band adjudicator"], horizontal=True)

    if mode == "Incident labeller":
        _render_incident_labeller(client, analyst)
    else:
        _render_gray_adjudicator(client, analyst)


# --------------------------------------------------------------------------------------------
# MODE 1 — Incident labeller
# --------------------------------------------------------------------------------------------
def _render_incident_labeller(client, analyst: str) -> None:
    st.markdown("#### Define a real incident")
    st.caption(
        "One submission = one ground-truth incident. Writes an append-only row to "
        "label_annotation (kind=incident_label). The realworld_ua_v1.yaml / verdicts_v1.json "
        "fixtures are GENERATED from all such rows via the button below — never hand-edited."
    )

    # A form batches the widgets so a single submit builds one atomic label payload.
    with st.form("incident_label_form", clear_on_submit=False):
        incident_id = st.text_input(
            "Incident ID",
            placeholder="ua_donbas_bridge_2026_06_20",
            help="Stable slug for this real incident; becomes the fixture key.",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            expected_band = st.selectbox(
                "Expected confidence band",
                EXPECTED_BANDS,
                help="Band you expect fusion to assign once this incident's obs are fused.",
            )
        with col_b:
            n_families = st.number_input(
                "Expected independent-family count",
                min_value=0,
                step=1,
                value=2,
                help="How many independent source families should corroborate this incident.",
            )

        st.markdown("**Observations** (JSON list)")
        st.caption(
            "Each observation: ref, source_id, type, time (ISO 8601), lon+lat OR place, text. "
            "lon/lat or place is coarsened to a 1km cell by the fixture builder."
        )
        obs_text = st.text_area(
            "Observations JSON",
            value=json.dumps(_OBS_TEMPLATE, indent=2),
            height=320,
            label_visibility="collapsed",
        )

        submitted = st.form_submit_button("Submit incident label")

    if submitted:
        # Validate locally before the round-trip so the analyst gets an immediate, specific
        # error instead of a 500 from the API parsing a malformed body.
        if not incident_id.strip():
            st.error("Incident ID is required.")
            return
        try:
            observations = json.loads(obs_text)
        except json.JSONDecodeError as exc:
            st.error(f"Observations JSON is invalid: {exc}")
            return
        if not isinstance(observations, list) or not observations:
            st.error("Observations must be a non-empty JSON list.")
            return

        payload = {
            "incident_id": incident_id.strip(),
            "expect": {"band": expected_band, "n_families": int(n_families)},
            # must_not_merge_with stays empty here; cross-incident "do not merge" constraints
            # are recorded via the gray-band adjudicator's same/different verdicts.
            "must_not_merge_with": [],
            "observations": observations,
        }

        # The API may be down — surface it rather than crash the screen.
        try:
            result = client.post_label("incident_label", payload, analyst)
        except Exception as exc:  # noqa: BLE001 — UI must stay resilient to any client error
            st.error(f"Could not save incident label: {exc}")
            return

        st.success(f"Saved incident label (label_id={result.get('label_id')}).")

    st.divider()
    st.markdown("#### Regenerate fixtures")
    st.caption(
        "Rebuilds realworld_ua_v1.yaml and verdicts_v1.json from ALL append-only labels. "
        "Run this after adding labels so the eval corpus reflects them."
    )
    if st.button("Regenerate fixtures from all labels"):
        try:
            result = client.regenerate_fixtures()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fixture regeneration failed: {exc}")
            return
        st.success("Fixtures regenerated.")
        st.json(result)


# --------------------------------------------------------------------------------------------
# MODE 2 — Gray-band adjudicator
# --------------------------------------------------------------------------------------------
def _render_gray_adjudicator(client, analyst: str) -> None:
    st.markdown("#### Adjudicate gray-band pairs")
    st.caption(
        "Pairs fuse() left undecided at the current thresholds. Your verdict seeds the frozen "
        "verdict cache used during replay — what you decide here is what ships."
    )

    try:
        snap = client.get_gray_band("synthetic_v1")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load gray-band snapshot: {exc}")
        return

    pairs = snap.get("gray_pairs", [])
    if not pairs:
        st.info("No gray-band pairs at current thresholds.")
        return

    run_id = snap.get("run_id")
    st.caption(f"{len(pairs)} gray pair(s) at run_id={run_id}.")

    for pair in pairs:
        a = pair.get("a", {})
        b = pair.get("b", {})
        # Unique, stable key per pair so each pair's widgets keep independent state across
        # reruns. obs ids are unique within a snapshot.
        key = f"{pair.get('obs_a')}__{pair.get('obs_b')}"

        with st.container(border=True):
            p = pair.get("p")
            st.markdown(f"**Pair `{key}`** — fusion p = {p:.3f}" if isinstance(p, (int, float))
                        else f"**Pair `{key}`**")

            col_a, col_b = st.columns(2)
            _render_obs_column(col_a, "A", a)
            _render_obs_column(col_b, "B", b)

            # Per-factor score breakdown — the WHY behind p, so the analyst can sanity-check.
            factors = pair.get("factors") or {}
            if factors:
                with st.expander("Score breakdown (factors)"):
                    st.json(factors)

            # same_incident is the synthetic corpus's ground truth. Hide it behind an expander
            # so the analyst can self-check without it anchoring their verdict by default.
            if pair.get("same_incident") is not None:
                with st.expander("Reveal ground-truth hint (synthetic only)"):
                    truth = "SAME incident" if pair["same_incident"] else "DIFFERENT incidents"
                    st.write(f"Ground truth: **{truth}**")

            choice = st.radio(
                "Verdict",
                ["same", "different"],
                horizontal=True,
                key=f"choice_{key}",
            )
            # Confidence on [0,1] mirrors the fusion score scale; default 0.5 = no lean.
            conf = st.slider(
                "Confidence",
                min_value=0.0,
                max_value=1.0,
                value=0.5,
                step=0.05,
                key=f"conf_{key}",
            )
            rationale = st.text_input(
                "Rationale (optional)",
                key=f"rat_{key}",
                placeholder="Why same/different — cites the deciding factor.",
            )

            if st.button("Save verdict", key=f"save_{key}"):
                try:
                    result = client.post_gray_verdict(
                        content_hash_a=a.get("content_hash"),
                        content_hash_b=b.get("content_hash"),
                        obs_type_a=a.get("obs_type"),
                        obs_type_b=b.get("obs_type"),
                        same=(choice == "same"),
                        confidence=conf,
                        analyst=analyst,
                        rationale=rationale,
                        run_id=run_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not save verdict: {exc}")
                else:
                    st.success(f"Saved verdict (label_id={result.get('label_id')}).")


def _render_obs_column(col, side: str, obs: dict) -> None:
    """Render one observation's safe (cell-level) fields. No coordinates ever appear here —
    the API only returns content_hash + cell_id for observations."""
    with col:
        st.markdown(f"**Obs {side}**")
        st.write(obs.get("text", ""))
        st.caption(
            f"type: `{obs.get('obs_type')}` · cell: `{obs.get('cell_id')}` · "
            f"start: {obs.get('occurred_start')}"
        )
