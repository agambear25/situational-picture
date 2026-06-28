"""
UNOSAT ground-truth evaluation for the SAR change detector (Phase 3e/3c).

UNOSAT publishes building-level damage points for Ukraine (data/ground_truth/unosat_labels.geojson,
13.5k+ points inside the Donbas AOI). This module aligns those points to the same 1km MGRS grid the
detector uses, then scores the detector's change cells against them — cell-level precision / recall /
F1, broken out by damage grade and city. That turns "the SAR detector seems to work" into a number.

Pure + offline: the scoring functions take plain cell sets (no DB, no GEE), so they're unit-tested
with synthetic data. The CLI wires the real UNOSAT file + the detector's cells (from the live DB or a
provided file) and prints the report.

Damage grade scale (UNOSAT CE…UKR_UNOSAT_Damage; higher = worse). Grades 2/3/4 are the "real damage"
a change detector should catch; grade 1 is slight/possible and 5–7 are other/uncertain categories.
The exact 5–7 codebook is confirmed against the UNOSAT product metadata before publishing results.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

from grid.mgrs_1km import to_cell_id
from fusion.geo import cell_distance_m

DAMAGE_LABELS = {
    1: "Slight / possible",
    2: "Moderate damage",
    3: "Severe damage",
    4: "Destroyed",
    5: "Possible damage",
    6: "Not applicable",
    7: "Other / uncertain",
}
# The classes a structure-scale change detector is expected to detect.
REAL_DAMAGE_GRADES = (2, 3, 4)

_GROUND_TRUTH = Path(__file__).resolve().parents[1] / "data" / "ground_truth" / "unosat_labels.geojson"
_THEATERS = Path(__file__).resolve().parents[1] / "config" / "theaters.yaml"


# --------------------------------------------------------------------------- load + align

def load_unosat_features(path: str | Path = _GROUND_TRUTH) -> list[dict]:
    """Load the UNOSAT label GeoJSON features (each a damage point with grade/city/date)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("features", [])


def _aoi_bbox(theater_id: str) -> tuple[float, float, float, float]:
    import yaml
    cfg = yaml.safe_load(_THEATERS.read_text(encoding="utf-8"))
    w, s, e, n = cfg["theaters"][theater_id]["bbox"]
    return float(w), float(s), float(e), float(n)


def truth_cells(
    features: Iterable[dict],
    bbox: tuple[float, float, float, float],
    grades: Iterable[int] = REAL_DAMAGE_GRADES,
) -> dict[str, dict]:
    """Aggregate UNOSAT damage points into 1km ground-truth cells.

    Returns {cell_id: {worst_grade, n_points, cities, grades}} for every cell that contains at
    least one damage point of an included grade inside the bbox. The cell is the unit of truth
    because the detector also resolves to 1km cells.
    """
    w, s, e, n = bbox
    grades = set(grades)
    cells: dict[str, dict] = {}
    for f in features:
        props = f.get("properties", {})
        grade = props.get("damage")
        if grade not in grades:
            continue
        coords = (f.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not (w <= lon <= e and s <= lat <= n):
            continue
        cid = to_cell_id(lon, lat)
        info = cells.setdefault(cid, {"worst_grade": 0, "n_points": 0, "cities": set(), "grades": Counter()})
        info["worst_grade"] = max(info["worst_grade"], grade)
        info["n_points"] += 1
        if props.get("city"):
            info["cities"].add(props["city"])
        info["grades"][grade] += 1
    return cells


# --------------------------------------------------------------------------- scoring

def score(
    detected_cells: Iterable[str],
    truth: dict[str, dict],
    buffer_m: float = 0.0,
) -> dict:
    """Cell-level precision / recall / F1 of detector cells vs UNOSAT truth cells.

    buffer_m = 0 (default) requires an exact 1km-cell match. A positive buffer counts a detection
    as a hit if it lies within buffer_m of a truth-cell centroid (tolerance for edge cases / minor
    misregistration); matching is many-to-many so one detection can cover several adjacent truth
    cells. Returns counts + the matched/missed sets so callers can drill in.
    """
    detected = set(detected_cells)
    truth_set = set(truth)
    if not detected and not truth_set:
        return _metrics(set(), set(), detected, truth_set)

    if buffer_m <= 0:
        matched_det = detected & truth_set
        matched_truth = detected & truth_set
    else:
        matched_det, matched_truth = set(), set()
        for d in detected:
            if d in truth_set:
                matched_det.add(d)
                matched_truth.add(d)
                continue
            hit = False
            for t in truth_set:
                if cell_distance_m(d, t) <= buffer_m:
                    matched_truth.add(t)
                    hit = True
            if hit:
                matched_det.add(d)
    return _metrics(matched_det, matched_truth, detected, truth_set)


def _metrics(matched_det: set, matched_truth: set, detected: set, truth_set: set) -> dict:
    tp_p = len(matched_det)                 # detections that hit truth
    precision = tp_p / len(detected) if detected else 0.0
    recall = len(matched_truth) / len(truth_set) if truth_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "n_detected": len(detected),
        "n_truth": len(truth_set),
        "true_positives": tp_p,
        "false_positives": len(detected - matched_det),
        "missed_truth": sorted(truth_set - matched_truth),
    }


