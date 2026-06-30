"""
Export a static snapshot of the dashboard for the GitHub Pages demo.

Hits the live read-only API and writes every view the UI requests to data/*.json files whose paths
match web/app.js's _staticFile() mapping. The published site (web/ + this data/) then runs fully
client-side with no backend — a clickable, never-breaks demo.

    .venv/bin/uvicorn api.main:app --port 8000 &        # the live API
    python scripts/export_static.py --out site           # writes site/data/*.json

Run scripts/build_demo.sh to assemble + publish the site.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
THEATER = "ua_donbas"


def get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read())


def write(out: Path, rel: str, data) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--event-details", type=int, default=500, help="how many event detail files to export")
    args = ap.parse_args()
    out = Path(args.out)
    n = 0

    # --- top-level singletons ---
    for rel, path in [
        ("data/healthz.json", "/healthz"),
        ("data/insights.json", f"/insights?theater_id={THEATER}"),
        ("data/control.json", "/control"),
        ("data/aois.json", f"/aois?theater_id={THEATER}"),
        ("data/verify-queue.json", f"/verify-queue?theater_id={THEATER}"),
        ("data/rejections.json", f"/rejections?theater_id={THEATER}"),
        ("data/events.json", f"/events?theater_id={THEATER}&limit=500"),
    ]:
        try:
            write(out, rel, get(path)); n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  skip {rel}: {e}")

    # --- region drill-down: level 1, then level 2 per oblast, then level 3 per active raion ---
    l1 = get(f"/rollup?level=1&theater_id={THEATER}")
    write(out, "data/rollup/l1.json", l1); n += 1
    for ob in l1.get("units", []):
        l2 = get(f"/rollup?level=2&parent={ob['admin_id']}&theater_id={THEATER}")
        write(out, f"data/rollup/l2-{ob['admin_id']}.json", l2); n += 1
        for ra in l2.get("units", []):
            if ra.get("n_events", 0) > 0:
                l3 = get(f"/rollup?level=3&parent={ra['admin_id']}&theater_id={THEATER}")
                write(out, f"data/rollup/l3-{ra['admin_id']}.json", l3); n += 1

    # --- geography feature layers ---
    for kind in ("water", "road", "forest", "builtup"):
        try:
            write(out, f"data/features/{kind}.json", get(f"/features?kind={kind}&theater_id={THEATER}")); n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  skip features/{kind}: {e}")

    # --- areas of interest: detail + their events ---
    for a in (get(f"/aois?theater_id={THEATER}").get("aois", [])):
        aid = a["aoi_id"]
        write(out, f"data/aoi/{aid}.json", get(f"/aois/{aid}")); n += 1
        write(out, f"data/events/aoi-{aid}.json", get(f"/events?theater_id={THEATER}&aoi={aid}&limit=300")); n += 1

    # --- per-event detail + per-cell history for the snapshot (so clicking works offline) ---
    events = get(f"/events?theater_id={THEATER}&limit={args.event_details}").get("events", [])
    cells = set()
    for e in events:
        eid = e["event_id"]
        try:
            write(out, f"data/event/{eid}.json", get(f"/events/{eid}")); n += 1
        except Exception:  # noqa: BLE001
            pass
        if e.get("cell_id"):
            cells.add(e["cell_id"])
    for cid in cells:
        try:
            write(out, f"data/cell/{cid}.json", get(f"/cells/{cid}")); n += 1
        except Exception:  # noqa: BLE001
            pass

    print(f"exported {n} snapshot files → {out}/data")


if __name__ == "__main__":
    main()
