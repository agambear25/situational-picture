"""
Idempotent numbered-SQL migration runner.
Runs migrations in filename order. Skips already-applied ones via a migrations table.
Usage: python -m db.migrations.run [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent
MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def get_conn(dsn: str | None = None):
    return psycopg2.connect(dsn or os.environ["DB_DSN"])


def ensure_migrations_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def applied(cur) -> set[str]:
    cur.execute("SELECT filename FROM public.schema_migrations")
    return {row[0] for row in cur.fetchall()}


def main(dry_run: bool = False):
    files = sorted(
        f for f in MIGRATIONS_DIR.iterdir()
        if MIGRATION_RE.match(f.name)
    )
    if not files:
        print("No migration files found.")
        return

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        ensure_migrations_table(cur)
        conn.commit()

        done = applied(cur)
        for f in files:
            if f.name in done:
                print(f"  skip  {f.name}")
                continue
            print(f"  apply {f.name}" + (" [dry-run]" if dry_run else ""))
            if not dry_run:
                sql = f.read_text()
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO public.schema_migrations(filename) VALUES (%s)",
                    (f.name,)
                )
                conn.commit()

        print("Done.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
