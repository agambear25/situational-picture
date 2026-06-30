# Named geographic entities + drill-down navigation — design (sub-project 1 of the UI rebuild)

First of three sub-projects from the UI-vision brainstorm (the others: an area intelligence panel,
and external commentary). This one is the **foundation**: a model for named geographic entities, a
feature-library substrate to derive them from, and the navigation/focus to reach them.

Decisions locked in brainstorming: **both derived + drawn**; **comprehensive feature kinds**
(water/road/forest/builtup/ridge/…, loaded incrementally); **two-tier** (reference library +
annotated areas-of-interest); separate from the PRD's `world.entity`.

---

## Section 1 — Data model  *(approved)*

**Tier 1 — `geo.geo_feature` (reference library; table exists, empty).** Every derived feature:
`feature_id · theater_id · kind (water|road|forest|builtup|ridge|rail|…) · subkind · name (OSM name)
· geom · source (osm|dem) · meta(jsonb)` + GIST index. Read-only; rendered as toggleable map layers.

**Tier 2 — `world.area_of_interest` (the analyst's named entities).**
`aoi_id · theater_id · kind (obstacle|defensive_line|grid_of_interest|named_feature|…) · label
· source (derived|drawn) · source_feature_id (→ geo_feature) · geom · note(text) · created_by · created_at`.

**Join — `world.aoi_cell` (aoi_id, cell_id).** An AOI's geometry is resolved ONCE to the 1km cells
it covers; that cell-set is how it joins to events (events in those cells = the AOI's events) and is
the analytical-not-targeting boundary (an AOI is a set of cells, never precise geometry vs
observations).

**Three ways an AOI is born**, all converging on a cell-set: (1) promote a feature (name a
river/road → references `source_feature_id`, inherits geometry → cells); (2) draw a line/polygon →
cells; (3) lasso grid cells directly. Named `area_of_interest` to avoid clashing with the PRD's
`world.entity` (detected military entities); the UI still calls them "areas / entities of interest".

## Section 2 — Feature loader (OSM + DEM substrate)  *(approved)*

`geo/feature_load.py`, mirroring `admin_load.py` (read → clip to AOI → classify → PostGIS), with a
reproducible `scripts/fetch_features.sh`.

- **Source: Geofabrik Ukraine `.osm.pbf` extract** (not Overpass — road/forest density would time
  out live), filtered with `osmium`/`pyrosm` (osmium is in `[full]`).
- **Classification (OSM tags → kind/subkind):** `waterway in (river,canal)` + `natural=water` →
  water; `highway in (motorway,trunk,primary)` → road; `railway=rail` → rail; `natural=wood` /
  `landuse=forest` → forest; `landuse in (residential,industrial)` → builtup (polygons).
- **Buildings = aggregated**, not footprints (millions of rows, pointless at 1km): built-up area
  polygons now, optional per-cell building density in `cell_context` later.
- **Ridgelines = DEM, deferred within this sub-project:** Copernicus DEM GLO-30 → slope + local
  maxima → `kind=ridge`. Different (raster) pipeline; land water/roads/forests/builtup first, add
  ridge as a follow-on load (model already supports it).
- **Offline-testable core:** tag→kind classification + geometry→cell-set resolution are pure +
  unit-tested; download/PostGIS I/O is the thin shell.

## Section 3 — Navigation, focus, API

**API (read-only except AOI writes):**
- `GET /features?kind=&bbox=` — reference library layers (simplified geometry).
- `GET /aois?theater_id=&kind=` — list AOIs.
- `GET /aois/{id}` — focus: geometry + cell-set + event count/bands (the rich brief is sub-project 2).
- `POST /aois` — create `{kind,label,source,source_feature_id|geom|cell_ids,note,created_by}`;
  resolves to a cell-set; written via the annotation-write session (AOIs are analyst annotations).
- `DELETE /aois/{id}`.
- `GET /events?aoi=<id>` — events in the AOI's cells (extend `list_events`, like the `admin` filter).

**UI (HTML board):**
- **Feature layers** — a toggle group (Rivers / Roads / Forests / Built-up) drawing `geo_feature`.
- **"Areas of interest"** — a section/lens: list + create + focus. Cross-cutting (an AOI can span
  raions), so it's its own lens, and AOIs intersecting the current admin area are surfaced in the
  "By area" focus.
- **Creation:** (a) click a feature → "mark as area of interest" (name it); (b) draw a line/polygon
  (Leaflet draw); (c) lasso grid cells.
- **AOI focus view:** highlight the AOI's cells/geometry + its events (reuse event cards) + back-link.

## Section 4 — Constraints, write path, error handling

- **Coarsening:** AOI→cell-set is the join; events join via cells (no precise observation coords
  leak). AOI geometry is analyst-drawn or public OSM (not sensitive). Resolution: `ST_Intersects`
  feature/drawn geometry → grid cells; lassoed cells used directly.
- **Write path:** `POST/DELETE /aois` use the annotation-write DB session (AOIs are analyst
  annotations, like review/label annotations — soft-reference cells, NOT rebuilt by `fusion.run`).
- **No person entities** (geographic only).
- **Validation:** geometry must intersect the AOI grid; empty cell-set → 422 (logged, not silent).

## Section 5 — Testing

- Pure: tag→kind classification; geometry→cell-set resolution; AOI event-join query shape.
- Loader: offline parse/classify on a tiny OSM fixture.
- API: contract-verified (AOI CRUD + the `aoi` event filter resolve).
- Gate: no fusion-spine impact; `eval.harness` stays 0.

---

## Build order

1. Migration: extend `geo.geo_feature`; add `world.area_of_interest` + `world.aoi_cell` (+ indexes).
2. Feature loader + classification core + tests; one OSM pass (water/roads/forests/builtup).
3. AOI model + API (`/features`, `/aois` CRUD, `/events?aoi=`) + geometry→cell-set resolver.
4. UI: feature layers, AOI lens + creation (promote/draw/lasso), focus view.
5. DEM ridgelines (follow-on load).

Out of scope (later sub-projects): the area intelligence brief (chronology summary / context /
forecast) and external commentary (Reddit/YouTube/Substack).
