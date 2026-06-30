# OSINT COP — Build Checkpoint vs PRD (2026-06-30)

A snapshot of what is built, what remains, what the PRD (v1.1) calls for, and what I recommend
next. Use it to decide sequencing. The companion UI-rebuild design lives in a separate spec.

---

## 1. Where we are

A Ukraine-first, local-only, $0, event-sourced OSINT Common Operating Picture is **running live**
on the Mac. The deterministic spine (ingest → fusion → read model → API → UI) is complete and
gated; the board now carries a **continuous 2022→2026 conflict chronology**, an **admin
hierarchy with region→district→community drill-down**, an **assessment layer** (significance,
anomaly, exposure, collection-gap), and **four independent sensor modalities** (text, thermal,
SAR, optical).

- Gate: `python -m eval.harness` exits 0; **123 offline tests pass**; replay bit-identical.
- Live board: **5,557 events across 53 months**; 7 source families
  (ucdp 5,560 · unosat 255 · nasa_firms 202 · copernicus_sar 139 · copernicus_optical 70 ·
  nasa_modis 27 · gdelt 3).
- Standing constraints all hold: $0 / local / Claude-OFF; never ingest ISW/DeepStateMap geometry;
  no person entities; 1km MGRS grid; coords coarsened at write.

---

## 2. What is BUILT

**Phases 0–2 (spine) — complete.** Append-only Observation log; rebuildable CQRS read model;
BLOCK→SCORE→ADJUDICATE→PROPAGATE fusion (pure, replayable); noisy-OR confidence over independent
families; 3B→7B→14B local LLM gray-band adjudicator (Ollama, with keep-separate fallback); UCDP +
FIRMS adapters; read-only FastAPI; coarsening boundary (no person, cell-only geometry).

**Phase 3 (imagery & CV) — through 3f.**
- 3a live smoke + embedding pin · 3b GEE S1/S2 acquisition · 3c UNOSAT ground truth + eval ·
  3d detector framework + determinism cache — **done**.
- **3e classical SAR log-ratio detector — done + validated on real Sentinel-1** (Mariupol vs
  UNOSAT, F1 ≈ 0.40 baseline).
- **3f Sentinel-2 optical detector (MNDWI flood / dNBR burn) — done + validated on real S2**
  (Sviati Hory + Kreminna 2022 forest burns → 25 burn_scar + 1 flood events on the board, the
  independent `copernicus_optical` family). Cross-modal corroboration proven offline (optical +
  text → High, bit-identical).

**Phase 4 (assessments) — 4a + 4c.**
- **4a significance + per-cell anomaly — done.** `/insights` ranks events by severity × confidence
  × recency × novelty (corroboration-weighted so confirmed incidents lead); flags activity spikes
  and escalations.
- **4c exposure + collection-gap — done.** Exposure = severity × proximity-to-settlement;
  collection-gap = recent, high-stakes, single-source events to chase a second source on.

**Phase 5 (breadth) — 5a (early).** Own-curated, license-clean **control overlay** (who held each
area, over time), synced to the time scrubber.

**Cross-cutting (beyond the strict roadmap).**
- **Continuous chronology**: UCDP GED 2022–2026 aggregated to (cell, month), 53 continuous months.
- **Admin substrate + drill-down**: geoBoundaries oblast→raion→hromada loaded (5/60/886 units),
  every cell stamped; `/rollup` choropleth + breadcrumb "By area" tab.
- **fusion.block() optimised** from O(N²) to a spatial-grid pre-filter (bit-identical) — unlocks
  finer-grained ingest.
- **HTML operator UI** served by the API (dark "intelligence-ops" board, place-name-forward,
  plain-language confidence, satellite basemap, time scrubber, per-cell history).

---

## 3. What is LEFT (per PRD v1.1)

