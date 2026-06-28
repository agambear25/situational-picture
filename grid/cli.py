"""
Grid CLI — build the MGRS grid and verify Phase-0 gate.

Usage:
    python -m grid.cli build --theater ua_donbas
    python -m grid.cli verify --theater ua_donbas
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2
import yaml


def _load_theater(theater_id: str) -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "theaters.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    theaters = cfg.get("theaters", {})
    if theater_id not in theaters:
        print(f"ERROR: theater '{theater_id}' not in config/theaters.yaml", file=sys.stderr)
        sys.exit(1)
    return theaters[theater_id]


def cmd_build(args):
    from grid.builder import build_grid
    from grid.admin_link import PostgisAdminResolver, NullAdminResolver

    theater = _load_theater(args.theater)
    bbox = theater["bbox"]  # [west, south, east, north]

    dsn = os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop")
    conn = psycopg2.connect(dsn)

    try:
        resolver = PostgisAdminResolver(conn) if not args.no_admin else NullAdminResolver()
        n = build_grid(args.theater, tuple(bbox), conn, admin_resolver=resolver)
        print(f"Built {n} cells for theater '{args.theater}'")
    finally:
        conn.close()


def cmd_verify(args):
    """Phase-0 gate: assert every cell has cell_id, label, and a cell_context row."""
    dsn = os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop")
    conn = psycopg2.connect(dsn)
    failures = []

    with conn.cursor() as cur:
        # Check cells exist
        cur.execute(
            "SELECT COUNT(*) FROM geo.grid_cell WHERE theater_id = %s",
            (args.theater,)
        )
        n_cells = cur.fetchone()[0]
        if n_cells == 0:
            failures.append(f"No cells found for theater '{args.theater}' — run 'build' first")

        # Check all cells have a label
        cur.execute(
            "SELECT COUNT(*) FROM geo.grid_cell WHERE theater_id = %s AND label IS NULL",
            (args.theater,)
        )
        n_unlabeled = cur.fetchone()[0]
        if n_unlabeled > 0:
            failures.append(f"{n_unlabeled} cells missing label")

        # Check cell_context coverage
        cur.execute(
            """
            SELECT COUNT(*) FROM geo.grid_cell gc
            WHERE gc.theater_id = %s
              AND NOT EXISTS (
                SELECT 1 FROM geo.cell_context cc WHERE cc.cell_id = gc.cell_id
              )
            """,
            (args.theater,)
        )
        n_no_ctx = cur.fetchone()[0]
        if n_no_ctx > 0:
            failures.append(f"{n_no_ctx} cells missing cell_context — run load_substrate")

    conn.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        sys.exit(1)

    print(f"PASS: {n_cells} cells, all labeled, all have cell_context")


def main():
    parser = argparse.ArgumentParser(prog="python -m grid.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build MGRS grid for a theater")
    p_build.add_argument("--theater", default="ua_donbas")
    p_build.add_argument("--no-admin", action="store_true",
                         help="Skip admin resolver (for offline tests)")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify", help="Assert Phase-0 gate conditions")
    p_verify.add_argument("--theater", default="ua_donbas")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
