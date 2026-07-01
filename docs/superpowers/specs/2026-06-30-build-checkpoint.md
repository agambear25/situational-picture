# OSINT COP — Build Checkpoint vs PRD (2026-06-30, refreshed; §5 added 2026-07-01)

Where the build stands vs the PRD (v1.1) roadmap, after a long build session. Use it to see the
roadmap at a glance and decide sequencing. §4 (synthesis + AOR redesign) and §5 (geography
substrate + area-spine) are both SHIPPED — see their sections below for what's next.

---

## 1. Where we are

A Ukraine-first, local-only, $0, event-sourced OSINT Common Operating Picture, **running live** on
the Mac, **published as a clickable demo**, and now **multi-theater** (land + maritime). The
deterministic spine (ingest → fusion → read model → API → UI) is complete and gated; on top of it:
a continuous 2022→2026 chronology, an admin drill-down, an assessment layer, **four sensor
modalities**, **named areas of interest**, **SAR vessel detection**, and a **theater switcher**.

- Gate: `python -m eval.harness` exits 0; **135 offline tests pass**; replay bit-identical.
- **Ukraine — Donbas board: ~11,900 events** (UCDP now at *daily* granularity), 7 source families
  (ucdp · unosat · nasa_firms · copernicus_sar · copernicus_optical · nasa_modis · gdelt).
- **Black Sea board: 103 events** (Crimea strikes + Sevastopol vessel transits).
- **Public repo + live demo**: https://github.com/agambear25/situational-picture ·
  https://agambear25.github.io/situational-picture/ (static snapshot, both theaters).
- Standing constraints hold: $0 / local / Claude-OFF; never ISW/DeepStateMap; no person entities;
  1km MGRS grid; coords coarsened at write.

---

## 2. What is BUILT

**Phases 0–2 (spine) — complete.** Append-only Observation log; rebuildable CQRS read model;
BLOCK→SCORE→ADJUDICATE→PROPAGATE fusion (pure, replayable; `block()` now spatial-grid, sub-O(N²));
noisy-OR confidence over independent families; 3B→7B→14B local-LLM gray-band adjudicator (Ollama,
keep-separate fallback); UCDP + FIRMS adapters; read-only FastAPI; coarsening boundary.

**Phase 3 (imagery & CV) — 3a–3f done; 3i partial.**
- 3a–3d done. **3e SAR log-ratio** + **3f S2 optical (MNDWI flood / dNBR burn)** — both validated on
  real imagery; optical burns (Sviati Hory + Kreminna) on the board as `copernicus_optical`.
- **3i partial** — per-event **sensor provenance** ("Seen by radar + thermal + news") now in the UI.
- **3k partial** — admin substrate loaded; **OSM feature substrate** (geo_feature: water / roads /
  forests / built-up) loaded; soil/utility/installation still NULL.

**Phase 4 (assessments) — 4a + 4c done.**
- **4a** significance + per-cell anomaly (corroboration-weighted; `/insights`).
- **4c** exposure (severity × settlement-proximity) + collection-gap (recent high-stakes
  single-source). Both surface in `/insights`.

**Phase 5 (breadth) — 5a done; 5b substantially done.**
- **5a** own-curated control overlay (who held what, over time), scrubber-synced.
- **5b multi-theater + maritime** — **Black Sea theater** built (Crimea + Sevastopol + Kerch, 91k-
  cell grid, admin, UCDP, vessels); **SAR vessel detector** built + validated (Hormuz 57, Sevastopol
  254 on the fleet anchorage); **theater switcher** in the UI; Hormuz config ready (just run the
  detector); Delhi config exists, not loaded.

**Cross-cutting (beyond the strict roadmap), all this session:**
- **Continuous chronology** — UCDP GED 2022–2026, daily granularity.
- **Admin drill-down** — geoBoundaries oblast→raion→hromada; `/rollup` choropleth + breadcrumb.
- **Named areas of interest** — `world.area_of_interest` + `aoi_cell`; `/aois` CRUD; the "Watch
  areas" UI (draw / lasso / promote-a-feature → cell-set → events). *This is the AOR foundation.*
- **Published recruiter demo** — jargon-free README; static multi-theater snapshot on GitHub Pages;
  per-event provenance; per-theater gazetteers.

---

## 3. Roadmap status (PRD v1.1)