def recall_by_grade(detected_cells: Iterable[str], truth: dict[str, dict], buffer_m: float = 0.0) -> dict:
    """Recall computed separately for each damage grade (catching 'Destroyed' matters most)."""
    detected = set(detected_cells)
    by_grade: dict[int, dict] = {}
    grades = {g for info in truth.values() for g in info["grades"]}
    for g in sorted(grades):
        sub = {cid: info for cid, info in truth.items() if g in info["grades"]}
        r = score(detected, sub, buffer_m)
        by_grade[g] = {"label": DAMAGE_LABELS.get(g, str(g)), "recall": r["recall"], "n_truth": r["n_truth"]}
    return by_grade


def recall_by_city(detected_cells: Iterable[str], truth: dict[str, dict], buffer_m: float = 0.0) -> dict:
    detected = set(detected_cells)
    by_city: dict[str, dict] = defaultdict(lambda: {"truth": set()})
    for cid, info in truth.items():
        for city in (info["cities"] or {"(unknown)"}):
            by_city[city]["truth"].add(cid)
    out = {}
    for city, d in by_city.items():
        r = score(detected, {c: truth[c] for c in d["truth"]}, buffer_m)
        out[city] = {"recall": r["recall"], "n_truth": r["n_truth"]}
    return out


def format_report(overall: dict, by_grade: dict, by_city: dict, buffer_m: float) -> str:
    lines = ["=" * 60, "SAR DETECTOR vs UNOSAT GROUND TRUTH", "=" * 60,
             f"  match tolerance     : {int(buffer_m)} m ({'exact cell' if buffer_m <= 0 else 'with buffer'})",
             f"  detector cells      : {overall['n_detected']}",
             f"  UNOSAT damage cells : {overall['n_truth']}",
             f"  precision           : {overall['precision']:.1%}  ({overall['true_positives']}/{overall['n_detected']} hit real damage)",
             f"  recall              : {overall['recall']:.1%}  ({overall['true_positives']}/{overall['n_truth']} damage cells found)",
             f"  F1                  : {overall['f1']:.1%}",
             "-" * 60, "  recall by damage grade:"]
    for g, d in by_grade.items():
        lines.append(f"    {g} {d['label']:<18} {d['recall']:.1%}  (of {d['n_truth']} cells)")
    lines.append("  recall by city:")
    for city, d in sorted(by_city.items(), key=lambda kv: -kv[1]["n_truth"]):
        lines.append(f"    {str(city):<18} {d['recall']:.1%}  (of {d['n_truth']} cells)")
    lines.append("=" * 60)
    return "\n".join(lines)


# --------------------------------------------------------------------------- detector cells (live)

def detection_cells_from_db(conn, theater_id: str, families=("copernicus_sar",)) -> set[str]:
    """Pull the cells where the SAR detector placed an imagery observation (live DB)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT cell_id FROM log.observation "
            "WHERE theater_id = %s AND modality = 'imagery' AND source_family_id = ANY(%s)",
            (theater_id, list(families)),
        )
        return {r[0] for r in cur.fetchall()}


def _detection_cells_from_file(path: str) -> set[str]:
    """A JSON list of cell_ids (offline / testing, or a hand-exported detection set)."""
    return set(json.loads(Path(path).read_text(encoding="utf-8")))


def main():
    import argparse
    import os

    p = argparse.ArgumentParser(prog="python -m eval.unosat",
                                description="Score the SAR change detector against UNOSAT ground truth.")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--labels", default=str(_GROUND_TRUTH), help="UNOSAT labels GeoJSON")
    p.add_argument("--detections-file", help="JSON list of detector cell_ids (instead of the DB)")
    p.add_argument("--buffer-m", type=float, default=0.0, help="match tolerance in metres (0 = exact cell)")
    p.add_argument("--grades", default="2,3,4", help="UNOSAT damage grades to count as truth")
    args = p.parse_args()

    bbox = _aoi_bbox(args.theater)
    grades = tuple(int(g) for g in args.grades.split(","))
    truth = truth_cells(load_unosat_features(args.labels), bbox, grades)

    if args.detections_file:
        detected = _detection_cells_from_file(args.detections_file)
    else:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
        try:
            detected = detection_cells_from_db(conn, args.theater)
        finally:
            conn.close()

    overall = score(detected, truth, args.buffer_m)
    by_grade = recall_by_grade(detected, truth, args.buffer_m)
    by_city = recall_by_city(detected, truth, args.buffer_m)
    print(format_report(overall, by_grade, by_city, args.buffer_m))


if __name__ == "__main__":
    main()
