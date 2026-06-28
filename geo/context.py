"""
The cell_context materializer: spatially join every static layer to each cell ONCE.
Deterministic, idempotent, replay-safe. No raster queried at event time — only key lookup.

Called by load_substrate.py after all layers are loaded.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_cell_context(conn, theater_id: str) -> int:
    """Finalize cell_context by joining admin labels from grid_cell and ensuring every
    cell has a context row (even if sparse).

    Returns number of cells with complete context.
    """
    with conn.cursor() as cur:
        # Ensure every grid cell has a cell_context row (some layers may have been skipped)
        cur.execute(
            """
            INSERT INTO geo.cell_context (cell_id, theater_id, updated_at)
            SELECT gc.cell_id, gc.theater_id, now()
            FROM geo.grid_cell gc
            WHERE gc.theater_id = %s
              AND NOT EXISTS (
                SELECT 1 FROM geo.cell_context cc WHERE cc.cell_id = gc.cell_id
              )
            """,
            (theater_id,)
        )

        # Denormalize admin labels from grid_cell into cell_context
        cur.execute(
            """
            UPDATE geo.cell_context cc
            SET
                admin_l1 = gc.admin_l1,
                admin_l2 = gc.admin_l2,
                admin_l3 = gc.admin_l3,
                label = gc.label,
                updated_at = now()
            FROM geo.grid_cell gc
            WHERE cc.cell_id = gc.cell_id
              AND gc.theater_id = %s
            """,
            (theater_id,)
        )

        conn.commit()

        cur.execute(
            "SELECT COUNT(*) FROM geo.cell_context WHERE theater_id = %s",
            (theater_id,)
        )
        n = cur.fetchone()[0]

    logger.info("cell_context: %d rows for theater %s", n, theater_id)
    return n


def cell_context_coverage(conn, theater_id: str) -> dict:
    """Return a coverage report — what fraction of cells have each field populated."""
    fields = [
        "mean_slope_deg", "dominant_landcover", "has_river",
        "has_bridge", "builtup_pct", "nearest_road_class",
    ]
    report = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM geo.cell_context WHERE theater_id = %s",
            (theater_id,)
        )
        total = cur.fetchone()[0]
        for field in fields:
            cur.execute(
                f"SELECT COUNT(*) FROM geo.cell_context WHERE theater_id = %s AND {field} IS NOT NULL",
                (theater_id,)
            )
            n = cur.fetchone()[0]
            report[field] = {"n": n, "pct": round(100 * n / total, 1) if total > 0 else 0}
    report["total_cells"] = total
    return report