| Pass | Objective | Status |
|---|---|---|
| 0–2 | Event-sourcing spine + fusion + confidence | ✅ done |
| 3a–3f | Imagery acquisition + classical SAR + optical detectors | ✅ done (validated) |
| 3g / 3h | Deep-CV train / inference | ⬜ not started (side quest at $0; classical baseline holds) |
| 3i | UI imagery layer + provenance | 🟧 partial (provenance done; dedicated change layer not) |
| 3j | Before/after + human-in-the-loop | ⬜ not started |
| 3k | Extended substrate (soil/utility/installation) | 🟧 partial (admin + OSM features done; rest NULL) |
| 4a | Significance + anomaly | ✅ done |
| 4b | Mobility + flood assessments | ⬜ not started (flood now cheap — optical detector + hydro) |
| 4c | Exposure + collection-gap | ✅ done (2026-07-01: exposure now uses real WorldCover `builtup_pct`, not just settlement-distance proxy) |
| 4d | Entity population (installation/vessel/site) | 🟧 partial (vessels detected as events; `world.entity` empty; AOI model built) |
| 5a | Control overlay | ✅ done |
| 5b | Multi-theater + Hormuz SAR vessel | 🟩 substantially done (Black Sea live; vessel detector validated; Hormuz one run away) |
| 5c | React/MapLibre UI + deploy decision | ⬜ not started (HTML UI is the interim; published static demo serves the deploy need) |
| 6a | RAG "ask the COP" | ✅ SHIPPED as the per-area synthesis **Read** (local-LLM prose + deterministic fallback, cached in world.area_read) — not a chatbot, by design (see §4) |
| 6b | Analyst-query UI | ✅ shipped as the AOR-first **"My watch"** home (attention-ranked areas + Read + provenance) |

---

## 4. Synthesis layer + AOR-first UI redesign — ✅ SHIPPED (2026-07-01)

Built in three phases + published to the live demo:
- **Phase 1 (pure core):** `assess/attention.py` (escalating/steady/quieting classifier),
  `synth/context.py` (grounded, coord-free context), `synth/read.py` (deterministic + LLM Read).
- **Phase 2 (API/DB):** `world.area_read` cache (migration 0013), `gather_area_context` /
  `get_cached_read` / `upsert_read`, `GET /watch` (attention-ranked feed) + `GET /aois/{id}/read`,
  region-watch via `admin_id`, `synth/run.py` runner.
- **Phase 3 (UI):** **"My watch"** is the default tab — areas ranked by attention, each with its
  Read snippet + "seen by" provenance; the area focus shows the **✦ Intelligence read** panel;
  legend collapsed off the map.
- **Phase 4 (publish):** local-LLM (qwen2.5:14b) Reads regenerated + cached; static export wired for
  `/watch` + `/read`; multi-theater snapshot republished to gh-pages. Verified end-to-end in static
  mode. **Bug fixed:** `OllamaClient.generate` hard-coded the Verdict JSON schema, so the Read came
  back as adjudicator JSON — split into `generate()` (constrained) + `generate_text()` (free-form).
- **Honest note:** both demo AOIs read **"quieting"** — real (activity peaked 2022–24, the recent
  tail is sparse), not a bug. An actively-spiking area would read "escalating" and lead the feed.
- **Not yet done:** the full map-first *visual* shell (slim icon rail / floating cards) — the AOR
  *workflow* + synthesis ship now on the existing tab shell; the visual rebuild (5c, React/MapLibre)
  remains the open polish item.

### Original design decision (for reference)

**Synthesis layer + AOR-first UI redesign.** Decided via brainstorm:
- **Workflow = watch-list / AOR-first.** The home screen is **"My Watch"**: the areas you're
  responsible for, ranked by an **attention signal** (escalating / steady / quieting, from the
  anomaly + significance engine scoped per area), each with a one-line synthesis **Read**. Map
  scoped to the AOR. **Explore** is where you find + "add to watch"; opening an area is the
  investigate view. Watchable = **areas (AOIs) + whole regions** now; entities later.
- **Synthesis layer** = a per-area **Read** (`/read?area=`): a grounded, local-LLM "recent activity
  + indicators" read (honest: indicators from the data's anomaly/trend, not prophecy), with
  provenance. Powers the My-Watch attention ranking + the focus view. This is PRD **6a** scoped to
  area-narrative (not a freeform chatbot — that was deliberately left out).
- **Shell** = map-first command center (full-bleed map hero, slim left rail, floating cards, corner
  legend) — replacing the clunky 7-tab panel/map split.
- Spec next: `docs/superpowers/specs/2026-06-30-synthesis-aor-redesign-design.md`.

---

