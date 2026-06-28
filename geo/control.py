"""
DYNAMIC control overlay: control_status is append-only, as_of-stamped.
MVP: own-derived / empty stub only.

HARD LICENSE RULE (enforced by test in geo/tests/test_control_overlay.py):
  NEVER ingest ISW or DeepStateMap geometry. These sources have restrictive
  license terms incompatible with open-source redistribution.
  The control source whitelist is the only permitted sources list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Sources that MAY be ingested as control overlay — ISW/DSM are NEVER on this list
PERMITTED_SOURCES = frozenset({
    "own_derived",    # your own analysis
    "ua_mod_press",   # Ukrainian MoD press releases (attribution required)
})

FORBIDDEN_SOURCES = frozenset({
    "isw",            # Institute for the Study of War
    "deepstatemap",   # DeepStateMap
    "deepstate",
    "institute_for_the_study_of_war",
})


def assert_source_permitted(source: str) -> None:
    """Raise ValueError if source is forbidden. Called before any ingest."""
    lower = source.lower().replace(" ", "_")
    for forbidden in FORBIDDEN_SOURCES:
        if forbidden in lower:
            raise ValueError(
                f"FORBIDDEN: cannot ingest control overlay from source '{source}'. "
                "ISW and DeepStateMap geometry is license-incompatible. "
                "Only own-derived analysis is permitted."
            )


def write_control_status(
    conn,
    cell_id: str,
    theater_id: str,
    controller: str,
    confidence: float,
    as_of: datetime,
    source: str,
) -> None:
    """Append a control_status row. Raises ValueError on forbidden sources."""
    assert_source_permitted(source)
    if source not in PERMITTED_SOURCES:
        logger.warning(
            "Source '%s' not in PERMITTED_SOURCES — writing anyway but flagging for review",
            source,
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO geo.control_status
                (cell_id, theater_id, controller, confidence, as_of, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (cell_id, theater_id, controller, confidence, as_of, source),
        )
    conn.commit()


def latest_control(conn, cell_id: str) -> dict | None:
    """Return the most recent control_status row for a cell."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT controller, confidence, as_of, source
            FROM geo.control_status
            WHERE cell_id = %s
            ORDER BY as_of DESC
            LIMIT 1
            """,
            (cell_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"controller": row[0], "confidence": row[1], "as_of": row[2], "source": row[3]}
