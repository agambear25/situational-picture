# Situational Picture

### ▶ [**Open the live demo →**](https://agambear25.github.io/situational-picture/)
*A real, clickable snapshot of the dashboard — runs entirely in your browser, no install.*

---

**An open-source intelligence (OSINT) tool that turns scattered public reports into one
trustworthy map of a conflict — built to run entirely on a laptop, for free.**

It reads free public data — conflict-event databases, NASA fire/heat satellites, and
Sentinel-1/2 imagery — works out which reports describe the *same* real-world incident, merges them
into a single map of events, and rates each one by how many **independent** sources actually saw it.
Think of a newsroom wire desk with a built-in fact-checker: many overlapping, noisy reports in, one
de-duplicated, confidence-rated picture out.

It is **decision-support, not targeting** — that line is enforced in the design, not just promised
(see [Responsible by design](#responsible-by-design)).

> Theater 1 is the Donbas region of Ukraine, 2022–present. The whole system is region-agnostic — a
> config swap points it at a different place.

---

## What it does

- **A continuous timeline of the war.** ~5,500 events across every month from February 2022 to
  2026, drawn from ~22,000 real conflict records plus fire and satellite data. Scrub a slider and
  watch the front move.
- **"What matters" first.** Instead of a flat list, the board ranks events by how *severe*,
  *confirmed*, *recent*, and *unusual* they are — so the handful of confirmed, high-stakes incidents
  rise above thousands of routine reports.
- **Confidence you can trust.** An event is only "Confirmed" when **independent** kinds of sources
  agree on it — a news report *and* a satellite detection *and* a heat signature in the same place.
  Ten copies of one wire story add nothing; one genuinely independent second source does.
- **Drill down by area.** Region → district → community, each shaded by how much is happening there,
  down to the individual events.
- **Mark areas you care about.** Draw a box or trace a feature (a river line, a road corridor) on
  the map, name it, and the tool instantly pulls everything that has happened inside it.
- **Four kinds of sensors.** Text (conflict databases), thermal (NASA fire satellites), radar
  (Sentinel-1, works through cloud and at night), and optical (Sentinel-2, for floods and burn
  scars) — the more independent sensor types that agree, the higher the confidence.
- **Honest about uncertainty.** Every map dot shows its confidence level; nothing is silently
  dropped; sources behind each event are always one click away.

## Screenshots

The operator board has four main views — **What matters** (the ranked feed), **By area** (the
region drill-down), **Watch areas** (your marked areas of interest), and **Events** (the searchable
timeline) — over a satellite basemap with a who-controlled-what overlay.

**▶ [Try them live in the browser →](https://agambear25.github.io/situational-picture/)** — the
demo is a static snapshot of real board data, so the map, the rankings, and the region drill-down
all work with nothing to install.

## The interesting problems it solves

This is less a data pipeline than a small intelligence engine. The hard parts:

**1. Deciding what's the same incident.** The same artillery strike might show up as a news report,
a database entry, and a satellite change — at slightly different coordinates and times. The engine
groups candidate reports by place, time, and type, then scores each pair on *how* similar they are
before merging. It deliberately starts by over-grouping and then splits back apart, because **a
missed event is worse than a duplicate.**

**2. Rating confidence honestly.** Confidence is computed so that **independent** sources compound
but **echoes** of one source don't. A satellite detection of building damage in the same place as a
news report of a strike lifts the event's confidence; a re-tweet of that news report does not.

**3. Staying reproducible with AI in the loop.** A small **local** language model (no cloud, no API
keys, $0) breaks ties on the genuinely ambiguous cases. Its verdicts are cached and version-stamped
so the whole map can be **rebuilt identically from the raw records at any time** — the AI never
becomes an unauditable black box.

**4. Reading satellite imagery, locally.** Radar and optical change detectors run on the laptop
(the cloud is only a free data tap). The radar damage detector was validated against UN satellite
damage assessments of Mariupol; the optical burn detector was validated against the 2022 Sviati
Hory forest fires — 33 burn detections, zero false floods.

**5. Privacy and ethics built into the data model** — see below.

## Responsible by design

- **Locations are rounded to ~1km cells before anything is stored.** Precise coordinates are
  discarded at the door. The tool can say "a strike near Avdiivka," never a targetable point.
- **No people.** The data model structurally cannot store a person as an entity — it's a database
  constraint, not a guideline.
- **Decision-support, not targeting** is enforced at the boundary where data leaves the system.
- **License-clean.** It never ingests proprietary frontline-map data; the "who controlled what"
  overlay is hand-authored from public knowledge and clearly labelled as approximate.

## How it's built

| Layer | Technology |
|---|---|
| Data store | PostgreSQL + PostGIS (geospatial) |
| Backend | Python, FastAPI (read-only API) |
| Imagery | Google Earth Engine (free tier) as a data tap; detection runs locally with NumPy/SciPy |
| Tie-breaking AI | Local LLM via Ollama (Qwen 2.5), $0 / offline |
| Map UI | Vanilla JavaScript + Leaflet (no build step) |
| Geography | OpenStreetMap + geoBoundaries (admin regions), MGRS 1km grid |

The engine is **deterministic and replayable**: the same raw records always rebuild the same map,
which is verified by an automated test that re-runs the whole pipeline and checks the result is
bit-for-bit identical. ~130 tests gate every change.

## Data sources (all free, all public)

UCDP (conflict events, CC-BY) · NASA FIRMS (fire/thermal) · Copernicus Sentinel-1 & Sentinel-2
(satellite imagery) · UNOSAT (damage assessments, used as ground truth) · OpenStreetMap +
geoBoundaries (geography).

## Running it

Needs PostgreSQL 17 + PostGIS, Python 3.11+, and (optionally) Ollama for the tie-breaking AI.

```bash
# 1. Database
createdb osint_cop && psql osint_cop -c "CREATE EXTENSION postgis; CREATE EXTENSION vector;"
psql osint_cop -f db/migrations/0001_extensions.sql   # ... through the latest migration
psql osint_cop -f db/roles.sql

# 2. Python
python -m venv .venv && .venv/bin/pip install -e ".[full]"

# 3. Build the 1km grid + load the geography/admin substrate
.venv/bin/python -m grid.cli build --theater ua_donbas
bash scripts/fetch_admin.sh        # region/district/community boundaries
bash scripts/fetch_ucdp_donbas.sh  # the 2022–2026 conflict chronology

# 4. Turn the raw records into the map, then score what matters
.venv/bin/python -m fusion.run  --theater ua_donbas
.venv/bin/python -m assess.run  --theater ua_donbas

# 5. Serve the board at http://127.0.0.1:8000/ui/
.venv/bin/uvicorn api.main:app --port 8000
```

Run the test suite and the determinism check with `pytest -q` and `python -m eval.harness`.

## Honest limitations

- **One curated theater.** The pipeline is region-agnostic, but only the Donbas is fully loaded.
- **Multi-tempo, not real-time.** Conflict databases lag by weeks; satellite imagery is per-overpass
  and cloud-limited; only thermal data is near-live. The board reflects that mix honestly.
- **Classical detectors, for now.** The satellite detectors are deterministic baselines; a deep-
  learning change model is designed-for but not the current default.
- **The control overlay is illustrative** — city-level and approximate, authored from public
  knowledge, explicitly *not* a live frontline.

## Project layout

```
ingest/    read public feeds + run the imagery detectors → an append-only record log
fusion/    group, score, and merge records into confidence-rated events (the engine)
assess/    rank what matters + flag anomalies, exposure, and collection gaps
api/        + web/   read-only API and the operator map UI
geo/ grid/  the 1km grid, admin regions, and geography substrate
eval/      the determinism gate + the satellite-detector validators
docs/superpowers/specs/   design write-ups and the build checkpoint
```

---

*Built as a portfolio project to explore how far a rigorous, honest, $0/local intelligence tool can
go on public data alone. Not affiliated with any government or vendor.*