## 5. Geography substrate + area-spine — ✅ SHIPPED (2026-07-01)

User: "I like the By-area tab and its drill-down, want it as the anchor; recency isn't reflected
well; can we do structure/geography detection (farmland, forest, dirt road)?" Spec =
`docs/superpowers/specs/2026-07-01-geography-area-spine-design.md`, plan =
`docs/superpowers/specs/2026-07-01-geography-area-spine.md`. Built on branch `feat/geo-area-spine`,
fast-forward merged to `main` (commit `79c79b4`).

- **Phase A (geography substrate):** populated `geo.cell_context` for BOTH theaters — ESA WorldCover
  land cover (dominant class + `builtup_pct`) and OSM roads incl. a new `road_surface` (paved/
  unpaved/dirt, from the `surface` tag + `track`/`path` defaults). **Caught 2 real bugs in the
  process:** (1) the ua_donbas WorldCover tile names were invalid from Phase 0 onward (`N45E034`
  etc. — WorldCover 3° tiles must be multiples of 3; correct tiles are `N45E036/E039/N48E036/E039`)
  — land cover had *never* actually loaded for ua_donbas; (2) `load_transport`'s per-cell `intersects`
  scan over every Ukraine road was O(cells×roads) and would never finish on the full PBF — fixed with
  a bbox pre-filter + `shapely.strtree.STRtree` (71k/91k cells populated in ~73s/theater). Activated
  the land-cover plausibility gate that was wired since Phase 3 but no-op on empty data (naval
  events implausible on cropland, damage implausible over water) — **refined mid-build**: the naive
  "must be water" rule wrongly penalized the real Sevastopol fleet anchorage (a 1km harbor cell is
  WorldCover `built-up`, not `water`); changed to "penalize only clearly-inland cover." Eval gate
  digest unchanged (`bf11cd6b…`) — the synthetic corpus is land-cover-agnostic by design. Events now
  carry a geography phrase ("on cropland · along an unpaved track"); exposure scoring uses real
  `builtup_pct` when populated, falling back to the settlement-distance proxy.
- **Phase B (unified area-ref + recency fix):** one abstraction — `admin:<id>` or `aoi:<id>` — so
  AOIs and admin units (oblast/raion/hromada) share the synthesis Read/attention/terrain engines.
  New **`GET /area/{ref}`** returns Read + attention + terrain profile + **recent activity** (its own
  surface, last 30 days) + **significant incidents** (separate, all-time) + **attention-ranked child
  areas** (the drill-down). **Recency fix, two parts:** separating recent/significant into distinct
  lists (recency no longer competes inside one score), plus re-tuning `recency_floor` 0.5→0.3 and
  `recency_tau_days` 14→10. **Caught a real regression while verifying:** the multiplicative floor
  drop compressed every event's score below the old absolute storage gate (`min_significance: 0.15`),
  so only ~4 events cleared it — the feed lost depth in one theater and went to ZERO in the other
  (sparse Crimea). Fixed properly: `assess.run` now stores the **top-N by score** above a low noise
  floor (mirroring how exposure/gaps already worked), which is robust to future score-scale shifts.
  **By-area tab is now the place-view spine** — clicking any unit opens its Read, terrain chips,
  geo-tagged recent activity, significant incidents, and attention-ranked children to drill further.
- **Verified live** (Claude_Preview): Donetsk Oblast → 67% cropland/54% unpaved terrain, Read, 18
  children with **Dobropillia "▲ Escalating" leading** → drilled to its 13 hromadas. Static demo
  re-exported (1470 files, incl. `/area` snapshots for every populated admin unit + AOI) and
  republished — verified serving live at agambear25.github.io/situational-picture/.
- **166 tests pass, eval gate exits 0.** Deferred (Phase C, not this pass): the full shell collapse
  (retire tabs, fold Events/My-watch into the place view, map-first rail) — the *workflow* ships on
  the existing tab shell; the visual rebuild is still PRD 5c.

## 6. After this — recommended order

1. **4d entity population** — promote vessels + key features to `world.entity`; lets you *watch an
   entity*, completing the AOR story.
2. **4b flood assessment** — cheap now (optical flood detector + hydro substrate).
3. **Hormuz theater** — config exists, detector proven; one acquisition + ingest run.
4. **3j before/after HITL** — pairs with the investigate view.
5. **Phase C shell collapse** — retire tabs, promote the By-area place view to the whole shell,
   fold My-watch/Events in, map-first rail (PRD 5c).
6. **3g/3h deep CV** — deferred; classical detectors hold.
