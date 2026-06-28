"""
The coarsening chokepoint for all ingest paths.

resolve_point / resolve_place / resolve_to_cell all return CellResolution,
which deliberately OMITS the precise input coordinate.
Every observation goes through one of these before being written to log.observation.
"""
from __future__ import annotations

import logging
from typing import Optional

from grid.admin_link import AdminResolver, NullAdminResolver
from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision

logger = logging.getLogger(__name__)


class GridResolver:
    """Resolve any GeoRef input to a 1km MGRS CellResolution.

    Precision config thresholds read from config/grid.yaml via the caller.
    """

    def __init__(
        self,
        conn,
        admin_resolver: AdminResolver | None = None,
        precise_threshold_m: float = 500.0,
        coarse_threshold_m: float = 5000.0,
    ):
        self._conn = conn
        self._admin = admin_resolver or NullAdminResolver()
        self._precise_m = precise_threshold_m
        self._coarse_m = coarse_threshold_m

    def resolve_point(
        self,
        lon: float,
        lat: float,
        precision_m: float = 0.0,
    ) -> Optional[CellResolution]:
        """Resolve a coordinate + precision radius to a cell.

        Returns None only if the point is genuinely outside all known cells.
        In that case the caller writes to obs_rejection (never silently drops).
        """
        try:
            cell_id = to_cell_id(lon, lat)
        except Exception as e:
            logger.warning("MGRS snap failed for (%.5f, %.5f): %s", lon, lat, e)
            return None

        cell = self._load_cell(cell_id)
        if cell is None:
            logger.warning("Cell %s not in grid_cell (outside AOI or grid not built)", cell_id)
            return None

        if precision_m <= self._precise_m:
            geo_prec = GeoPrecision.PRECISE
        else:
            geo_prec = GeoPrecision.COARSE

        return CellResolution(
            cell=cell,
            precision=geo_prec,
            non_precise=(geo_prec != GeoPrecision.PRECISE),
        )

    def resolve_place(
        self,
        place_name: str,
        theater_id: str,
    ) -> Optional[CellResolution]:
        """Resolve a place name (any language/transliteration) to a cell via place_alias.

        Place-name-only observations are NOT dropped — they get PLACE_ONLY precision
        and still land in log.observation, contributing to BLOCK via place_id.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT pa.cell_id, pa.place_id, pa.geom
                FROM geo.place_alias pa
                WHERE pa.theater_id = %s
                  AND pa.name ILIKE %s
                ORDER BY pa.is_preferred DESC
                LIMIT 1
                """,
                (theater_id, place_name.strip()),
            )
            row = cur.fetchone()

        if row is None:
            # Trigram fallback
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pa.cell_id, pa.place_id, pa.geom
                    FROM geo.place_alias pa
                    WHERE pa.theater_id = %s
                      AND similarity(pa.name, %s) > 0.4
                    ORDER BY similarity(pa.name, %s) DESC
                    LIMIT 1
                    """,
                    (theater_id, place_name, place_name),
                )
                row = cur.fetchone()

        if row is None:
            logger.warning("No place_alias match for %r in %s", place_name, theater_id)
            return None

        cell_id, place_id, geom = row
        if cell_id is None and geom is not None:
            # place has coords but no cell_id yet — snap it
            try:
                from shapely import wkb
                pt = wkb.loads(bytes(geom), hex=True)
                cell_id = to_cell_id(pt.x, pt.y)
            except Exception:
                return None

        cell = self._load_cell(cell_id)
        if cell is None:
            return None

        return CellResolution(
            cell=cell,
            precision=GeoPrecision.PLACE_ONLY,
            place_id=place_id,
            non_precise=True,
            flags=("place_name_only",),
        )

    def resolve_to_cell(
        self,
        lon: float | None,
        lat: float | None,
        precision_m: float = 0.0,
        place_name: str | None = None,
        theater_id: str = "ua_donbas",
    ) -> Optional[CellResolution]:
        """Unified entry point: try coordinate first, fall back to place name.

        Returns None → caller writes to obs_rejection (never a silent drop).
        """
        if lon is not None and lat is not None:
            result = self.resolve_point(lon, lat, precision_m)
            if result is not None:
                return result

        if place_name:
            return self.resolve_place(place_name, theater_id)

        return None

    def _load_cell(self, cell_id: str) -> Optional[Cell]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT cell_id, theater_id, label, admin_l1, admin_l2, admin_l3, local_seq
                FROM geo.grid_cell
                WHERE cell_id = %s
                """,
                (cell_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Cell(
            cell_id=row[0],
            theater_id=row[1],
            label=row[2] or row[0],
            admin_l1=row[3],
            admin_l2=row[4],
            admin_l3=row[5],
            local_seq=row[6],
        )
