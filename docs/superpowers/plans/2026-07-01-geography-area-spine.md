# Geography Substrate + Area-Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Light up the dormant land-cover/road substrate and weave it into scoring + event text, then unify AOIs and admin units behind one `/area/{ref}` API with a recency-forward payload, surfaced in the By-area tab.

**Architecture:** Phase A populates `geo.cell_context` (WorldCover land cover, OSM roads incl. unpaved, built-up %) which activates the already-wired land-cover plausibility gate and upgrades exposure; Phase B generalizes context-gathering to an `admin:`/`aoi:` area-ref and splits each area's payload into *recent* vs *significant* lists with re-tuned recency.

**Tech Stack:** Python 3.11, FastAPI, psycopg2, PostGIS, rasterio (WorldCover), pyosmium (OSM), numpy, pytest. Vanilla-JS + Leaflet frontend. Local Postgres/PostGIS, GEE not required for this pass.

## Global Constraints

- **$0 / local / Claude-OFF** — no paid APIs, no Claude API; local data only. (verbatim project invariant)
- **No precise coords past the API boundary** — everything is cell-level (1km MGRS). Land-cover context is a property of a *cell*, never a precise point.
- **No person entities** — CHECK-enforced; unchanged here.
- **Determinism contract** — pure detectors/scorers replay bit-identical; `python -m eval.harness` must exit 0. Any change to fusion scoring requires re-freezing fixtures and re-proving the gate.
- **`.env` holds secrets (FIRMS_MAP_KEY, DB_DSN) — never commit; never echo to chat.**
- **Run tests with the project venv:** `source .venv/bin/activate` (NOT conda base, which lacks deps). Activate `.env` for DB tasks: `set -a && source .env && set +a`.
- **Theaters in scope:** `ua_donbas` (71,110 cells) and `black_sea` (91,416 cells).

---

## File Structure

- `config/layer_sources.yaml` — add black_sea WorldCover tile list (modify).
- `scripts/fetch_geography.sh` — reproducible WorldCover + Geofabrik download (create).
- `geo/layers/transport.py` — read OSM `surface` tag → `road_surface` (modify).
- `db/migrations/0014_road_surface.sql` — add `geo.cell_context.road_surface` column (create).
- `geo/tests/test_transport_surface.py` — surface-tag handler test (create).
- `geo/terrain.py` — pure land-cover/road aggregation for a cell set (create).
- `geo/tests/test_terrain.py` — aggregation test (create).
- `assess/exposure.py` — use real `builtup_pct` when present (modify).
- `assess/tests/test_exposure_builtup.py` — built-up vs proxy test (create).
- `fusion/tests/test_landcover_penalty.py` — plausibility truth-table test (create).
- `api/coarsen.py` — attach land-cover/road phrase to event text/meta (modify).
- `api/tests/test_geo_context_phrase.py` — phrase builder test (create).
- `api/queries.py` — `_resolve_area`, `gather_area_context_ref`, `area_payload`, terrain join (modify).
- `api/routers/areas.py` — `GET /area/{ref}`, `GET /area/{ref}/read` (modify).
- `config/assessment.yaml` — recency floor/tau + `recent_window_days` (modify).
- `web/app.js`, `web/styles.css` — place panel inside the By-area tab (modify).

---

## Phase A — Geography substrate + contextualization

### Task A1: black_sea WorldCover tiles + fetch script

**Files:**
- Modify: `config/layer_sources.yaml` (landcover.tiles)
- Create: `scripts/fetch_geography.sh`

**Interfaces:**
- Produces: WorldCover GeoTIFFs at `data/ground_truth/worldcover/<TILE>.tif`; `data/ground_truth/osm/ukraine-latest.osm.pbf`. Tile list `landcover.tiles.black_sea`.

- [ ] **Step 1: Add black_sea tiles to config**

In `config/layer_sources.yaml` under `landcover.tiles`, add:
```yaml
      black_sea: ["N42E033", "N42E036", "N45E033", "N45E036"]
```

- [ ] **Step 2: Write the fetch script**

Create `scripts/fetch_geography.sh`:
```bash
#!/usr/bin/env bash
# Download ESA WorldCover 10m tiles + the Geofabrik Ukraine OSM extract for the geography substrate.
# Data → data/ground_truth/{worldcover,osm}/ (gitignored). Reproducible; skips files already present.
set -euo pipefail
WC_DIR="data/ground_truth/worldcover"; OSM_DIR="data/ground_truth/osm"
mkdir -p "$WC_DIR" "$OSM_DIR"
BASE="https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
TILES="N45E034 N45E037 N48E034 N48E037 N42E033 N42E036 N45E033 N45E036"
for t in $TILES; do
  f="$WC_DIR/ESA_WorldCover_10m_2021_v200_${t}_Map.tif"
  if [ -s "$f" ]; then echo "have $t"; continue; fi
  echo "fetch $t"; curl -fsSL "$BASE/ESA_WorldCover_10m_2021_v200_${t}_Map.tif" -o "$f" \
    || { echo "  (tile $t absent — likely all-ocean, skipping)"; rm -f "$f"; }
done
PBF="$OSM_DIR/ukraine-latest.osm.pbf"
[ -s "$PBF" ] || curl -fSL "https://download.geofabrik.de/europe/ukraine-latest.osm.pbf" -o "$PBF"
echo "geography data ready in $WC_DIR + $OSM_DIR"
```

