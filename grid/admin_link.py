"""
Protocol + PostGIS implementation for resolving a point to its admin hierarchy.
Keeps grid/ decoupled from geo/ — resolver depends only on this protocol.
"""
from __future__ import annotations

from typing import Optional, Protocol


class AdminRecord:
    def __init__(self, l1: str | None, l2: str | None, l3: str | None, path: str | None):
        self.admin_l1 = l1
        self.admin_l2 = l2
        self.admin_l3 = l3
        self.admin_path = path


class AdminResolver(Protocol):
    def resolve(self, lon: float, lat: float) -> Optional[AdminRecord]:
        """Return admin record for the point, or None if outside coverage."""
        ...


class PostgisAdminResolver:
    """Point-in-polygon against geo.geo_feature(layer='admin') in PostGIS."""

    def __init__(self, conn):
        self._conn = conn

    def resolve(self, lon: float, lat: float) -> Optional[AdminRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    properties->>'admin_l1' AS l1,
                    properties->>'admin_l2' AS l2,
                    properties->>'admin_l3' AS l3,
                    properties->>'admin_path' AS path
                FROM geo.geo_feature
                WHERE layer = 'admin'
                  AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ORDER BY (properties->>'admin_level')::int DESC
                LIMIT 1
                """,
                (lon, lat),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return AdminRecord(row[0], row[1], row[2], row[3])


class NullAdminResolver:
    """Stub resolver for offline tests — returns None (no admin data)."""

    def resolve(self, lon: float, lat: float) -> Optional[AdminRecord]:
        return None
