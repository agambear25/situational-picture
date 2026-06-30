# Geography substrate + area-spine — design (2026-07-01)

## Goal

Three user asks, resolved into one cohesive build:

1. **By-area becomes the spine** (the anchor view) — chosen depth: *make it the spine*. A geographic
   drill-down (region → district → community) where opening any place shows its Intelligence Read,
   ranked events, and geography in one view.
2. **Recency is under-reflected** — chosen gap: *recent events are buried in the ranking* under
   historical, high-confidence incidents.
3. **Geography / structure detection** (farmland, forest, dirt road) — chosen depth: *stamp every
   cell AND contextualize events* with it.

**Scope of THIS pass (user direction "A then B in one pass"):** Phase **A** (geography substrate +
event contextualization) then Phase **B** (unified area API + recency rebalance), delivered together.
The full UI shell collapse — folding Events / What-matters into the place view, retiring tabs — is
**Phase C, deferred** to a follow-on. B is surfaced *inside the existing By-area tab* so this pass has
visible payoff without the shell rewrite.

## The unifying abstraction: a "place" is one object

Today AOIs (drawn watch-areas) and admin units (oblast/raion/hromada) are different things on
different code paths. The spine only works if they unify:

> An **area** is referenced as `admin:<id>` **or** `aoi:<id>`. The synthesis Read, attention,
> ranked events, and geography profile are all computed *for an area*, independent of its kind.

This is the foundation of Phase B. The Read/attention engines already take a context dict
(`synth/context.py`, `assess/attention.py` are area-kind-agnostic), so only the **context-gathering**
step needs to branch on area kind.

---

## Phase A — Geography substrate + contextualization

All data + wiring already exist; they are dormant because `geo.cell_context` land-cover/road columns
are NULL for all 162k cells (verified: `dominant_landcover`, `builtup_pct`, `nearest_road_class` =
0 populated in both theaters).

### A1. Data fetch (reproducible scripts)
- **ESA WorldCover 10m 2021 v200** (CC-BY) — GeoTIFF tiles from the S3 URL already in
  `config/layer_sources.yaml`. `ua_donbas` tile list exists (`N45E034 N45E037 N48E034 N48E037`);
  **add a `black_sea` tile list.** WorldCover tiles are 3°×3° named by SW corner; 32–37E/44–46.3N
  needs the E033+E036 columns × N42+N45 rows = **`N42E033 N42E036 N45E033 N45E036`** (verify the
  exact set exists at fetch — some all-ocean tiles may be absent, which is fine).
- **OSM roads** — Geofabrik `ukraine-latest.osm.pbf` (ODbL) already configured; covers Crimea too.
- New `scripts/fetch_geography.sh` downloads both into a gitignored data dir.

### A2. Populate the substrate
- Run `geo.layers.landcover.load_landcover` → `dominant_landcover` + `landcover_label` per cell
  (WorldCover codes: 10 trees / 20 shrub / 30 grassland / 40 cropland / 50 built-up / 60 bare /
  80 water / 90 wetland).
- Run `geo.layers.transport.load_transport` → `nearest_road_class` + `has_bridge` per cell.
  `ROAD_CLASS_RANK` already includes `track`(7) and `path`(8) — the **dirt-road proxy**. Extend the
  handler to also read `surface` (`unpaved`/`dirt`/`ground`/`gravel`) so an *unpaved primary* is
  flagged, not just `highway=track`. Store a coarse `road_surface` (paved|unpaved|unknown).
- Optionally derive `builtup_pct` from WorldCover class-50 fraction per cell (a real number to
  replace the settlement-distance proxy in exposure).
- A `geo/load_substrate.py` run wires these; idempotent UPSERT on `cell_context` (already the case).

### A3. Activate the (already-wired) land-cover plausibility gate
`fusion/config.py.landcover_penalty()` + `fusion/run.py`'s `landcover_by_obs()` already apply
`taxonomy.yaml: landcover_plausibility` — but it no-ops on empty data. Populating A2 turns it on:
`naval_transit` off-water → ×0.2; `building_damaged`/`crater` over water → ×0.5/0.6; `troop_move`/
`convoy` in water → ×0.3. **This is a determinism-spine change** → re-freeze eval fixtures and prove
`eval.harness` still exits 0 with the gate live (the gate is pure; the digest changes legitimately,
so re-baseline + assert no_silent_drop / over_merge=0 hold).

### A4. Contextualize events (descriptive)
At coarsen/label time attach the event-cell's `landcover_label` + `nearest_road_class`/`road_surface`
to event meta, and compose a short phrase into the human text: *"on cropland near forest"*,
*"along an unpaved track"*. Pure formatting from substrate columns; no precise coords leak
(cell-level only, consistent with the coarsening boundary).

### A5. Exposure upgrade
`assess/exposure.py` currently uses settlement-distance as a built-up proxy. Switch to real
`builtup_pct` (+ land cover) when present, falling back to the proxy when NULL — so a strike on
class-50 built-up scores higher exposure than one on cropland.

### A6. Area terrain profile (aggregation)
New `geo/terrain.py` (pure query helper): given a set of cell_ids (an area), return the land-cover
mix (% cropland/forest/grassland/built-up/water), road length by class, and unpaved share. Consumed
by Phase B's area payload. One implementation, used by both admin units and AOIs.

---

## Phase B — Unified area API + recency rebalance

### B1. Unified context gathering
Generalize `api/queries.gather_area_context` to accept an **area-ref**:
- `aoi:<id>` → cells via `world.aoi_cell` (existing path).
- `admin:<id>` → cells `WHERE <id> IN (admin_l1_id, admin_l2_id, admin_l3_id)` (existing rollup
  join). Resolve the admin level + label from `geo.admin_unit`.
