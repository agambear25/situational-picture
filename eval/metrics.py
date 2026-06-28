"""
Pure metric functions over a FusionResult + ground truth. No I/O, no DB.
Each captures one of the four failure modes the design names:
  Drop (no_silent_drop), Over-merge (over_merge_rate / pairwise precision),
  Fragment (fragmentation_rate / pairwise & event recall), Mis-confidence (band_correctness).
"""
from __future__ import annotations

from itertools import combinations


def predicted_partition(result) -> dict[str, str]:
    """obs_id -> event_id for every observation that landed in an event."""
    pred = {}
    for e in result.events:
        for o in e.created_from_obs:
            pred[o] = e.event_id
    return pred


def incident_members(gt: dict) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for obs_id, inc in gt["partition"].items():
        members.setdefault(inc, []).append(obs_id)
    return members


def event_level_recall(result, gt: dict) -> float:
    """Fraction of true incidents whose observations all land in a single event."""
    pred = predicted_partition(result)
    members = incident_members(gt)
    recovered = 0
    for obs_ids in members.values():
        eids = {pred.get(o) for o in obs_ids}
        if len(eids) == 1 and None not in eids:
            recovered += 1
    return recovered / len(members) if members else 1.0


def pairwise_pr(result, gt: dict) -> dict:
    pred = predicted_partition(result)
    part = gt["partition"]
    tp = fp = fn = tn = 0
    for a, b in combinations(sorted(part), 2):
        true_same = part[a] == part[b]
        pred_same = pred.get(a) is not None and pred.get(a) == pred.get(b)
        if true_same and pred_same:
            tp += 1
        elif true_same and not pred_same:
            fn += 1
        elif not true_same and pred_same:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def no_silent_drop_audit(result, input_obs_ids: set[str]) -> dict:
    cov = result.coverage(input_obs_ids)
    return {"ok": result.no_silent_drop(input_obs_ids), **cov}


def fragmentation_rate(result, gt: dict) -> float:
    """Fraction of true incidents split across more than one event."""
    pred = predicted_partition(result)
    members = incident_members(gt)
    fragmented = 0
    for obs_ids in members.values():
        eids = {pred.get(o) for o in obs_ids}
        if len([e for e in eids if e is not None]) > 1:
            fragmented += 1
    return fragmented / len(members) if members else 0.0


def over_merge_rate(result, gt: dict) -> float:
    """Fraction of predicted events that contain observations from more than one incident."""
    part = gt["partition"]
    over = 0
    for e in result.events:
        incs = {part.get(o) for o in e.created_from_obs}
        if len(incs) > 1:
            over += 1
    return over / len(result.events) if result.events else 0.0


def band_correctness(result, gt: dict) -> dict:
    """Among incidents recovered into a single event, fraction whose band matches expectation."""
    pred = predicted_partition(result)
    members = incident_members(gt)
    events_by_id = {e.event_id: e for e in result.events}
    checked = correct = 0
    mismatches = []
    for inc, obs_ids in members.items():
        eids = {pred.get(o) for o in obs_ids}
        if len(eids) != 1 or None in eids:
            continue
        exp = gt["expectations"].get(inc, {}).get("band")
        if not exp:
            continue
        got = events_by_id[next(iter(eids))].confidence_band
        checked += 1
        if got == exp:
            correct += 1
        else:
            mismatches.append({"incident": inc, "expected": exp, "got": got})
    return {"rate": correct / checked if checked else 1.0, "checked": checked, "mismatches": mismatches}


def echo_test(result, gt: dict) -> dict:
    """Echo groups must collapse to one event with exactly one independent family (Rumored)."""
    pred = predicted_partition(result)
    events_by_id = {e.event_id: e for e in result.events}
    failures = []
    for group in gt.get("echo_groups", []):
        eids = {pred.get(o) for o in group}
        if len(eids) != 1 or None in eids:
            failures.append({"group": group, "reason": "not single event", "events": list(eids)})
            continue
        e = events_by_id[next(iter(eids))]
        if e.n_independent_families != 1:
            failures.append({"group": group, "reason": f"families={e.n_independent_families}"})
        elif e.confidence_band != "Rumored":
            failures.append({"group": group, "reason": f"band={e.confidence_band}"})
    return {"ok": not failures, "failures": failures}


def must_not_merge_violations(result, gt: dict) -> list:
    """Pairs that ground truth says are different incidents but landed in one event."""
    pred = predicted_partition(result)
    part = gt["partition"]
    violations = []
    for e in result.events:
        for a, b in combinations(sorted(e.created_from_obs), 2):
            if part.get(a) != part.get(b):
                violations.append((a, b))
    return violations
