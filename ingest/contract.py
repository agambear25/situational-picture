"""
The Observation contract — the single spine every adapter and the fusion engine share.

Hard rules enforced here:
  - snap-and-coarsen: precise input coords are consumed by the resolver and DISCARDED;
    the Observation carries only a 1km cell_id (+ uncertainty radius), never lon/lat.
  - zero silent drops: any raw item that cannot be validated or placed produces a
    Rejection, never a None that vanishes.
  - exact-dup guard: content_hash is deterministic over (normalized text + cell + time bucket).

This module imports nothing heavy at module load (no psycopg2 / httpx / torch);
DB and embedding backends are injected, so fixtures and the eval harness import it freely.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeoRef:
    """A location as an adapter reports it — precise, pre-coarsening.

    Exactly one of (lon+lat) or place_name must be present. The resolver turns
    this into a cell_id and the precise coords are dropped at that boundary.
    """
    lon: Optional[float] = None
    lat: Optional[float] = None
    precision_m: float = 0.0
    place_name: Optional[str] = None

    def __post_init__(self):
        has_coord = self.lon is not None and self.lat is not None
        if not has_coord and not self.place_name:
            raise ValueError("GeoRef needs coordinates or a place_name")


@dataclass(frozen=True)
class RawObservation:
    """What an ingest adapter emits before normalization."""
    theater_id: str
    source_id: str
    source_family_id: str
    modality: str
    obs_type: str
    occurred_start: datetime
    occurred_end: datetime
    geo: GeoRef
    text: str = ""
    lang: Optional[str] = None
    self_conf: Optional[float] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """The immutable, coarsened observation. Mirrors log.observation.

    No precise coordinate is stored — `cell_id` is the location. `centroid_lonlat`
    is derived from the cell on demand by fusion, never persisted as the input coord.
    """
    obs_id: str
    theater_id: str
    source_id: str
    source_family_id: str
    modality: str
    obs_type: str
    occurred_start: datetime
    occurred_end: datetime
    cell_id: str
    geom_precision_m: float
    content_hash: str
    place_id: Optional[int] = None
    text: str = ""
    embedding: Optional[tuple[float, ...]] = None   # 384-d or None (eval runs trigram-only)
    lang: Optional[str] = None
    self_conf: Optional[float] = None
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_fixture(cls, d: dict) -> "Observation":
        """Build directly from a fixture dict (cell already assigned, no resolver)."""
        return cls(
            obs_id=d["obs_id"],
            theater_id=d.get("theater_id", "ua_donbas"),
            source_id=d["source_id"],
            source_family_id=d["source_family_id"],
            modality=d.get("modality", "text"),
            obs_type=d["obs_type"],
            occurred_start=_parse_dt(d["occurred_start"]),
            occurred_end=_parse_dt(d.get("occurred_end", d["occurred_start"])),
            cell_id=d["cell_id"],
            geom_precision_m=float(d.get("geom_precision_m", 1000.0)),
            content_hash=d.get("content_hash") or _content_hash(d.get("text", ""), d["cell_id"], _parse_dt(d["occurred_start"])),
            place_id=d.get("place_id"),
            text=d.get("text", ""),
            embedding=tuple(d["embedding"]) if d.get("embedding") else None,
            lang=d.get("lang"),
            self_conf=d.get("self_conf"),
            meta=d.get("meta", {}),
        )


@dataclass(frozen=True)
class Rejection:
    """A raw item that could not become an Observation. The opposite of a silent drop."""
    theater_id: str
    source_id: str
    raw_payload: dict
    reason: str   # 'no_cell_resolve' | 'invalid_type' | 'invalid_modality' | 'exact_dup' | 'invalid_geom'


# ---------------------------------------------------------------------------
# Injected backends (Protocols — keep this module dependency-light)
# ---------------------------------------------------------------------------

@runtime_checkable
class CellResolver(Protocol):
    def resolve_to_cell(
        self, lon: float | None, lat: float | None, precision_m: float,
        place_name: str | None, theater_id: str,
    ):  # -> CellResolution | None
        ...


@runtime_checkable
class Embedder(Protocol):
    def embed(self, text: str) -> tuple[float, ...]:
        ...
    @property
    def dim(self) -> int:
        ...


@runtime_checkable
class Bus(Protocol):
    def publish(self, subject: str, payload: dict) -> None:
        ...


# ---------------------------------------------------------------------------
# Normalization — the coarsening + validation chokepoint
# ---------------------------------------------------------------------------

def normalize(
    raw: RawObservation,
    resolver: CellResolver,
    taxonomy: dict,
    embedder: Optional[Embedder] = None,
    obs_id: Optional[str] = None,
) -> tuple[Optional[Observation], Optional[Rejection]]:
    """Validate + coarsen a RawObservation into an Observation, or a Rejection.

    Returns (Observation, None) on success or (None, Rejection) on any failure.
    Never raises for data problems — failures are surfaced as Rejections (no silent drop).
    """
    valid_modalities = set(taxonomy.get("modalities", []))
    valid_types = set(taxonomy.get("event_types", []))

    if raw.modality not in valid_modalities:
        return None, _reject(raw, "invalid_modality")
    if raw.obs_type not in valid_types:
        return None, _reject(raw, "invalid_type")

    res = resolver.resolve_to_cell(
        raw.geo.lon, raw.geo.lat, raw.geo.precision_m,
        raw.geo.place_name, raw.theater_id,
    )
    if res is None:
        return None, _reject(raw, "no_cell_resolve")

    cell_id = res.cell.cell_id
    place_id = getattr(res, "place_id", None)

    chash = _content_hash(raw.text, cell_id, raw.occurred_start)

    embedding = None
    if embedder is not None and raw.text.strip():
        vec = embedder.embed(raw.text)
        if len(vec) != embedder.dim:
            raise ValueError(
                f"Embedder returned dim {len(vec)}, expected {embedder.dim} — refusing (fail loud)"
            )
        embedding = tuple(vec)

    obs = Observation(
        obs_id=obs_id or _det_uuid(chash),
        theater_id=raw.theater_id,
        source_id=raw.source_id,
        source_family_id=raw.source_family_id,
        modality=raw.modality,
        obs_type=raw.obs_type,
        occurred_start=raw.occurred_start,
        occurred_end=raw.occurred_end,
        cell_id=cell_id,
        geom_precision_m=raw.geo.precision_m,
        content_hash=chash,
        place_id=place_id,
        text=_normalize_text(raw.text),
        embedding=embedding,
        lang=raw.lang,
        self_conf=raw.self_conf,
        meta=dict(raw.meta),
    )
    return obs, None


def observation_from_cell(
    *,
    cell_id: str,
    theater_id: str,
    source_id: str,
    source_family_id: str,
    modality: str,
    obs_type: str,
    occurred_start: datetime,
    occurred_end: datetime,
    text: str = "",
    self_conf: Optional[float] = None,
    meta: Optional[dict] = None,
    taxonomy: Optional[dict] = None,
    embedder: Optional[Embedder] = None,
    place_id: Optional[int] = None,
    geom_precision_m: float = 1000.0,
) -> Observation:
    """Build an Observation from an ALREADY-COARSENED detection — the cell_id is authoritative.

    This is the pre-coarsened entry point used by imagery detectors: the cell is fixed by the
    detection cache, and re-resolving a *centroid* would be unsafe at MGRS/UTM zone edges (a
    cell's centroid can re-snap into the adjacent zone). So we take the cell_id directly and
    reuse the SAME content_hash / embedding logic as normalize(); no resolver, no re-derivation.
    Validates the enums when a taxonomy is supplied (fail loud, never a silent bad type).
    """
    if taxonomy is not None:
        if modality not in set(taxonomy.get("modalities", [])):
            raise ValueError(f"invalid modality {modality!r}")
        if obs_type not in set(taxonomy.get("event_types", [])):
            raise ValueError(f"invalid obs_type {obs_type!r}")
    text = _normalize_text(text)
    chash = _content_hash(text, cell_id, occurred_start)
    embedding = None
    if embedder is not None and text.strip():
        vec = embedder.embed(text)
        if len(vec) != embedder.dim:
            raise ValueError(f"Embedder returned dim {len(vec)}, expected {embedder.dim} — fail loud")
        embedding = tuple(vec)
    return Observation(
        obs_id=_det_uuid(chash), theater_id=theater_id, source_id=source_id,
        source_family_id=source_family_id, modality=modality, obs_type=obs_type,
        occurred_start=occurred_start, occurred_end=occurred_end, cell_id=cell_id,
        geom_precision_m=geom_precision_m, content_hash=chash, place_id=place_id,
        text=text, embedding=embedding, self_conf=self_conf, meta=dict(meta or {}),
    )


def persist_observation(conn, obs: Observation, bus: Optional[Bus] = None) -> tuple[Optional[str], Optional[str]]:
    """Append-only write of an ALREADY-COARSENED Observation (the imagery-detector path).

    Imagery observations are built pre-coarsened via observation_from_cell (cell_id authoritative,
    no resolver), so they skip normalize() and enter the log here. Returns (obs_id, None) on
    insert or (None, 'exact_dup') on a content_hash conflict — never a silent drop.
    """
    inserted = _write_observation(conn, obs)
    if not inserted:
        return None, "exact_dup"
    if bus is not None:
        bus.publish("observation.ingested", {"obs_id": obs.obs_id, "cell_id": obs.cell_id})
    return obs.obs_id, None


def ingest_one(
    raw: RawObservation,
    resolver: CellResolver,
    taxonomy: dict,
    conn,
    embedder: Optional[Embedder] = None,
    bus: Optional[Bus] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Production path: normalize → append-only write → publish. Returns (obs_id, reject_reason).

    Lazily imports psycopg2-dependent writers so the pure contract stays import-light.
    On UNIQUE(content_hash) conflict, the row is an exact dup → logged as a rejection, not an error.
    """
    obs, rej = normalize(raw, resolver, taxonomy, embedder)
    if rej is not None:
        _write_rejection(conn, rej)
        return None, rej.reason

    inserted = _write_observation(conn, obs)
    if not inserted:
        _write_rejection(conn, _reject(raw, "exact_dup"))
        return None, "exact_dup"

    if bus is not None:
        bus.publish("observation.ingested", {"obs_id": obs.obs_id, "cell_id": obs.cell_id})
    return obs.obs_id, None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def _content_hash(text: str, cell_id: str, occurred_start: datetime) -> str:
    bucket = occurred_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")  # hour bucket
    norm = _normalize_text(text).lower()
    raw = f"{norm}|{cell_id}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _det_uuid(seed: str) -> str:
    """Deterministic obs_id from content_hash so replays are stable (no random uuid)."""
    h = hashlib.sha256(("obs:" + seed).encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _parse_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _reject(raw: RawObservation, reason: str) -> Rejection:
    return Rejection(
        theater_id=raw.theater_id,
        source_id=raw.source_id,
        raw_payload={
            "obs_type": raw.obs_type, "modality": raw.modality,
            "text": raw.text, "place_name": raw.geo.place_name,
            "lon": raw.geo.lon, "lat": raw.geo.lat,
        },
        reason=reason,
    )


def _write_observation(conn, obs: Observation) -> bool:
    """Append-only insert. Returns False if content_hash already present (exact dup)."""
    from psycopg2 import errors  # lazy
    emb = list(obs.embedding) if obs.embedding else None
    # Guard against an empty tstzrange: tstzrange(x, x, '[)') is EMPTY and reads back with
    # NULL bounds, silently losing the timestamp. An instantaneous observation must still
    # occupy a non-empty half-open interval — bump the end by 1s if it is not strictly after.
    occ_start = obs.occurred_start
    occ_end = obs.occurred_end
    if occ_end is None or occ_end <= occ_start:
        occ_end = occ_start + timedelta(seconds=1)
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO log.observation
                    (obs_id, theater_id, source_id, source_family_id, modality, obs_type,
                     occurred_at, cell_id, geom_precision_m, place_id, raw_text, embedding,
                     content_hash, lang, self_conf, meta)
                VALUES
                    (%s, %s, %s, %s, %s, %s,
                     tstzrange(%s, %s, '[)'), %s, %s, %s, %s, %s,
                     %s, %s, %s, %s::jsonb)
                ON CONFLICT (content_hash) DO NOTHING
                """,
                (
                    obs.obs_id, obs.theater_id, obs.source_id, obs.source_family_id,
                    obs.modality, obs.obs_type, occ_start, occ_end,
                    obs.cell_id, obs.geom_precision_m, obs.place_id, obs.text, emb,
                    obs.content_hash, obs.lang, obs.self_conf, _json(obs.meta),
                ),
            )
            inserted = cur.rowcount == 1
        except Exception:
            conn.rollback()
            raise
    conn.commit()
    return inserted


def _write_rejection(conn, rej: Rejection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO log.obs_rejection (theater_id, source_id, raw_payload, reason)
            VALUES (%s, %s, %s::jsonb, %s)
            """,
            (rej.theater_id, rej.source_id, _json(rej.raw_payload), rej.reason),
        )
    conn.commit()


def _json(d: dict) -> str:
    import json
    return json.dumps(d, sort_keys=True, default=str)