Returns the same context dict shape, so `synth` + `assess` reuse unchanged. Add `terrain` (A6) to
the dict.

### B2. Area endpoints
- `GET /area/{ref}` → `{ area: {ref,label,kind,level,breadcrumb,bbox}, read, attention, terrain,
  recent: [...], significant: [...], children: [...] }`.
  - `read` + `attention` from the existing engines.
  - `recent` = events in the last **30 days** (configurable `recent_window_days`), newest-first,
    with a per-event `is_recent_spike` flag.
  - `significant` = top-priority events (existing significance ranking) — the *all-time* lens.
  - `children` = immediate sub-areas (next admin level), each with its attention badge + counts,
    so the response itself drives the drill-down.
- `GET /area/{ref}/read` → the Read alone (cached for AOIs via `world.area_read`; on-the-fly
  deterministic for admin units, or cached too — see B5).
- `/watch` is reframed as **starred areas** (unchanged mechanics; AOIs are the stars). `/rollup`
  stays as-is for the choropleth.

### B3. Recency rebalance (the fix)
Two parts, because forcing recency into one significance scalar is what buried it:
1. **Separate surfaces.** `recent` and `significant` are *distinct* lists in the payload (B2) — the
   place view shows "Recent activity" above "Significant incidents". Recency gets its own surface.
2. **Re-tune the scalar so recency still bites in the priority list:** `config/assessment.yaml`
   `recency_floor` 0.5 → **0.3**, `recency_tau_days` 14 → **10**. Rank the **drill-down children by
   attention** (recent-vs-prior escalation), not raw total volume, so escalating places float up.
   Re-run `assess.run`; spot-check that a fresh single-source strike now out-ranks a year-old one of
   equal severity, while a major historical confirmed incident still appears (floor > 0).

### B4. Surface in the existing By-area tab (this pass's visible deliverable)
Upgrade `web/app.js` `renderAreas`/`renderAreaEvents`: when you drill to any unit, render a compact
**place panel** = Intelligence Read + attention badge + terrain profile chips + "Recent activity"
list + "Significant incidents" list + children-with-badges. Reuses the `.read-panel` / `.att-badge`
CSS from the synthesis pass. This is the place view living *inside* the current tab — Phase C later
promotes it to the whole shell.

### B5. Read caching for admin units (optional, perf)
`world.area_read` is AOI-keyed (FK→aoi). Admin-area Reads can be computed on-the-fly (deterministic,
fast) for now; if LLM Reads for regions are wanted, add a parallel `world.admin_read` cache or widen
the key. **Default: on-the-fly deterministic for admin units this pass** (YAGNI the cache until the
volume hurts).

---

## Build order within the one pass

A1 → A2 (data first, lowest risk, immediately inspectable) → A3 (gate + re-freeze, the one spine
change) → A4/A5/A6 (formatting + aggregation) → B1 → B2 → B3 (recency) → B4 (UI). A is independently
valuable even if B slips; B depends on A6 (terrain) + A2 (data).

## Deferred (Phase C, explicitly not this pass)
Full shell collapse: tabs retire, By-area place view becomes the whole main panel, My-watch →
"Starred" lens, Events/What-matters fold in as place sections, map-first rail. Tracked, not built now.

## Honesty notes & risks
- **WorldCover is a 2021 single epoch** — correct for terrain ("this cell is cropland"), NOT for
  change detection. State it in the UI attribution.
- **Land cover is *where*, not evidence** — context, never corroboration. The plausibility gate only
  *penalizes* implausible types; it never invents or confirms.
- **"Dirt road" = OSM tag coverage** — as complete as OSM is in that area; absence ≠ no track.
- **A3 changes the fusion digest** — this is a real spine change. Gate must be re-frozen and proven
  (no_silent_drop, over_merge=0, replay bit-identical on the *new* baseline). Highest-care step.
- **$0 / local / Claude-OFF / no precise coords** invariants all hold — substrate is free downloads;
  context is cell-level.

## Testing / verification
- A2: assert populated-cell counts > 0 and land-cover histogram is sane per theater (cropland should
  dominate Donbas; water non-trivial in black_sea).
- A3: unit-test `landcover_penalty` truth table; re-freeze fixtures; `eval.harness` exit 0; confirm a
  `naval_transit` on a land cell is penalized and a vessel on water is not.
- A6/B1: `terrain` aggregation unit test on a synthetic cell set; `gather_area_context('admin:…')`
  resolves the right cells (cross-check vs `/rollup` counts).
- B2/B3: contract-verify `/area/{ref}` live for an oblast, a raion, and an AOI; assert `recent` is
  date-sorted and `significant` is priority-sorted; recency re-tune spot-check (fresh > stale).
- B4: Claude_Preview screenshot of the By-area place panel for a drilled hromada.

## Files (anticipated)
- A: `scripts/fetch_geography.sh`, `config/layer_sources.yaml` (black_sea tiles),
  `geo/layers/transport.py` (surface tag), `geo/load_substrate.py` (run path),
  `api/coarsen.py`/label path (A4 text), `assess/exposure.py` (A5), `geo/terrain.py` (A6),
  re-frozen `eval/fixtures/*` (A3).
- B: `api/queries.py` (`gather_area_context` area-ref, terrain), `api/routers/areas.py`
  (`/area/{ref}`, `/area/{ref}/read`), `config/assessment.yaml` (recency knobs),
  `web/app.js` + `web/styles.css` (B4 place panel).
