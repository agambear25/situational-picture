"""
THE PHASE-1 GATE. `python -m eval.harness` must exit 0 before any live feed is wired
(the eval-gate-before-live-feed hard rule). Runs entirely offline: frozen verdicts stand
in for Ollama, in-memory fixtures stand in for the DB.

Gates (HARD = blocking):
  - no_silent_drop        == True   (HARD)  every observation accounted for
  - replay bit-identical  == True   (HARD)  rebuild from log reproduces the picture
  - event_level_recall    >= 0.95
  - pairwise_recall       >= 0.90
  - over_merge_rate        == 0             no must-not-merge pair fused
  - gray band fully frozen (0 model calls, 0 degraded)  fixtures/verdicts in sync
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from ingest.contract import Observation
from llm.backend import FrozenBackend
from llm.cache import FrozenVerdictCache
from fusion.fuse import fuse
from fusion.replay import assert_bit_identical
from eval import metrics

FIX = Path(__file__).parent / "fixtures"

TARGETS = {
    "event_recall_min": 0.95,
    "pairwise_recall_min": 0.90,
}


def load_observations() -> list[Observation]:
    data = yaml.safe_load((FIX / "synthetic_v1.yaml").read_text())
    return [Observation.from_fixture(d) for d in data["observations"]]


def load_ground_truth() -> dict:
    return yaml.safe_load((FIX / "ground_truth_v1.yaml").read_text())


def run_gate(verbose: bool = True) -> tuple[bool, dict]:
    obs = load_observations()
    gt = load_ground_truth()
    input_ids = {o.obs_id for o in obs}

    cache = FrozenVerdictCache(FIX / "verdicts_v1.json")
    backend = FrozenBackend()

    result = fuse(obs, cache, backend, theater_id=gt["theater_id"])
    # replay: same inputs + same frozen cache -> identical digest
    replay_result = fuse(obs, cache, backend, theater_id=gt["theater_id"])
    bit_identical = assert_bit_identical(result, replay_result)

    drop = metrics.no_silent_drop_audit(result, input_ids)
    pw = metrics.pairwise_pr(result, gt)
    er = metrics.event_level_recall(result, gt)
    frag = metrics.fragmentation_rate(result, gt)
    over = metrics.over_merge_rate(result, gt)
    bands = metrics.band_correctness(result, gt)
    echo = metrics.echo_test(result, gt)
    mnm = metrics.must_not_merge_violations(result, gt)
    counters = result.counters

    gray_frozen_ok = counters.get("model_calls", 0) == 0 and counters.get("degraded_keep_separate", 0) == 0

    checks = {
        "no_silent_drop (HARD)": drop["ok"],
        "replay_bit_identical (HARD)": bit_identical,
        "event_recall>=0.95": er >= TARGETS["event_recall_min"],
        "pairwise_recall>=0.90": pw["recall"] >= TARGETS["pairwise_recall_min"],
        "over_merge_rate==0": over == 0.0,
        "no_must_not_merge_violations": len(mnm) == 0,
        "gray_band_fully_frozen": gray_frozen_ok,
        "echo_test": echo["ok"],
        "band_correctness==1.0": bands["rate"] == 1.0,
    }
    ok = all(checks.values())

    report = {
        "n_obs": len(obs), "n_events": len(result.events), "n_incidents": gt["n_incidents"],
        "event_recall": round(er, 4), "pairwise": {k: round(v, 4) if isinstance(v, float) else v for k, v in pw.items()},
        "fragmentation_rate": round(frag, 4), "over_merge_rate": round(over, 4),
        "band_correctness": bands, "echo_test": echo, "must_not_merge_violations": mnm,
        "no_silent_drop": drop, "counters": counters,
        "replay_bit_identical": bit_identical, "digest": result.digest(),
        "checks": checks, "ok": ok,
    }

    if verbose:
        _print_report(report)
    return ok, report


def _print_report(r: dict) -> None:
    print("=" * 70)
    print(f"FUSION EVAL GATE — {r['n_obs']} obs, {r['n_events']} events, {r['n_incidents']} incidents")
    print("=" * 70)
    print(f"  event_recall      : {r['event_recall']}")
    print(f"  pairwise recall   : {r['pairwise']['recall']}   precision: {r['pairwise']['precision']}")
    print(f"  fragmentation     : {r['fragmentation_rate']}")
    print(f"  over_merge_rate   : {r['over_merge_rate']}")
    print(f"  band correctness  : {r['band_correctness']['rate']}  ({r['band_correctness']['checked']} checked)")
    print(f"  echo test         : {'ok' if r['echo_test']['ok'] else r['echo_test']['failures']}")
    print(f"  no_silent_drop    : {r['no_silent_drop']['ok']}  "
          f"(in_events={r['no_silent_drop']['in_events']}, rejected={r['no_silent_drop']['rejected']})")
    print(f"  replay identical  : {r['replay_bit_identical']}   digest={r['digest'][:16]}…")
    print(f"  counters          : {r['counters']}")
    print("-" * 70)
    for name, passed in r["checks"].items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("=" * 70)
    print("GATE PASSED ✓" if r["ok"] else "GATE FAILED ✗")


def main():
    ok, _ = run_gate(verbose=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
