"""Pure, dependency-light value objects for the 1km MGRS grid."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


CELL_ID_RE = re.compile(r"^[0-9]{1,2}[A-Z][A-Z]{2}[0-9]{10}$")


class GeoPrecision(str, Enum):
    PRECISE = "precise"        # ≤500m; fits within one cell
    COARSE = "coarse"          # ≤5km; snapped to nearest cell
    PLACE_ONLY = "place_only"  # name-resolved, no coordinate


@dataclass(frozen=True)
class Cell:
    cell_id: str                    # MGRS 1km e.g. '37UDB1234567890'
    theater_id: str
    label: str                      # human label e.g. 'Avdiivka-16'
    admin_l1: Optional[str] = None  # oblast
    admin_l2: Optional[str] = None  # raion
    admin_l3: Optional[str] = None  # hromada
    local_seq: Optional[int] = None

    def __post_init__(self):
        if not is_valid_cell_id(self.cell_id):
            raise ValueError(f"Invalid MGRS 1km cell_id: {self.cell_id!r}")


@dataclass(frozen=True)
class CellResolution:
    """Result of resolving any input to a 1km MGRS cell.

    Deliberately omits the precise input coordinate — the cell IS the reference.
    """
    cell: Cell
    precision: GeoPrecision
    place_id: Optional[int] = None   # resolved gazetteer id
    non_precise: bool = False        # True when precision != PRECISE
    flags: tuple[str, ...] = field(default_factory=tuple)


def is_valid_cell_id(cell_id: str) -> bool:
    return bool(CELL_ID_RE.match(cell_id))
