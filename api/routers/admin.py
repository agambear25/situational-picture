"""Admin/ops router: health, the no-drop rejection ledger, replay audit, and an insights stub.

These endpoints are the operator-facing proof surface for the COP's integrity claims:
- /healthz answers WITHOUT a DB dependency so liveness probes still report when the DB is down,
  and it surfaces the live-feed GATE flag (config/runtime.yaml) that keeps feeds off until eval passes.
- /rejections exposes the no-drop ledger: every observation that did NOT become an event is
  accounted for with a reason, so "nothing was silently swallowed" is verifiable, not asserted.
- /admin/replay runs a READ-ONLY determinism + no-drop audit; it re-derives events in memory and
  compares digests. It never rebuilds or writes — read-only role only.
- /insights is an honest stub: the assessment layer is Phase 4 and we say so rather than faking it.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Query

from api.deps import get_conn, get_settings
from api import queries

router = APIRouter()

# runtime.yaml holds the live-feed GATE flag. Resolve relative to the repo root
# (api/routers/admin.py -> parents[2]) so the path is stable regardless of CWD.
_RUNTIME_YAML = Path(__file__).resolve().parents[2] / "config" / "runtime.yaml"


def _load_runtime() -> dict:
    """Read config/runtime.yaml live. Read fresh per request (not cached) so flipping the GATE
    flag is reflected without a process restart; tolerate a missing/empty file by returning {}."""
    if not _RUNTIME_YAML.exists():
        return {}
    with _RUNTIME_YAML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # runtime.yaml nests everything under a top-level "runtime:" key.
    return data.get("runtime", {})


@router.get("/healthz")
def healthz() -> dict:
    """Liveness + posture probe. NO DB dependency on purpose: a DB outage must still return a
    response so operators can distinguish "API down" from "DB down", and so the GATE flag and
    read-only posture remain inspectable while degraded."""
    s = get_settings()
    runtime = _load_runtime()
    return {
        "status": "ok",
        "read_only": s["read_only_mode"],
        "live_feeds_enabled": bool(runtime.get("live_feeds_enabled", False)),
        "theater": s["default_theater"],
    }


@router.get("/rejections")
def rejections(
    theater_id: str | None = Query(default=None),
    limit: int = Query(default=200),
    conn=Depends(get_conn),
) -> dict:
    """The no-drop ledger: a summary tally plus the per-row reasons for observations that were
    not placed into events. This is the auditable proof that non-placed obs are accounted for."""
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    # Cap the page size to the configured ceiling rather than trusting client input.
    limit = min(limit, s["max_page_size"])
    return {
        "summary": queries.rejection_summary(conn, theater_id),
        "rejections": queries.list_rejections(conn, theater_id, limit),
    }


@router.get("/insights")
def insights(theater_id: str | None = Query(default=None), conn=Depends(get_conn)) -> dict:
    """Phase-4a assessments: the ranked 'what matters' feed (significance) + per-cell anomalies.

    Reads world.assessment (materialised by assess.run). Each item carries a plain rationale and
    a cell-only centroid + place label — no precise coordinate leaves the API. Returns
    available=False (not a fake score) when the assessment table is empty for the theater.
    """
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    from api.coarsen import cell_geometry
    from api.places import nearest_place

    def _locate(cell_id: str) -> dict:
        try:
            centroid = cell_geometry(cell_id, "cell_centroid")
            lon, lat = centroid["coordinates"]
            return {"centroid": centroid, "place": nearest_place(lon, lat, theater_id)}
        except Exception:  # noqa: BLE001 — a missing place label must never break /insights
            return {"centroid": None, "place": None}

    significant = queries.top_significant(conn, theater_id, limit=25)
    anomalies = queries.anomaly_assessments(conn, theater_id, limit=20)
    exposure = queries.event_assessments(conn, theater_id, "exposure", limit=25)
    gaps = queries.event_assessments(conn, theater_id, "gaps", limit=25)
    for item in (*significant, *anomalies, *exposure, *gaps):
        item.update(_locate(item["cell_id"]))

    return {
        "available": bool(significant or anomalies or exposure or gaps),
        "significant": significant,
        "anomalies": anomalies,
        "exposure": exposure,
        "gaps": gaps,
    }


@router.get("/theaters")
def theaters(conn=Depends(get_conn)) -> dict:
    """Theaters that actually have data, with label + bbox (so the UI can switch + re-center)."""
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "theaters.yaml")
                         .read_text(encoding="utf-8"))["theaters"]
    with conn.cursor() as cur:
        cur.execute("SELECT theater_id, count(*) FROM world.event GROUP BY 1")
        counts = {r[0]: r[1] for r in cur.fetchall()}
    out = [{"theater_id": tid, "label": t.get("label", tid), "bbox": t.get("bbox"),
            "n_events": counts.get(tid, 0)}
           for tid, t in cfg.items() if counts.get(tid, 0) > 0]
    out.sort(key=lambda x: -x["n_events"])
    return {"theaters": out}


@router.post("/admin/replay")
def admin_replay(
    theater_id: str | None = Query(default=None),
    conn=Depends(get_conn),
) -> dict:
    """READ-ONLY determinism + no-drop audit. Re-derives events from observations in memory and
    compares against materialized state; reports bit_identical and any dropped obs. It does NOT
    rebuild or write — hence get_conn (read-only role), despite being a POST."""
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    return queries.replay_check(conn, theater_id)
