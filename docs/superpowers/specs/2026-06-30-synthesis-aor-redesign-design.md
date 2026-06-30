# Synthesis layer + AOR-first UI redesign — design

The sophistication + workflow leap: turn the board from a data browser into an analyst's
**watch-list (Area-of-Responsibility) monitoring loop**, with a local-LLM **synthesis Read** per
area. Maps to PRD 6a (scoped to area-narrative, not a freeform chatbot) + a map-first UI rebuild.
Read-only over the deterministic spine; $0 / local / Claude-OFF holds.

Decisions locked in brainstorming: north star = **clean + powerful** (progressive disclosure);
workflow = **AOR-first**; synthesis = **per-area indicators Read** (honest: indicators from the
data, not prophecy); shell = **map-first command center**; watchable = **areas + regions** now,
entities later.

---

## 1. Workflow (the job)

Monitor → Investigate → Decide → (areas you keep drive the next look). The product answers
*"what should I — responsible for these places — pay attention to?"*

- **My Watch (home).** Your watched areas, ranked by an **attention signal**
  (escalating / steady / quieting), each with a one-line synthesis Read. Map scoped to your AOR.
- **Explore.** The full theater (map + region drill-down + events) — to *find* areas and
  **add them to your watch**.
- **Investigate (focus).** Open a watched area → events, evidence + provenance, trend, full Read.

---

## 2. Architecture (units, each independently testable)

**A. The watch list (AOR) — reuse `world.area_of_interest`.** Watched areas ARE AOIs. "Watch a
region" = create an AOI whose cell-set is the admin unit's cells (kind `region`,
`source='derived'`, `source_admin_id` in meta). No new table; one new creation path. The AOR is
"all AOIs for the theater" (optionally a `watched` boolean later; for now every AOI is watched).

**B. Attention classifier — pure (`assess/attention.py`).** Per area, from its cells' events +
the assessment rows scoped to those cells: classify `escalating | steady | quieting` + a score.
Inputs: recent-window event count vs the prior baseline (trend), presence of an `activity_spike` /
`escalation` anomaly in the area, count of confirmed (multi-family) events. Deterministic; unit-
tested. No LLM. This drives the My-Watch ranking and the "N need attention" header.

**C. Read context-builder — pure (`synth/context.py`).** Per area, assemble a compact structured
context: event counts by type + band, recent vs prior trend, top-N events (place + type + date +
confidence), the area's anomalies, the distinct sensor families (provenance), the date span. Pure,
unit-tested — this is what the LLM reasons over (and what the deterministic fallback formats).

**D. Read generator — `synth/read.py`.** Given the context (C), produce `{summary, indicators,
provenance}`:
- **Primary:** local LLM (Ollama, the existing 7B/14B) with a tight grounded prompt — 2–3 sentence
  summary + an `indicators` label echoing B's classification, framed as *indicators from the data*.
- **Fallback (Ollama off / timeout):** a deterministic template that formats the context into a
  readable read ("993 events; activity up ~3× this month; escalating; seen by radar + news"). So
  the UI ALWAYS has a Read — never blocks (mirrors the fusion adjudicator's keep-separate rule).
- **Cache:** `world.area_read` (aoi_id, summary, indicators, provenance, input_hash, generated_at).
  Regenerate only when the input context hash changes. Lets the static demo pre-generate + export.

**E. API.**
- `GET /watch?theater_id=` — the AOR: each area + its attention (B) + a Read snippet (D, cached).
- `GET /aois/{id}/read` — the full Read (D); generates+caches on miss.
- `POST /aois` — extended: `source='region'` + `admin_id` creates an AOI from a region's cells.
- (existing `/aois`, `/events?aoi=`, `/rollup`, `/insights`, `/theaters` stay.)

**F. UI shell — map-first command center** (`web/`). Full-bleed map hero; slim left rail
(My Watch / Explore / More); floating cards; legend → corner toggle. Three surfaces:
- **My Watch** — attention-ranked area cards (B + D snippet); map highlights the AOR, dims the rest;
  empty-state onboarding ("add your first area").
- **Explore** — map + region drill-down (`/rollup`) + events + the what-matters feed; every
  region/area gets an **"add to watch"** affordance.
- **Investigate** — area focus: events (`/events?aoi=`) + provenance + trend + the full Read.
- Theater switcher, time scrubber, control overlay, provenance all carried over.

---

## 3. Data flow

My Watch → `GET /watch` → cards (attention from B, snippet from cached D) + map highlight. ·
Open an area → `GET /aois/{id}/read` (full Read) + `GET /events?aoi=`. · Explore → `/rollup` +
`/events`; "add to watch" → `POST /aois` (drawn) or region-AOI. · Read generation reads only the
read model + assessments (never the evidence log writes); writes only the `area_read` cache.

## 4. Error handling / constraints

- **Ollama off** → deterministic Read fallback; UI never blocks.
- **Empty AOR** → onboarding state, not an error.
- **Coarsening** — the Read is built from cell-level events + family-level provenance; no precise
  coord or person ever enters the context or the output (assert at the context-builder).
- **Honesty** — `indicators` is labelled as derived from the data's anomaly/trend, not a forecast
  model; the UI copy says "indicators", never "prediction".

## 5. Testing

- Pure + unit-tested: attention classifier (B), context-builder (C) incl. the no-precise-coord
  assertion, the deterministic Read fallback (D).
- Read generator with the LLM: contract-verified with a mocked backend (like the fusion adjudicator
  tests); a frozen-context test asserts the fallback output.
- API: contract-verified live (DB-bound), like the rest of the API layer.
- Gate: the fusion spine is untouched → `eval.harness` stays 0; offline test count goes up.

## 6. Build order

1. **B + C + D-fallback** — pure attention + context + template Read, with tests. (No LLM yet.)
2. **D-LLM + cache + `world.area_read` migration + `/watch` + `/aois/{id}/read` + region-AOI.**
3. **UI redesign** — the map-first shell + My Watch + Explore + Investigate + Read panel. (Biggest.)
4. **Demo** — pre-generate Reads, re-export the multi-theater static snapshot, republish.

## 7. Scope / honesty notes

- Synthesis is **area-narrative + indicators**, not a freeform Q&A chatbot (deliberately out).
- The LLM Read is slow per call (~10–55s on 16GB) → caching + pre-generation are load-bearing, not
  optional. 7B is the workhorse; 14B optional for the focus-view Read.
- The UI redesign is the largest piece and is mostly frontend; the spine/API stay stable.
- Out of scope (later): watching *entities*, external commentary (Reddit/YT/Substack), 5c React.
