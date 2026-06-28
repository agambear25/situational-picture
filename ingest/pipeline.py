"""
Shared adapter plumbing: build the ingest context (DB + resolver + taxonomy + embedder + bus)
and run a batch of RawObservations through the contract's append-only ingest_one.

This is the ONLY place adapters touch psycopg2 / sentence-transformers / NATS — all lazy — so
each adapter's parse functions stay pure and offline-testable. Every raw item lands in
log.observation or log.obs_rejection: ingest_raws never drops one silently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grid.resolver import GridResolver
from ingest.bus import NatsBus, NullBus
from ingest.contract import RawObservation, ingest_one

logger = logging.getLogger(__name__)

_CFG = Path(__file__).parent.parent / "config"


@dataclass
class IngestContext:
    conn: object
    resolver: object
    taxonomy: dict
    embedder: object
    bus: object
    theater: dict


def _runtime() -> dict:
    return yaml.safe_load((_CFG / "runtime.yaml").read_text())["runtime"]


def _theater_cfg(theater_id: str) -> dict:
    theaters = yaml.safe_load((_CFG / "theaters.yaml").read_text())["theaters"]
    if theater_id not in theaters:
        raise KeyError(f"unknown theater_id {theater_id!r}")
    return theaters[theater_id]


def build_context(theater_id: str = "ua_donbas", *, use_embedder: bool = True) -> IngestContext:
    """Connect to the engine DB and assemble resolver/taxonomy/embedder/bus for an adapter run."""
    import os
    import psycopg2  # lazy

    rt = _runtime()
    dsn = os.environ.get("DB_DSN") or rt.get("db_dsn") or "postgresql://localhost:5432/osint_cop"
    conn = psycopg2.connect(dsn)

    taxonomy = yaml.safe_load((_CFG / "taxonomy.yaml").read_text())
    resolver = GridResolver(conn)

    embedder = None
    if use_embedder:
        from ingest.embedding import MiniLMEmbedder  # lazy/heavy
        embedder = MiniLMEmbedder()

    if rt.get("bus_enabled"):
        bus = NatsBus(rt.get("nats_servers", ["nats://localhost:4222"]),
                      rt.get("nats_stream", "cop_events"))
    else:
        bus = NullBus()

    return IngestContext(conn=conn, resolver=resolver, taxonomy=taxonomy,
                         embedder=embedder, bus=bus, theater=_theater_cfg(theater_id))


def ingest_raws(raws, ctx: IngestContext) -> dict:
    """Run RawObservations through the contract. Returns counters; nothing is dropped silently."""
    counters = {"seen": 0, "ingested": 0, "rejected": 0, "by_reason": {}}
    for raw in raws:
        if raw is None:
            continue
        counters["seen"] += 1
        obs_id, reason = ingest_one(
            raw, ctx.resolver, ctx.taxonomy, ctx.conn, embedder=ctx.embedder, bus=ctx.bus,
        )
        if obs_id is not None:
            counters["ingested"] += 1
        else:
            counters["rejected"] += 1
            counters["by_reason"][reason] = counters["by_reason"].get(reason, 0) + 1
    logger.info("ingest complete: %s", counters)
    return counters


def in_bbox(lon: float, lat: float, bbox) -> bool:
    """bbox = [west, south, east, north] (EPSG:4326)."""
    w, s, e, n = bbox
    return (w <= lon <= e) and (s <= lat <= n)
