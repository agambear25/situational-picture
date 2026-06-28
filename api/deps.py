"""
API dependencies: read-only DB connection + merged settings.

The connection uses the cop_api role DSN (read-only on the read model; the engine write-path
is unreachable — see db/roles.sql). psycopg2 is imported lazily so api.coarsen and the pure
tests import this package without a DB driver present.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).parent.parent / "config"


@lru_cache(maxsize=1)
def get_settings() -> dict:
    """API + runtime settings, merged. config/api.yaml is canonical for bind/coarsening."""
    with open(_CFG / "api.yaml") as f:
        api = yaml.safe_load(f)["api"]
    with open(_CFG / "runtime.yaml") as f:
        runtime = yaml.safe_load(f)["runtime"]
    return {
        "host": api["host"],
        "port": int(api["port"]),
        "default_theater": api.get("default_theater", runtime.get("theater_id", "ua_donbas")),
        "enforce_coarsening": bool(api.get("enforce_coarsening", True)),
        "cors_origins": api.get("cors_origins", []),
        "default_page_size": int(api.get("default_page_size", 100)),
        "max_page_size": int(api.get("max_page_size", 1000)),
        "read_only_mode": bool(api.get("read_only_mode", True)),
        # read-only role DSN; env override wins (DB_DSN_API), then config, then a local default
        "db_dsn": os.environ.get("DB_DSN_API") or runtime.get("db_dsn_api")
        or "postgresql://cop_api:changeme@localhost:5432/osint_cop",
    }


def _connect():
    import psycopg2  # lazy
    conn = psycopg2.connect(get_settings()["db_dsn"])
    conn.set_session(readonly=True, autocommit=False)
    return conn


def get_conn():
    """FastAPI dependency: yields a read-only connection and always closes it."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def get_write_conn():
    """FastAPI dependency for the ONLY two write paths: append-only review/label annotations.

    Same cop_api role — which holds INSERT only on world.review_annotation and
    world.label_annotation and nothing else — so a writable session here still cannot touch
    the log or the read model, and the append-only triggers block UPDATE/DELETE regardless.
    """
    import psycopg2  # lazy
    conn = psycopg2.connect(get_settings()["db_dsn"])
    conn.set_session(readonly=False, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()