- [ ] **Step 3: Run it**

Run: `chmod +x scripts/fetch_geography.sh && bash scripts/fetch_geography.sh`
Expected: WorldCover tiles + ~1.6GB ukraine PBF land under `data/ground_truth/`. (PBF download is large; allow time.)

- [ ] **Step 4: Verify + confirm gitignore**

Run: `ls -la data/ground_truth/worldcover/ && du -h data/ground_truth/osm/*.pbf && git check-ignore data/ground_truth/osm/ukraine-latest.osm.pbf`
Expected: ≥6 tiles present; PBF listed; `git check-ignore` echoes the path (already ignored). If not ignored, add `data/ground_truth/` to `.gitignore`.

- [ ] **Step 5: Commit (config + script only, NOT data)**

```bash
git add config/layer_sources.yaml scripts/fetch_geography.sh
git commit -m "geo: black_sea WorldCover tiles + reproducible fetch_geography.sh"
```

---

### Task A2: OSM `surface` tag → road_surface (paved/unpaved/dirt)

**Files:**
- Create: `db/migrations/0014_road_surface.sql`
- Modify: `geo/layers/transport.py`
- Test: `geo/tests/test_transport_surface.py`

**Interfaces:**
- Consumes: `TransportHandler` (existing).
- Produces: `geo.cell_context.road_surface TEXT` ('paved'|'unpaved'|'unknown'); each road dict gains `surface`. `load_transport` writes `road_surface` alongside `nearest_road_class`.

- [ ] **Step 1: Write the migration**

Create `db/migrations/0014_road_surface.sql`:
```sql
-- Coarse paving of the cell's dominant road: 'paved' | 'unpaved' | 'unknown'.
-- 'unpaved' = OSM surface in (unpaved,dirt,ground,gravel,compacted,fine_gravel,earth,mud,sand)
--             OR highway in (track, path) with no explicit paved surface — the "dirt road" proxy.
ALTER TABLE geo.cell_context ADD COLUMN IF NOT EXISTS road_surface TEXT;
```

- [ ] **Step 2: Write the failing test**

Create `geo/tests/test_transport_surface.py`:
```python
from geo.layers.transport import classify_surface

def test_explicit_unpaved():
    assert classify_surface("primary", "dirt") == "unpaved"
    assert classify_surface("secondary", "gravel") == "unpaved"

def test_explicit_paved():
    assert classify_surface("primary", "asphalt") == "paved"
    assert classify_surface("residential", "paved") == "paved"

def test_track_is_unpaved_by_default():
    assert classify_surface("track", None) == "unpaved"
    assert classify_surface("path", None) == "unpaved"

def test_major_road_unknown_surface_defaults_paved():
    assert classify_surface("motorway", None) == "paved"
    assert classify_surface("primary", None) == "paved"

def test_unknown_minor():
    assert classify_surface("unclassified", None) == "unknown"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest geo/tests/test_transport_surface.py -q`
Expected: FAIL — `cannot import name 'classify_surface'`.

- [ ] **Step 4: Implement classify_surface + capture the tag**

In `geo/layers/transport.py`, add the pure helper near `ROAD_CLASS_RANK`:
```python
_UNPAVED = {"unpaved", "dirt", "ground", "gravel", "compacted", "fine_gravel", "earth", "mud", "sand"}
_PAVED = {"paved", "asphalt", "concrete", "paving_stones", "sett", "cobblestone"}
_PAVED_BY_DEFAULT = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential"}

def classify_surface(highway: str, surface: str | None) -> str:
    if surface:
        if surface in _UNPAVED:
            return "unpaved"
        if surface in _PAVED:
            return "paved"
    if highway in ("track", "path"):
        return "unpaved"
    if highway in _PAVED_BY_DEFAULT:
        return "paved"
    return "unknown"
```
In `TransportHandler.way`, capture the surface and store it:
```python
        surface = w.tags.get("surface")
        ...
            self.roads.append({"geom": geom, "road_class": highway, "bridge": is_bridge,
                               "surface": classify_surface(highway, surface)})
```
In `load_transport`, after `nearest_class` is chosen, derive the surface of that top-ranked road and write it:
```python
            nearest_surface = ranked[0]["surface"] if roads_in_cell else None
```
and extend the INSERT column list + ON CONFLICT to include `road_surface = EXCLUDED.road_surface` (add `nearest_surface` to the params tuple).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest geo/tests/test_transport_surface.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Apply the migration**

