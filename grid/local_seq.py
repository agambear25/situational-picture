"""
Deterministic local sequence numbers within an admin subdivision.
e.g. 'Avdiivka-16' — seq is stable across replays as long as cell enumeration order is stable.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def assign_local_seqs(
    cells: Iterable[dict],          # dicts with 'cell_id' and 'admin_l3' (hromada)
    admin_key: str = "admin_l3",
) -> dict[str, int]:
    """Return {cell_id: local_seq} — deterministic numbering within each admin unit.

    Cells are sorted by cell_id (MGRS is lexicographically stable) before numbering,
    so the mapping is replay-safe regardless of iteration order.
    """
    by_admin: dict[str, list[str]] = defaultdict(list)
    for cell in cells:
        admin = cell.get(admin_key) or "_unassigned"
        by_admin[admin].append(cell["cell_id"])

    result: dict[str, int] = {}
    for admin_cells in by_admin.values():
        for seq, cell_id in enumerate(sorted(admin_cells), start=1):
            result[cell_id] = seq
    return result


def build_label(admin_name: str | None, seq: int) -> str:
    """Format the human label e.g. 'Avdiivka-16'."""
    if not admin_name:
        return f"Unknown-{seq}"
    # Sanitize: strip extra whitespace, title-case
    name = " ".join(admin_name.strip().split())
    return f"{name}-{seq}"