| Pass | PRD objective | Status |
|---|---|---|
| 3g | Deep-CV training harness (offline, free GPU → HF checkpoint) | not started |
| 3h | Deep-CV inference adapter + determinism contract | not started |
| 3i | **UI: imagery layer** — damage/change layer, corroboration + provenance shown | **not started** |
| 3j | **UI: before/after + human-in-the-loop** confirm/reject → label_annotation | not started |
| 3k | Extended substrate: soil / utility / installation → cell_context non-NULL | partial (admin done; rest NULL) |
| 4b | Mobility + flood assessments (substrate-dependent) | not started |
| 4d | **Entity population** (installation/site/vessel, cell-granular, confidence + staleness) | **schema exists, 0 rows** |
| 5b | Delhi + Hormuz theaters (config swap); Hormuz SAR-vessel microservice | not started |
| 5c | **React/MapLibre UI** + deployment decision | not started (HTML UI is the interim) |
| 6a | **RAG "ask the COP"** — LangGraph + pgvector retrieval → grounded, cited answers | not started |
| 6b | Analyst-query UI (chat panel with citations) | not started |

`world.entity` (entity_id/theater_id/kind/label/cell_id/confidence/first_seen/last_seen/meta) and
`geo.geo_feature` are in the schema but **empty** — the foundation for 4d and for named geographic
entities is present but unpopulated.

---

## 4. The new UI vision (this checkpoint's trigger)

The user wants a far richer operator experience:

1. **Entity drill-down** from regional/geographic level down to a particular entity or grid of
   interest — extending the existing oblast→raion→hromada drill-down to a 4th "entity / cell"
   level.
2. **Named geographic entities** the user can create — e.g. a river bank acting as a
   fortification / natural obstacle, a defensive line, a grid of interest — with strong focus.
3. **Area-of-interest intelligence panel** — a journalist-style brief for a selected area:
   plain-text **summary chronology** (what has happened), **latest signs**, a rough **forecast**
   read (climate, war, troop movements, flare-ups), plus **context** — population and (where
   available, rough) pre-war language demographics.
4. **External commentary** — pull relevant snippets from social/long-form sources (Reddit,
   YouTube, Substack) about the area or the wider theater.

**How it maps to the PRD:** this is mostly **4d (entities)** + **3i (imagery/provenance in UI)** +
**6a (RAG/narrative synthesis)** + a NEW external-context-ingestion capability the PRD only gestures
at. It is genuinely a multi-subsystem effort and should be decomposed (see §5).

---

## 5. Recommendation

The data foundation is solid; the highest-leverage work is now **presentation + synthesis**, which
is exactly the user's UI vision. I recommend decomposing the vision into four sub-projects and
building them in dependency order, each as its own spec → plan → implementation cycle:

- **A. Entity & area model (foundation)** — extend `world.entity` for user-created *named
  geographic* entities (river/obstacle/line/grid-of-interest, not just military), anchored to a set
  of cells + optional geometry; an `/entities` + `/areas/{id}` API. Unblocks everything else. Maps
  to PRD 4d. **Recommended first.**
- **B. Entity drill-down + focus UI** — extend the "By area" drill-down to entities/grids; a strong
  single-area focus view. Maps to 3i + the UI side of 4d.
- **C. Area intelligence panel (synthesis)** — plain-text chronology summary + context
  (population/demographics from free sources) + forecast read, generated locally (Ollama) and
  grounded in the area's own events. Maps to 6a, read-only over the spine.
- **D. External commentary research** — pull Reddit/YouTube/Substack snippets for an area. New
  capability; needs a careful $0 / rate-limit / licensing approach. Lowest in the order (most
  external risk).

**Lower priority but worth noting:** 4b flood assessment is now cheap (we have an optical flood
detector + a hydro substrate hook); 3j before/after HITL pairs naturally with B; the React/MapLibre
5c migration should wait until the HTML UI's feature set stabilises after this vision lands.

**Not recommended now:** 3g/3h deep CV (the classical detectors are an honest baseline and the
$0/local constraint makes GPU training a side quest); 5b multi-theater (breadth before the depth
this vision adds).