Run: `set -a && source .env && set +a && psql "$DB_DSN" -f db/migrations/0014_road_surface.sql`
Expected: `ALTER TABLE`.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0014_road_surface.sql geo/layers/transport.py geo/tests/test_transport_surface.py
git commit -m "geo: classify road surface (paved/unpaved/dirt) from OSM surface tag"
```

---

### Task A3: Populate the substrate (land cover + roads + built-up %)

**Files:**
- Modify: `geo/layers/landcover.py` (add `builtup_pct` write)

**Interfaces:**
- Consumes: WorldCover tiles (A1), `load_landcover`, `load_transport`.
- Produces: populated `geo.cell_context.{dominant_landcover, landcover_label, builtup_pct, nearest_road_class, road_surface, has_bridge}` for both theaters.

- [ ] **Step 1: Add builtup_pct to the landcover pass**

In `geo/layers/landcover.py` `_dominant_class`, also compute the class-50 fraction; extend `load_landcover` to write `builtup_pct = (count of class 50) / (valid pixel count)` into the same UPSERT (add the column to the INSERT + ON CONFLICT). Keep it one raster read per cell. (If `_dominant_class` returns only the label, add a sibling `_class_fractions(datasets, poly) -> dict[int,float]` and use `.get(50, 0.0)` for builtup_pct and `max(...)` for dominant.)

- [ ] **Step 2: Run the populate for ua_donbas**

Run:
```bash
set -a && source .env && set +a && source .venv/bin/activate
python -c "
import os, psycopg2, yaml
from geo.layers.landcover import load_landcover
from geo.layers.transport import load_transport
src=yaml.safe_load(open('config/layer_sources.yaml'))['layers']
conn=psycopg2.connect(os.environ['DB_DSN'])
tiles=['data/ground_truth/worldcover/ESA_WorldCover_10m_2021_v200_%s_Map.tif'%t for t in src['landcover']['tiles']['ua_donbas']]
print('landcover:', load_landcover('ua_donbas', conn, tiles))
print('transport:', load_transport('ua_donbas', conn, 'data/ground_truth/osm/ukraine-latest.osm.pbf', (36.0,46.8,39.5,49.5)))
"
```
Expected: non-zero counts for both. (Transport over the full Ukraine PBF is slow — minutes — because it scans all ways; acceptable for a one-time populate.)

- [ ] **Step 3: Run the populate for black_sea**

Repeat Step 2 with `black_sea`, its tile list, and bbox `(32.0,44.0,37.0,46.3)`.

- [ ] **Step 4: Verify population + sanity**

Run:
```bash
python -c "
import os, psycopg2
c=psycopg2.connect(os.environ['DB_DSN']); cur=c.cursor()
for t in ('ua_donbas','black_sea'):
    cur.execute(\"SELECT landcover_label, count(*) FROM geo.cell_context WHERE theater_id=%s AND dominant_landcover IS NOT NULL GROUP BY 1 ORDER BY 2 DESC\",(t,))
    print(t, cur.fetchall())
"
```
Expected: ua_donbas dominated by `cropland`; black_sea has meaningful `water`. Non-empty road_surface counts. If a theater is all-NULL, the tile paths or bbox are wrong — fix before continuing.

- [ ] **Step 5: Commit (code only)**

```bash
git add geo/layers/landcover.py
git commit -m "geo: write builtup_pct from WorldCover class-50 fraction; populate substrate (run)"
```

---

### Task A4: Re-freeze the eval gate with the plausibility gate live

**Files:**
- Create: `fusion/tests/test_landcover_penalty.py`
- Modify: `eval/fixtures/*` (regenerated, not hand-edited)

**Interfaces:**
- Consumes: `FusionConfig.landcover_penalty(obs_type, landcover_code)` (existing).
- Produces: a passing gate on the new baseline; a truth-table test pinning the gate's behavior.

- [ ] **Step 1: Write the truth-table test**

Create `fusion/tests/test_landcover_penalty.py`:
```python
from fusion.config import load_fusion_config

def test_naval_transit_requires_water():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("naval_transit", 80) == 1.0       # water OK
    assert cfg.landcover_penalty("naval_transit", 40) == 0.2       # cropland penalized

def test_damage_implausible_over_water():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("building_damaged", 80) == 0.5    # water penalized
    assert cfg.landcover_penalty("building_damaged", 50) == 1.0    # built-up OK

def test_no_rule_or_no_data_is_neutral():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("strike", 40) == 1.0              # no rule
    assert cfg.landcover_penalty("naval_transit", None) == 1.0     # unpopulated cell
```
(Adjust the loader name to the real one in `fusion/config.py` — confirm `load_fusion_config` vs the actual factory.)

- [ ] **Step 2: Run it**

Run: `python -m pytest fusion/tests/test_landcover_penalty.py -q`
Expected: PASS (the gate is already implemented; this pins it).

- [ ] **Step 3: Re-freeze fixtures**

The synthetic eval corpus does not depend on live land cover (its obs carry no real cells), so the gate stays neutral there and the digest should be unchanged. Confirm:

Run: `python -m eval.harness`
Expected: exit 0; event_recall 1.0; over_merge 0; no_silent_drop true; **digest unchanged**. If the digest changed, regenerate fixtures per the repo's documented step (`python -m eval.build_fixtures`) and re-run until exit 0.

- [ ] **Step 4: Re-project the live board so the gate applies to real data**

Run:
```bash
python -m fusion.run --theater ua_donbas
python -m fusion.run --theater black_sea
```
Expected: 0 dropped both theaters. Spot-check the gate bit:
```bash
python -c "
import os,psycopg2
c=psycopg2.connect(os.environ['DB_DSN']);cur=c.cursor()
cur.execute(\"SELECT count(*) FROM world.event e JOIN geo.cell_context g ON g.cell_id=e.cell_id WHERE e.event_type='naval_transit' AND g.dominant_landcover IS NOT NULL AND g.dominant_landcover<>80\")
print('naval_transit on non-water cells (should be few/penalized):', cur.fetchone()[0])
"
```

- [ ] **Step 5: Commit**

```bash
git add fusion/tests/test_landcover_penalty.py eval/fixtures
git commit -m "fusion: pin land-cover plausibility gate; re-prove eval gate on live substrate"
```

---

### Task A5: Event geography context (text + meta) and exposure upgrade

**Files:**
- Create: `api/geo_phrase.py`
- Test: `api/tests/test_geo_context_phrase.py`
- Modify: `api/coarsen.py` (attach phrase), `assess/exposure.py` (built-up)
- Test: `assess/tests/test_exposure_builtup.py`

**Interfaces:**
- Produces: `geo_phrase(landcover_label, road_class, road_surface) -> str | None` ("on cropland", "on cropland · along an unpaved track"). `exposure(event, settlements, cfg)` uses `event.get("builtup_pct")` when present.

- [ ] **Step 1: Write the phrase test**

Create `api/tests/test_geo_context_phrase.py`:
```python
from api.geo_phrase import geo_phrase

def test_landcover_only():
    assert geo_phrase("cropland", None, None) == "on cropland"

def test_landcover_and_unpaved_road():
    assert geo_phrase("trees", "track", "unpaved") == "on woodland · along an unpaved track"

def test_paved_road_not_called_dirt():
    assert "unpaved" not in geo_phrase("built-up", "primary", "paved")

def test_nothing_known():
    assert geo_phrase(None, None, None) is None
```

- [ ] **Step 2: Run it (fails)**

Run: `python -m pytest api/tests/test_geo_context_phrase.py -q`
Expected: FAIL — no module `api.geo_phrase`.

- [ ] **Step 3: Implement geo_phrase**

Create `api/geo_phrase.py`:
```python
"""Compose a short, cell-level geography phrase for an event from substrate columns. Pure."""
from __future__ import annotations

_LABEL = {"trees": "woodland", "cropland": "cropland", "grassland": "grassland",
          "built-up": "a built-up area", "water": "water", "wetland": "wetland",
          "shrubland": "shrubland", "bare": "bare ground"}

def geo_phrase(landcover_label, road_class, road_surface):
    parts = []
    if landcover_label:
        parts.append("on " + _LABEL.get(landcover_label, landcover_label))
    if road_class:
        if road_surface == "unpaved":
            parts.append("along an unpaved track" if road_class in ("track", "path")
                         else f"along an unpaved {road_class} road")
        elif road_class in ("motorway", "trunk", "primary", "secondary"):
            parts.append(f"near a {road_class} road")
    return " · ".join(parts) if parts else None
```

- [ ] **Step 4: Run it (passes)**

Run: `python -m pytest api/tests/test_geo_context_phrase.py -q`
Expected: PASS.

- [ ] **Step 5: Attach the phrase in coarsen**

In `api/coarsen.py`, when building the coarsened event view, look up the cell's `landcover_label`/`nearest_road_class`/`road_surface` (join already-available `geo.cell_context` in the event query, or pass them in) and add `geo_context: geo_phrase(...)` to the event meta. Do NOT mutate the stored event; this is presentation only. (If `coarsen_event` has no DB handle, add the three fields to the `queries.list_events` SELECT and pass them through — keep cell-level only, no coords.)

- [ ] **Step 6: Exposure built-up test**

Create `assess/tests/test_exposure_builtup.py`:
```python
from assess.exposure import exposure
from assess.config import load_assessment_config  # confirm real loader name

def _cfg(): return load_assessment_config()

def test_builtup_beats_proxy_when_present():
    cfg = _cfg()
    settlements = [{"label": "Town", "lon": 37.0, "lat": 48.0}]
    ev = {"lon": 37.05, "lat": 48.0, "event_type": "strike", "builtup_pct": 0.9}
    far = {"lon": 37.05, "lat": 48.0, "event_type": "strike", "builtup_pct": 0.0}
    assert exposure(ev, settlements, cfg)["score"] > exposure(far, settlements, cfg)["score"]

def test_falls_back_to_proxy_when_builtup_none():
    cfg = _cfg()
    settlements = [{"label": "Town", "lon": 37.0, "lat": 48.0}]
    ev = {"lon": 37.0, "lat": 48.0, "event_type": "strike", "builtup_pct": None}
    assert exposure(ev, settlements, cfg) is not None    # proxy path still works
```

- [ ] **Step 7: Run it (fails), implement, pass**

In `assess/exposure.py` `exposure(...)`, when `event.get("builtup_pct") is not None`, blend or replace the settlement proximity with `builtup_pct` (e.g. `proximity = max(proximity, builtup_pct)`), so a high built-up cell scores high exposure regardless of the nearest-settlement distance; keep the proxy when `builtup_pct` is None. Ensure `load_events` (the caller) selects `g.builtup_pct` into the event dict.

Run: `python -m pytest assess/tests/test_exposure_builtup.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/geo_phrase.py api/tests/test_geo_context_phrase.py api/coarsen.py assess/exposure.py assess/tests/test_exposure_builtup.py
git commit -m "geo: event geography phrase + exposure uses real builtup_pct"
```

---

### Task A6: Terrain profile aggregation

**Files:**
- Create: `geo/terrain.py`
- Test: `geo/tests/test_terrain.py`

**Interfaces:**
- Produces: `terrain_profile(conn, theater_id, cell_ids: list[str]) -> dict` →
  `{landcover: {label: pct, ...}, builtup_pct: float, road_unpaved_share: float, n_cells: int}`.

- [ ] **Step 1: Write the test (pure aggregation over injected rows)**

Create `geo/tests/test_terrain.py`:
```python
from geo.terrain import summarize_cells

def test_landcover_mix_and_unpaved_share():
    rows = [
        {"landcover_label": "cropland", "builtup_pct": 0.0, "road_surface": "paved"},
        {"landcover_label": "cropland", "builtup_pct": 0.1, "road_surface": "unpaved"},
        {"landcover_label": "trees", "builtup_pct": 0.0, "road_surface": None},
        {"landcover_label": "built-up", "builtup_pct": 0.8, "road_surface": "unpaved"},
    ]
    out = summarize_cells(rows)
    assert out["n_cells"] == 4
    assert round(out["landcover"]["cropland"], 2) == 0.5
    assert round(out["builtup_pct"], 3) == 0.225           # mean of builtup_pct
    assert round(out["road_unpaved_share"], 3) == round(2/3, 3)  # of cells with a known surface

def test_empty():
    assert summarize_cells([])["n_cells"] == 0
```

- [ ] **Step 2: Run (fails)**

Run: `python -m pytest geo/tests/test_terrain.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Implement**

Create `geo/terrain.py`:
```python
"""Aggregate the land-cover/road substrate of a set of 1km cells into an area terrain profile. Pure
`summarize_cells` (unit-tested on injected rows); `terrain_profile` is the thin DB wrapper."""
from __future__ import annotations

def summarize_cells(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"landcover": {}, "builtup_pct": 0.0, "road_unpaved_share": 0.0, "n_cells": 0}
    counts: dict[str, int] = {}
    bu_sum = 0.0
    surfaced = unpaved = 0
    for r in rows:
        lbl = r.get("landcover_label")
        if lbl:
            counts[lbl] = counts.get(lbl, 0) + 1
        bu = r.get("builtup_pct")
        if bu is not None:
            bu_sum += bu
        s = r.get("road_surface")
        if s in ("paved", "unpaved"):
            surfaced += 1
            unpaved += (s == "unpaved")
    known = sum(counts.values()) or 1
    return {
        "landcover": {k: v / known for k, v in sorted(counts.items(), key=lambda x: -x[1])},
        "builtup_pct": bu_sum / n,
        "road_unpaved_share": (unpaved / surfaced) if surfaced else 0.0,
        "n_cells": n,
    }

def terrain_profile(conn, theater_id: str, cell_ids: list[str]) -> dict:
    if not cell_ids:
        return summarize_cells([])
    with conn.cursor() as cur:
        cur.execute(
            """SELECT landcover_label, builtup_pct, road_surface FROM geo.cell_context
               WHERE theater_id = %s AND cell_id = ANY(%s)""",
            (theater_id, cell_ids))
        rows = [{"landcover_label": r[0], "builtup_pct": r[1], "road_surface": r[2]}
                for r in cur.fetchall()]
    return summarize_cells(rows)
```

- [ ] **Step 4: Run (passes)**

Run: `python -m pytest geo/tests/test_terrain.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add geo/terrain.py geo/tests/test_terrain.py
git commit -m "geo: terrain_profile aggregation (land-cover mix, builtup, unpaved share)"
```

---

## Phase B — Unified area API + recency rebalance

### Task B1: Area-ref resolution + generalized context

**Files:**
- Modify: `api/queries.py`
- Test: `api/tests/test_area_ref.py`

**Interfaces:**
- Produces:
  - `_resolve_area(conn, ref) -> dict | None` → `{ref, kind, id, label, theater, level, cell_filter_sql, cell_filter_params}` where `cell_filter_sql` is a SQL fragment selecting the area's cells.
  - `gather_area_context_ref(conn, ref) -> dict | None` — same shape as the existing `gather_area_context` plus `terrain` and `cell_ids`.
  - existing `gather_area_context(conn, aoi_id)` re-expressed as `gather_area_context_ref(conn, f"aoi:{aoi_id}")` (back-compat shim, callers unchanged).

- [ ] **Step 1: Write the test**

Create `api/tests/test_area_ref.py` (live DB; mark/skip if no DB):
```python
import os, pytest, psycopg2

@pytest.fixture
def conn():
    dsn = os.environ.get("DB_DSN")
    if not dsn: pytest.skip("no DB_DSN")
    c = psycopg2.connect(dsn); yield c; c.close()

def test_admin_ref_resolves_cells(conn):
    from api.queries import _resolve_area, rollup
    units = rollup(conn, "ua_donbas", 1, None, None)
    aid = units[0]["admin_id"]
    a = _resolve_area(conn, f"admin:{aid}")
    assert a and a["kind"] == "admin" and a["theater"] == "ua_donbas"

def test_aoi_ref_matches_legacy(conn):
    from api.queries import gather_area_context, gather_area_context_ref, list_aois
    aois = list_aois(conn, "ua_donbas")
    if not aois: pytest.skip("no AOIs")
    aid = aois[0]["aoi_id"]
    legacy = gather_area_context(conn, aid)
    unified = gather_area_context_ref(conn, f"aoi:{aid}")
    assert unified["label"] == legacy["label"]
    assert len(unified["events"]) == len(legacy["events"])
```

- [ ] **Step 2: Run (fails)**

Run: `set -a && source .env && set +a && python -m pytest api/tests/test_area_ref.py -q`
Expected: FAIL — `_resolve_area` not defined.

- [ ] **Step 3: Implement `_resolve_area` + `gather_area_context_ref`**

In `api/queries.py`:
```python
def _resolve_area(conn, ref: str):
    kind, _, rid = ref.partition(":")
    with conn.cursor() as cur:
        if kind == "aoi":
            cur.execute("SELECT label, theater_id FROM world.area_of_interest WHERE aoi_id=%s", (int(rid),))
            r = cur.fetchone()
            if not r: return None
            return {"ref": ref, "kind": "aoi", "id": int(rid), "label": r[0], "theater": r[1],
                    "level": None,
                    "cell_sql": "SELECT cell_id FROM world.aoi_cell WHERE aoi_id=%s",
                    "cell_params": (int(rid),)}
        if kind == "admin":
            cur.execute("SELECT name, theater_id, level FROM geo.admin_unit WHERE admin_id=%s", (rid,))
            r = cur.fetchone()
            if not r: return None
            return {"ref": ref, "kind": "admin", "id": rid, "label": r[0], "theater": r[1],
                    "level": r[2],
                    "cell_sql": "SELECT cell_id FROM geo.cell_context WHERE %s IN (admin_l1_id, admin_l2_id, admin_l3_id)",
                    "cell_params": (rid,)}
    return None
```
Then write `gather_area_context_ref(conn, ref)` by lifting the body of the current `gather_area_context` but substituting the area's `cell_sql` subquery for `(SELECT cell_id FROM world.aoi_cell WHERE aoi_id=%s)` in the events/anomalies/families queries, collecting `cell_ids` from `cell_sql`, and adding `terrain = terrain_profile(conn, theater, cell_ids)`. Re-point the old `gather_area_context(conn, aoi_id)` to `return gather_area_context_ref(conn, f"aoi:{aoi_id}")` (drop `terrain`/`cell_ids` keys are additive, so existing callers are unaffected).
(Confirm the `geo.admin_unit` column names — `admin_id`, `name`, `level` — against migration 0011; adjust if they differ.)

- [ ] **Step 4: Run (passes)**

Run: `python -m pytest api/tests/test_area_ref.py -q`
Expected: PASS (or skip if no AOIs).

- [ ] **Step 5: Run the existing area/synth tests for regression**

Run: `python -m pytest assess/ synth/ api/ -q`
Expected: PASS — the back-compat shim keeps `/watch`, `synth.run`, `areas.py` green.

- [ ] **Step 6: Commit**

```bash
git add api/queries.py api/tests/test_area_ref.py
git commit -m "api: unified area-ref (admin:/aoi:) context gathering + terrain"
```

---

### Task B2: `GET /area/{ref}` + recency-split payload

**Files:**
- Modify: `api/queries.py` (`area_payload`), `api/routers/areas.py`
- Modify: `config/assessment.yaml` (`recent_window_days` if not present)

**Interfaces:**
- Consumes: `gather_area_context_ref`, `classify_attention`, `generate_read`/`deterministic_read`, `terrain_profile`, the existing significance ranking.
- Produces: `GET /area/{ref}` → `{area, read, attention, terrain, recent, significant, children}`; `GET /area/{ref}/read` → the Read.

- [ ] **Step 1: Implement `area_payload` in queries.py**

```python
def area_payload(conn, ref: str, recent_window_days: int = 30):
    from datetime import datetime, timezone
    from assess.attention import classify_attention
    from synth.context import build_context
    from synth.read import deterministic_read
    ctx = gather_area_context_ref(conn, ref)
    if ctx is None:
        return None
    area = _resolve_area(conn, ref)
    now = datetime.now(timezone.utc)
    att = classify_attention(ctx["events"], ctx["anomalies"], now)
    read = deterministic_read(build_context(ctx["label"], ctx["events"], ctx["anomalies"],
                                            ctx["families"], now), att)
    cutoff = now.timestamp() - recent_window_days * 86400
    recent = sorted([e for e in ctx["events"]
                     if e["occurred_start"] and e["occurred_start"].timestamp() >= cutoff],
                    key=lambda e: e["occurred_start"], reverse=True)
    significant = _rank_significant(ctx["events"])      # reuse existing significance ordering
    children = _area_children(conn, area)               # next admin level w/ attention, [] for AOI
    return {"area": {k: area[k] for k in ("ref", "kind", "label", "theater", "level")},
            "read": read, "attention": att, "terrain": ctx["terrain"],
            "recent": recent[:50], "significant": significant[:50], "children": children}
```
Add `_rank_significant(events)` (sort by the significance scalar already used by `top_significant`/`assess.significance`; reuse that helper rather than re-deriving) and `_area_children(conn, area)` (for an admin area, the immediate child admin units via the existing rollup `parent=` query, each annotated with `classify_attention` over its events; for an AOI, `[]`). If wiring child-attention is heavy, children may carry just `{admin_id, name, n_events, band_mix}` from `rollup` for v1 and attention can be added later — but ranking children by attention is the recency goal, so prefer computing it.

- [ ] **Step 2: Add the routes**

In `api/routers/areas.py`:
```python
@router.get("/area/{ref}")
def area(ref: str, conn=Depends(get_conn)) -> dict:
    p = queries.area_payload(conn, ref)
    if p is None:
        raise HTTPException(status_code=404, detail="area not found")
    return p

@router.get("/area/{ref}/read")
def area_read_ep(ref: str, conn=Depends(get_conn)) -> dict:
    p = queries.area_payload(conn, ref)
    if p is None:
        raise HTTPException(status_code=404, detail="area not found")
    return {**p["read"], "attention": p["attention"]}
```
(`ref` contains a colon — FastAPI path params accept it; verify `admin:UA14` routes. If a slash ever appears in an admin_id it won't, but geoBoundaries ids are colon/letter-numeric — confirm.)

- [ ] **Step 3: Verify live**

Run:
```bash
lsof -ti :8000 | xargs kill -9 2>/dev/null; sleep 1
nohup .venv/bin/uvicorn api.main:app --port 8000 >/tmp/api.log 2>&1 &
sleep 4
AID=$(curl -s "http://127.0.0.1:8000/rollup?level=1&theater_id=ua_donbas" | python -c "import sys,json;print(json.load(sys.stdin)['units'][0]['admin_id'])")
curl -s "http://127.0.0.1:8000/area/admin:$AID" | python -c "import sys,json;d=json.load(sys.stdin);print('label',d['area']['label']);print('terrain',list(d['terrain']['landcover'].items())[:3]);print('recent',len(d['recent']),'significant',len(d['significant']),'children',len(d['children']))"
```
Expected: a label, a land-cover mix, non-zero counts; `recent` date-sorted, `significant` priority-sorted.

- [ ] **Step 4: Commit**

```bash
git add api/queries.py api/routers/areas.py config/assessment.yaml
git commit -m "api: GET /area/{ref} unified payload (read+attention+terrain+recent+significant+children)"
```

---

### Task B3: Recency rebalance (knobs + child ranking)

**Files:**
- Modify: `config/assessment.yaml`
- Test: `assess/tests/test_recency_rebalance.py`

**Interfaces:**
- Consumes: `assess.significance.recency`.
- Produces: re-tuned `recency_floor=0.3`, `recency_tau_days=10`; children ranked by attention in `area_payload`.

- [ ] **Step 1: Write the test pinning the new behavior**

Create `assess/tests/test_recency_rebalance.py`:
```python
from datetime import datetime, timedelta, timezone
from assess.significance import recency
from assess.config import load_assessment_config   # confirm loader

def test_floor_and_tau_make_recent_outrank_old():
    cfg = load_assessment_config()
    assert cfg.recency_floor == 0.3
    assert cfg.recency_tau_days == 10
    now = datetime.now(timezone.utc)
    fresh = cfg.recency_floor + (1-cfg.recency_floor)*recency(now - timedelta(days=2), now, cfg.recency_tau_days)
    old   = cfg.recency_floor + (1-cfg.recency_floor)*recency(now - timedelta(days=400), now, cfg.recency_tau_days)
    assert fresh > 0.8 and old < 0.4 and (fresh - old) > 0.4   # recency now bites
```

- [ ] **Step 2: Run (fails on the asserted constants)**

Run: `python -m pytest assess/tests/test_recency_rebalance.py -q`
Expected: FAIL — current floor 0.5 / tau 14.

- [ ] **Step 3: Re-tune the config**

In `config/assessment.yaml` set `recency_tau_days: 10.0` and `recency_floor: 0.3`. Add `recent_window_days: 30` under the significance/insights block if absent.

- [ ] **Step 4: Run (passes)**

Run: `python -m pytest assess/tests/test_recency_rebalance.py -q`
Expected: PASS.

- [ ] **Step 5: Re-run assess + sanity-check the feed**

Run:
```bash
python -m assess.run --theater ua_donbas && python -m assess.run --theater black_sea
curl -s "http://127.0.0.1:8000/insights?theater_id=ua_donbas" | python -c "import sys,json;d=json.load(sys.stdin);[print(round(x['priority'],2), x.get('event_type'), x.get('place_label')) for x in d['significant'][:8]]"
```
Expected: recent high-severity events rank above year-old equivalents; a major historical confirmed incident still appears (floor > 0).

- [ ] **Step 6: Confirm `area_payload` children are attention-ranked**

Ensure `_area_children` sorts by `attention_sort_key`. Re-verify one admin area's `children` leads with an escalating/active child.

- [ ] **Step 7: Commit**

```bash
git add config/assessment.yaml assess/tests/test_recency_rebalance.py
git commit -m "assess: recency rebalance (floor 0.5->0.3, tau 14->10) + attention-ranked children"
```

---

### Task B4: By-area place panel (UI)

**Files:**
- Modify: `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `GET /area/{ref}`.
- Produces: drilling any admin unit renders a place panel = Read + attention badge + terrain chips + Recent activity + Significant incidents + children-with-badges.

- [ ] **Step 1: Fetch the area payload on drill**

In `web/app.js` `renderAreas`/the unit-click handler, when a unit is selected call `api('/area/admin:'+adminId')` and render the place panel into the panel column (reuse `.read-panel`, `.att-badge` from the synthesis pass).

- [ ] **Step 2: Render the place panel**

Add a `placePanel(p)` builder: header (`p.area.label` + breadcrumb), the `✦ Intelligence read` block (`p.read.summary` + attention badge), a **Terrain** row of chips from `p.terrain.landcover` (top 3 + `unpaved X%`), a **Recent activity** list (`p.recent`, newest first, date + type + place + `geo_context` if present), a **Significant incidents** list (`p.significant`), and a **Drill in** list of `p.children` each with its attention badge (click → recurse).

- [ ] **Step 3: Style**

In `web/styles.css` append `.terrain-chips`, `.terrain-chip`, `.place-section h4`, `.recent-row`, reusing existing tokens. Recent rows get a subtle "fresh" accent; older significant rows are calmer — make the recent-vs-historical split visible.

- [ ] **Step 4: Verify with the preview tool**

Start the API, `preview_start`, drill region→raion→hromada, screenshot the place panel. Confirm: Read present, terrain chips show land cover, Recent activity is date-sorted, children carry attention badges.

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/styles.css
git commit -m "ui: By-area place panel (read + terrain + recent vs significant + drill-in)"
```

---

## Phase C — deferred (not in this plan)
Full shell collapse: retire tabs, promote the place panel to the whole main panel, My-watch → "Starred" lens, fold Events/What-matters in. Separate spec/plan when ready.

## Final integration check
- [ ] `python -m pytest -q` (full offline suite) green.
- [ ] `python -m eval.harness` exit 0, no_silent_drop true, over_merge 0.
- [ ] Both theaters re-projected (`fusion.run` + `assess.run`), 0 dropped.
- [ ] Rebuild + republish the static demo (`bash scripts/build_demo.sh`) once `/area` is exported (add `/area/{ref}` snapshots to `export_static.py` + `_staticFile` in a follow-up if the demo should show the place panel — note: the demo currently has no admin-area snapshots; flag if needed).

## Self-review notes
- Spec §A1–A6, §B1–B5 all map to tasks A1–A6, B1–B4 (B5 read-caching for admin units intentionally dropped — admin Reads are on-the-fly deterministic per the spec's "default").
- Loader/config names VERIFIED against source: `load_fusion_config()` (fusion/config.py:118), `load_assessment_config()` with fields `recency_tau_days`/`recency_floor` (assess/config.py:35), `assess.significance.significance(event, now, cfg, cell_type_counts)` (reuse `queries.top_significant` for the per-area ranking — it already supplies `cell_type_counts` for novelty), and `geo.admin_unit(admin_id PK, theater_id, level, name, parent_id)` (0011). The `_resolve_area` admin query and all test imports match these.
- Demo export of `/area` is flagged as a follow-up, not silently assumed.
