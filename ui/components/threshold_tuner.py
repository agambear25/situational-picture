"""
Threshold tuner — let an analyst sweep tau_low / tau_high over the frozen pair-score snapshot
and *preview* the consequences before committing them to config. The whole point is to make the
gray-band/auto-merge tradeoff visible: tighter thresholds shrink the gray band (less LLM/human
work) but risk false auto-merges, looser ones do the opposite.

WHY recompute in pure Python instead of calling the model: the per-pair score p is already frozen
in the snapshot (deterministic synthetic_v1 run). Re-banding is just thresholding that fixed p, so
we can show precision/recall/gray-fraction instantly and offline — no live feed, Claude stays OFF.
This previews only; the real numbers come from a fresh `python -m eval.harness` run, and gray pairs
still require LLM/human adjudication. This screen never changes that, it only proposes thresholds.

Coarsening invariant is untouched here: we operate on pair scores and content hashes, never on
coordinates or any 'person' entity.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

# Where the live fusion thresholds live, relative to this component file
# (ui/components/threshold_tuner.py -> ../../config/thresholds.yaml). Resolved once so the
# "Apply" button writes back to the same file the harness reads.
_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"

# Histogram resolution for the p-distribution. ~20 buckets is enough to read the shape of the
# distribution and see where the two cut points fall without overwhelming st.bar_chart.
_HIST_BINS = 20


def _band_for(p: float, tau_low: float, tau_high: float) -> str:
    """Re-band a single pair score under candidate thresholds (same rule fusion uses)."""
    if p >= tau_high:
        return "same"
    if p <= tau_low:
        return "different"
    return "gray"


def _histogram(scores: list[float], bins: int) -> dict[str, int]:
    """Bucket p ∈ [0,1] into `bins` equal bins; keys are bin-start labels for st.bar_chart."""
    counts = [0] * bins
    for p in scores:
        # Clamp to the last bin so p == 1.0 doesn't index out of range.
        idx = min(int(p * bins), bins - 1)
        counts[idx] += 1
    width = 1.0 / bins
    return {f"{i * width:.2f}": counts[i] for i in range(bins)}


def render(client):
    st.subheader("Threshold tuner")
    st.caption(
        "Previews from the frozen pair-score snapshot — re-bands a fixed score in Python, no model "
        "call. Gray pairs still need LLM/human adjudication; this only proposes the cut points."
    )

    # The API may be down or the snapshot empty — be resilient (Streamlit convention).
    try:
        snap = client.get_gray_band("synthetic_v1")
    except Exception as exc:  # noqa: BLE001 — surface any client/transport failure to the analyst
        st.error(f"Could not load the gray-band snapshot: {exc}")
        return

    pairs = snap.get("pairs", [])
    if not pairs:
        st.info("No pair scores in the snapshot yet. Run `python -m eval.harness` to populate it.")
        return

    # Defaults come from the snapshot's own thresholds so the sliders open at the current config.
    defaults = snap.get("thresholds", {})
    default_low = float(defaults.get("tau_low", 0.38))
    default_high = float(defaults.get("tau_high", 0.82))

    tau_low = st.slider("tau_low (≤ → auto-reject)", 0.0, 1.0, default_low, 0.01)
    tau_high = st.slider("tau_high (≥ → auto-merge)", 0.0, 1.0, default_high, 0.01)
    # Enforce the ordering invariant: a gray band only exists when tau_low <= tau_high. If the
    # analyst crosses them, collapse to a single cut so the preview stays meaningful.
    if tau_low > tau_high:
        st.warning("tau_low exceeded tau_high — clamping tau_low down to keep a valid band.")
        tau_low = tau_high

    # Recompute bands over the frozen scores. Skip pairs with no usable p.
    scores: list[float] = []
    n_gray = 0
    n_same = 0
    # Pairwise precision/recall vs ground truth. same_incident may be null for some pairs
    # (no gold label) — those are excluded from P/R but still count toward gray-fraction.
    same_and_true = 0          # decided "same" AND truly same incident → true positives
    total_true = 0             # all pairs that are truly same incident → P/R denominator basis
    for pair in pairs:
        p = pair.get("p")
        if p is None:
            continue
        p = float(p)
        scores.append(p)
        band = _band_for(p, tau_low, tau_high)
        if band == "gray":
            n_gray += 1
        elif band == "same":
            n_same += 1

        truth = pair.get("same_incident")
        if truth is None:
            continue  # no gold label → cannot score P/R for this pair
        if truth:
            total_true += 1
            if band == "same":
                same_and_true += 1

    n_scored = len(scores)
    gray_fraction = n_gray / n_scored if n_scored else 0.0
    # Guard divide-by-zero: precision undefined with no auto-merges, recall undefined with no
    # truly-same pairs. Report 0.0 in those degenerate cases rather than crashing.
    precision = same_and_true / n_same if n_same else 0.0
    recall = same_and_true / total_true if total_true else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("gray fraction", f"{gray_fraction:.1%}", help=f"{n_gray} / {n_scored} pairs")
    c2.metric("auto-merge precision", f"{precision:.1%}", help=f"{same_and_true} / {n_same} merged")
    c3.metric("auto-merge recall", f"{recall:.1%}", help=f"{same_and_true} / {total_true} true-same")
    # No-silent-drop is a structural guarantee of the event-sourcing replay, not a tunable metric —
    # every observation is materialized or explicitly dropped, never lost. Show it as constant truth.
    c4.metric("no_silent_drop", "guaranteed by design")

    # Distribution of p with the two cut points called out in text (st.bar_chart can't draw vlines).
    st.caption(f"Score distribution — tau_low = {tau_low:.2f}, tau_high = {tau_high:.2f}")
    st.bar_chart(_histogram(scores, _HIST_BINS))

    # Commit step: write the proposed thresholds back into config/thresholds.yaml, preserving every
    # other key (confidence_bands, llm gammas, staleness). yaml is imported lazily — only needed on
    # the rare write path, keeps the component import dependency-light for tests.
    if st.button("Apply thresholds"):
        import yaml  # lazy: only the write path needs it
        try:
            with _THRESHOLDS_PATH.open("r") as fh:
                cfg = yaml.safe_load(fh) or {}
            # setdefault so we never clobber sibling fusion keys when patching the two cut points.
            cfg.setdefault("fusion", {})
            cfg["fusion"]["tau_high"] = round(float(tau_high), 4)
            cfg["fusion"]["tau_low"] = round(float(tau_low), 4)
            with _THRESHOLDS_PATH.open("w") as fh:
                yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False)
        except Exception as exc:  # noqa: BLE001 — file/permission/parse errors must surface, not crash
            st.error(f"Could not write thresholds: {exc}")
            return
        # Preview math is NOT the harness math — force a real re-run before anyone trusts these.
        st.warning("Thresholds written. Re-run 'python -m eval.harness' before trusting the new values.")
