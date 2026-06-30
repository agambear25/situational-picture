# OSINT COP — Build Checkpoint vs PRD (2026-06-30, refreshed)

Where the build stands vs the PRD (v1.1) roadmap, after a long build session. Use it to see the
roadmap at a glance and decide sequencing. The next build (synthesis layer + AOR-first UI redesign)
is designed and about to start — see §4.

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
| 4c | Exposure + collection-gap | ✅ done |
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

## 5. After this — recommended order

1. **(in progress) Synthesis + AOR redesign** — the meaningful-workflow + sophistication leap.
2. **4d entity population** — promote vessels + key features to `world.entity`; lets you *watch an
   entity*, completing the AOR story.
3. **4b flood assessment** — cheap now (optical flood detector + hydro substrate).
4. **Hormuz theater** — config exists, detector proven; one acquisition + ingest run.
5. **3j before/after HITL** — pairs with the investigate view.
6. **3g/3h deep CV** / **5c React** — deferred; classical detectors + the HTML/static demo hold.
