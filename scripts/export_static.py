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


def get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read())


def write(out: Path, rel: str, data) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def export_theater(out: Path, t: str, event_details: int, cells: set) -> int:
    """Export one theater's views under data/<t>/… ; per-event/cell detail go global (by-id)."""
    b = f"data/{t}"
    n = 0
    for rel, path in [
        (f"{b}/watch.json", f"/watch?theater_id={t}"),
        (f"{b}/insights.json", f"/insights?theater_id={t}"),
        (f"{b}/control.json", f"/control?theater_id={t}"),
        (f"{b}/aois.json", f"/aois?theater_id={t}"),
        (f"{b}/verify-queue.json", f"/verify-queue?theater_id={t}"),
        (f"{b}/rejections.json", f"/rejections?theater_id={t}"),
        (f"{b}/events.json", f"/events?theater_id={t}&limit=500"),
    ]:
        try:
            write(out, rel, get(path)); n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  skip {rel}: {e}")

    l1 = get(f"/rollup?level=1&theater_id={t}")
    write(out, f"{b}/rollup/l1.json", l1); n += 1
    for ob in l1.get("units", []):
        l2 = get(f"/rollup?level=2&parent={ob['admin_id']}&theater_id={t}")
        write(out, f"{b}/rollup/l2-{ob['admin_id']}.json", l2); n += 1
        for ra in l2.get("units", []):
            if ra.get("n_events", 0) > 0:
                write(out, f"{b}/rollup/l3-{ra['admin_id']}.json",
                      get(f"/rollup?level=3&parent={ra['admin_id']}&theater_id={t}")); n += 1

    for kind in ("water", "road", "forest", "builtup"):
        try:
            write(out, f"{b}/features/{kind}.json", get(f"/features?kind={kind}&theater_id={t}")); n += 1
        except Exception:  # noqa: BLE001
            pass

    for a in get(f"/aois?theater_id={t}").get("aois", []):
        aid = a["aoi_id"]
        write(out, f"data/aoi/{aid}.json", get(f"/aois/{aid}")); n += 1
        write(out, f"data/aoi/{aid}-read.json", get(f"/aois/{aid}/read")); n += 1
        write(out, f"{b}/events/aoi-{aid}.json", get(f"/events?theater_id={t}&aoi={aid}&limit=300")); n += 1

    for e in get(f"/events?theater_id={t}&limit={event_details}").get("events", []):
        try:
            write(out, f"data/event/{e['event_id']}.json", get(f"/events/{e['event_id']}")); n += 1
        except Exception:  # noqa: BLE001
            pass
        if e.get("cell_id"):
            cells.add(e["cell_id"])
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--event-details", type=int, default=400)
    args = ap.parse_args()
    out = Path(args.out)

    write(out, "data/healthz.json", get("/healthz"))
    theaters = get("/theaters")
    write(out, "data/theaters.json", theaters)
    n, cells = 2, set()
    for t in theaters.get("theaters", []):
        print(f"  theater {t['theater_id']} ({t['n_events']} events) …")
        n += export_theater(out, t["theater_id"], args.event_details, cells)
    for cid in cells:
        try:
            write(out, f"data/cell/{cid}.json", get(f"/cells/{cid}")); n += 1
        except Exception:  # noqa: BLE001
            pass
    print(f"exported {n} snapshot files → {out}/data  ({len(theaters.get('theaters', []))} theaters)")


if __name__ == "__main__":
    main()
