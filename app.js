/* ============================================================================
   Situational Picture — front-end logic (vanilla JS, no build step).
   Talks to the read-only FastAPI on the same origin. All geometry it receives
   is already rounded to 1km cells by the API; this file never sees raw coords.
   Structure: tiny API helper -> map -> four tab renderers (events/review/add/
   health) -> event detail. Plain language only; see styles.css for the design.
   ============================================================================ */

const API = "";                       // same origin (served by the API at /ui/)
let THEATER = "ua_donbas";
let THEATERS = [];          // [{theater_id, label, bbox, n_events}] — populated from /theaters
let map, eventLayer, selectedLayer, controlLayer, featureLayer, aoiLayer, drawHandler;
const T_MIN = Date.UTC(2022, 1, 24);  // 2022-02-24, the full-scale invasion — chronology start
let UNTIL = null;                      // null = live (everything); else "YYYY-MM-DD" cumulative cut-off
// Coarse, own-curated control overlay (license-clean — authored from public knowledge, never ISW/DSM).
const CONTROL = {
  RU: { color: "#c0392b", label: "Russian-held" },
  UA: { color: "#2e6fd0", label: "Ukrainian-held" },
  contested: { color: "#b8862f", label: "Contested" },
};
let SHOW_CONTROL = true;

// Plain confidence labels + colours mirror the styles.css --band-* tokens (single source of truth).
const BANDS = {
  High:    { cls: "high",    color: "#ff5a52", plain: "Confirmed",        meaning: "confirmed by several independent sources" },
  Medium:  { cls: "medium",  color: "#ffb13d", plain: "Partly confirmed", meaning: "partly confirmed" },
  Low:     { cls: "low",     color: "#4ea3ff", plain: "Weak",             meaning: "weak signal" },
  Rumored: { cls: "rumored", color: "#8b93a4", plain: "Rumored",          meaning: "single source / not yet confirmed" },
};
// Classify a report's source family for the colour-coded evidence timeline.
function srcKind(o) {
  const m = (o.modality || "").toLowerCase(), f = (o.source_family_id || "").toLowerCase();
  if (f.includes("optical")) return { k: "sat", label: "Satellite optical" };
  if (f.includes("sar") || f.includes("sentinel1")) return { k: "sat", label: "Satellite radar" };
  if (f.includes("unosat")) return { k: "sat", label: "Damage assessment" };
  if (m === "thermal" || f.includes("firms") || f.includes("modis")) return { k: "thermal", label: "Thermal / fire" };
  if (m === "imagery" || f.includes("copernicus") || f.includes("sentinel")) return { k: "sat", label: "Satellite imagery" };
  return { k: "news", label: "News / report" };
}
const UNCORROBORATED = ["single-source", "echo-only", "verification-needed"];
const FLAG_PLAIN = {
  "single-source": "Only one source — not independently confirmed.",
  "echo-only": "Looks like the same report repeated, not separate confirmation.",
  "verification-needed": "Needs a person to check before relying on it.",
};
// Friendly label -> the event type the backend expects (kept to valid taxonomy types).
const EVENT_TYPES = {
  "Air / missile strike": "strike",
  "Airstrike": "airstrike",
  "Artillery / shelling": "artillery_fire",
  "Explosion": "explosion",
  "Building damaged / destroyed": "building_damaged",
  "Bridge damaged / destroyed": "bridge_damaged",
  "Fire": "fire",
  "Flood": "flood",
};
const CONFIDENCE = {
  "Very well confirmed — several independent sources": "High",
  "Fairly confirmed — a couple of sources": "Medium",
  "Weak — one source with a little support": "Low",
  "Unconfirmed — a single report": "Rumored",
};

/* ---- tiny API helper (live API, or pre-exported JSON in the static GitHub Pages demo) ---- */
// Static demo: no backend — GET calls read ./data/*.json snapshots, writes are no-ops.
const STATIC_MODE = location.hostname.endsWith("github.io") || location.protocol === "file:" ||
  location.search.includes("static") || window.STATIC_DEMO === true;
function _staticFile(path) {
  const [p, qs] = path.split("?");
  const q = new URLSearchParams(qs || "");
  // Global (not theater-scoped): single files + by-id detail (ids are globally unique).
  if (p === "/healthz") return "data/healthz.json";
  if (p === "/theaters") return "data/theaters.json";
  if (p.startsWith("/events/")) return `data/event/${p.split("/")[2]}.json`;
  if (p.startsWith("/aois/") && p.endsWith("/read")) return `data/aoi/${p.split("/")[2]}-read.json`;
  if (p.startsWith("/aois/")) return `data/aoi/${p.split("/")[2]}.json`;
  if (p.startsWith("/cells/")) return `data/cell/${encodeURIComponent(p.split("/")[2])}.json`;
  // Theater-scoped views live under data/<theater>/…
  const b = `data/${q.get("theater_id") || THEATER || "ua_donbas"}`;
  if (p.startsWith("/area/")) {           // /area/admin:<id> | /area/aoi:<id>  (colon → '-' in files)
    let ref = p.slice(6);
    if (ref.endsWith("/read")) return `${b}/area/${ref.slice(0, -5).replace(":", "-")}-read.json`;
    return `${b}/area/${ref.replace(":", "-")}.json`;
  }
  if (p === "/watch") return `${b}/watch.json`;
  if (p === "/insights") return `${b}/insights.json`;
  if (p === "/control") return `${b}/control.json`;
  if (p === "/aois") return `${b}/aois.json`;
  if (p === "/verify-queue") return `${b}/verify-queue.json`;
  if (p === "/rejections") return `${b}/rejections.json`;
  if (p === "/rollup") { const l = q.get("level") || "1", par = q.get("parent");
    return par ? `${b}/rollup/l${l}-${par}.json` : `${b}/rollup/l${l}.json`; }
  if (p === "/features") return `${b}/features/${q.get("kind")}.json`;
  if (p === "/events") { const aoi = q.get("aoi");
    return aoi ? `${b}/events/aoi-${aoi}.json` : `${b}/events.json`; }
  return null;
}
async function api(path, opts) {
  if (STATIC_MODE) {
    if (opts && opts.method && opts.method !== "GET") { toast("This is a read-only demo snapshot.", true); return null; }
    const f = _staticFile(path);
    if (!f) return null;
    const r = await fetch(f);
    return r.ok ? r.json() : null;
  }
  const r = await fetch(API + path, opts);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
function analyst() { return (document.getElementById("analyst").value || "analyst").trim(); }
function bandOf(ev) {
  const flags = ev.flags || [];
  if (flags.some((f) => UNCORROBORATED.includes(f))) return "Rumored";
  return BANDS[ev.confidence_band] ? ev.confidence_band : "Rumored";
}
function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3200);
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* ---- map ---- */
function initMap() {
  map = L.map("map", { zoomControl: true, attributionControl: true }).setView([48.2, 37.8], 7);
  // Keyless basemaps. Satellite is the default (asked for — you can see what's actually there);
  // a switcher (top-right) flips to terrain or a clean dark map.
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "&copy; Esri, Maxar, Earthstar Geographics", maxZoom: 19 });
  const terrain = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    { attribution: "&copy; Esri", maxZoom: 19 });
  const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { attribution: "&copy; OpenStreetMap &copy; CARTO", subdomains: "abcd", maxZoom: 19 });
  satellite.addTo(map);
  L.control.layers({ "Satellite": satellite, "Terrain": terrain, "Dark": dark }, null,
    { position: "topright", collapsed: true }).addTo(map);
  controlLayer = L.layerGroup().addTo(map);   // control tint sits UNDER the events
  featureLayer = L.layerGroup().addTo(map);   // OSM reference geography (rivers/roads/forests)
  eventLayer = L.layerGroup().addTo(map);
  aoiLayer = L.layerGroup().addTo(map);        // the selected / drawn area of interest (on top)
  if (window.L && L.Draw) map.on(L.Draw.Event.CREATED, onAoiDrawn);
  renderLegend();
}
async function loadControl(date) {
  if (!controlLayer) return;
  controlLayer.clearLayers();
  if (!SHOW_CONTROL) return;
  let data;
  try { data = await api(`/control${date ? `?date=${date}` : ""}`); } catch (e) { return; }
  for (const s of (data.settlements || [])) {
    const c = CONTROL[s.side];
    if (!c) continue;
    L.circle([s.lat, s.lon], { radius: 11000, color: c.color, weight: 1, opacity: 0.5,
      fillColor: c.color, fillOpacity: 0.16, interactive: false })
      .bindTooltip(`${esc(s.name)} — ${c.label}`, { sticky: true }).addTo(controlLayer);
  }
}
function renderLegend() {
  document.getElementById("legend").innerHTML =
    `<details><summary>Legend</summary>` +
    `<h4>How confident we are</h4>` +
    Object.entries(BANDS).map(([b, d]) =>
      `<div class="row"><span class="dot" style="background:${d.color}"></span><b>${d.plain}</b> — ${d.meaning}</div>`).join("") +
    `<h4 style="margin-top:10px">Who controlled the area</h4>` +
    Object.entries(CONTROL).map(([k, c]) =>
      `<div class="row"><span class="dot" style="background:${c.color};opacity:.65"></span>${c.label}</div>`).join("") +
    `<div class="legend-caveat">Control is coarse &amp; illustrative — city-level, approximate dates, authored from public knowledge. Not a live frontline.</div>` +
    `</details>`;
}
function drawEvents(events) {
  eventLayer.clearLayers();
  for (const ev of events) {
    const band = bandOf(ev);
    const color = BANDS[band].color;
    if (ev.geometry && ev.geometry.type === "Polygon") {
      L.geoJSON(ev.geometry, {
        style: { color, weight: 1, fillColor: color, fillOpacity: 0.4 },
      }).on("click", () => selectEvent(ev.event_id)).addTo(eventLayer);
    }
    if (ev.centroid && ev.centroid.coordinates) {
      const [lon, lat] = ev.centroid.coordinates;
      L.circleMarker([lat, lon], { radius: 6, color, fillColor: color, fillOpacity: 0.7 })
        .on("click", () => selectEvent(ev.event_id)).addTo(eventLayer);
      // Named place label on the map for corroborated (non-Rumored) events — the map upgrade.
      if (band !== "Rumored" && ev.place && ev.place.label) {
        L.marker([lat, lon], { interactive: false,
          icon: L.divIcon({ className: "place-label", html: esc(ev.place.label), iconAnchor: [-12, 8] }) })
          .addTo(eventLayer);
      }
    }
  }
  const chip = document.getElementById("mapChipN");
  if (chip) chip.textContent = events.length;
}
function setSummary(events) {
  const counts = { total: events.length, High: 0, Medium: 0, Low: 0, Rumored: 0 };
  for (const ev of events) counts[bandOf(ev)]++;
  for (const [k, v] of Object.entries(counts)) {
    const el = document.querySelector(`[data-count="${k}"]`);
    if (el) el.textContent = v;
  }
}
function focusEvent(ev) {
  if (selectedLayer) { map.removeLayer(selectedLayer); selectedLayer = null; }
  const c = ev.centroid && ev.centroid.coordinates;
  if (c) {
    selectedLayer = L.circleMarker([c[1], c[0]], { radius: 13, color: "#fff", weight: 2, fill: false }).addTo(map);
    map.setView([c[1], c[0]], Math.max(map.getZoom(), 11));
  }
}

/* ---- tabs ---- */
const TABS = { insights: renderInsights, areas: renderAreas, watch: renderWatchAreas, events: renderEvents, review: renderReview, add: renderAdd, health: renderHealth };
function setTab(tab) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  (TABS[tab] || renderEvents)();
}

/* ---- What matters (the Phase-4a assessment feed) ---- */
async function renderInsights() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="hint">The system's read on <b>what to look at first</b> — ranked by how
    severe, confirmed, recent and unusual each event is.</div><div id="insights" class="empty">Loading…</div>`;
  let data;
  try { data = await api(`/insights?theater_id=${THEATER}`); }
  catch (e) { document.getElementById("insights").innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const sig = (data && data.significant) || [], anom = (data && data.anomalies) || [];
  if (!data || !data.available) {
    document.getElementById("insights").textContent = "No assessments yet — run the assessment engine (assess.run).";
    return;
  }
  drawInsightMarkers(sig, anom);
  document.getElementById("insights").outerHTML = `
    <div id="insights">
      <div class="section-title">Priorities <span class="muted">· top ${sig.length}</span></div>
      ${sig.length ? sig.map(insightCard).join("") : `<div class="muted">Nothing scored.</div>`}
      <div class="section-title" style="margin-top:14px">Unusual activity</div>
      <div class="hint">Cells with a recent flare-up or a jump to a more severe kind of event.</div>
      ${anom.length ? anom.map(anomCard).join("") : `<div class="muted">No anomalies flagged.</div>`}
    </div>`;
  panel.querySelectorAll(".sig-card").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
  panel.querySelectorAll(".anom-card").forEach((c) => (c.onclick = () => focusCell(c.dataset.lat, c.dataset.lon)));
}
function insightCard(x, i) {
  const band = BANDS[x.confidence_band] ? x.confidence_band : "Rumored";
  const place = (x.place && x.place.label) || ("area " + x.cell_id);
  const pct = Math.max(4, Math.round(x.score * 100));
  return `<div class="sig-card" data-id="${esc(x.event_id)}" data-band="${band}">
    <div class="sig-top">
      <span class="sig-rank">${i + 1}</span>
      <span class="sig-place">${esc(place)}<span class="sig-type"> · ${esc(String(x.event_type).replace(/_/g, " "))}</span></span>
      <span class="pill ${BANDS[band].cls}">${BANDS[band].plain}</span>
    </div>
    <div class="sig-bar" title="priority ${pct}%"><span style="width:${pct}%;background:${BANDS[band].color}"></span></div>
    <div class="sig-why">${esc(x.rationale)}</div>
  </div>`;
}
function anomCard(a) {
  const place = (a.place && a.place.label) || ("area " + a.cell_id);
  const esc8 = a.subkind === "escalation";
  const c = a.centroid && a.centroid.coordinates;
  return `<div class="anom-card" data-cell="${esc(a.cell_id)}"${c ? ` data-lat="${c[1]}" data-lon="${c[0]}"` : ""}>
    <div class="anom-top">
      <span class="anom-badge ${esc8 ? "esc" : "spike"}">${esc8 ? "▲ Escalation" : "◉ Flare-up"}</span>
      <span class="anom-place">${esc(place)}</span>
    </div>
    <div class="sig-why">${esc(a.rationale)}</div>
  </div>`;
}
function drawInsightMarkers(sig, anom) {
  eventLayer.clearLayers();
  sig.forEach((x, i) => {
    const c = x.centroid && x.centroid.coordinates;
    if (!c) return;
    const band = BANDS[x.confidence_band] ? x.confidence_band : "Rumored";
    const color = BANDS[band].color;
    L.circleMarker([c[1], c[0]], { radius: 5 + Math.round(x.score * 11), color, weight: 2, fillColor: color, fillOpacity: 0.6 })
      .on("click", () => selectEvent(x.event_id)).addTo(eventLayer);
    if (i < 8 && x.place && x.place.label)
      L.marker([c[1], c[0]], { interactive: false,
        icon: L.divIcon({ className: "place-label", html: esc(x.place.label), iconAnchor: [-12, 8] }) }).addTo(eventLayer);
  });
  anom.forEach((a) => {
    const c = a.centroid && a.centroid.coordinates;
    if (!c) return;
    const col = a.subkind === "escalation" ? "#ff5a52" : "#ffb13d";
    L.circle([c[1], c[0]], { radius: 2600, color: col, weight: 2, opacity: 0.85, dashArray: "5 5",
      fill: false, interactive: false }).addTo(eventLayer);
  });
  loadControl(UNTIL);
  const chip = document.getElementById("mapChipN");
  if (chip) chip.textContent = sig.length;
}
function focusCell(lat, lon) {
  if (lat == null || lon == null) return;
  if (selectedLayer) { map.removeLayer(selectedLayer); selectedLayer = null; }
  selectedLayer = L.circleMarker([+lat, +lon], { radius: 15, color: "#fff", weight: 2, fill: false }).addTo(map);
  map.setView([+lat, +lon], Math.max(map.getZoom(), 11));
}

/* ---- By area (region → district → community drill-down) ---- */
let AREA = { level: 1, parent: null };
let AREA_UNITS = [];
const AREA_LABEL = { 1: "Regions (oblasts)", 2: "Districts (raions)", 3: "Communities (hromadas)" };
async function renderAreas() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  const q = `level=${AREA.level}` + (AREA.parent ? `&parent=${encodeURIComponent(AREA.parent)}` : "") +
            (UNTIL ? `&until=${UNTIL}T23:59:59Z` : "");
  let d;
  try { d = await api(`/rollup?theater_id=${THEATER}&${q}`); }
  catch (e) { panel.innerHTML = `<div class="empty">Couldn't load areas: ${esc(e.message)}</div>`; return; }
  AREA_UNITS = (d && d.units) || [];
  drawAreaChoropleth(AREA_UNITS);
  const crumbs = [{ name: "All areas", level: 0, admin_id: "" }].concat(d.breadcrumb || []);
  const crumbHTML = crumbs.map((c) =>
    `<span class="crumb" data-lvl="${c.level}" data-id="${esc(c.admin_id || "")}">${esc(c.name)}</span>`)
    .join('<span class="crumb-sep">›</span>');
  const max = Math.max(1, ...AREA_UNITS.map((u) => u.n_events));
  const active = AREA_UNITS.filter((u) => u.n_events > 0);
  panel.innerHTML = `
    <div class="hint">Activity rolled up by area — tap to drill in. ${UNTIL ? "As of " + esc(UNTIL) : "All dates"}.</div>
    <div class="breadcrumb">${crumbHTML}</div>
    <div class="area-levelhdr">${AREA_LABEL[AREA.level]} · ${active.length}/${AREA_UNITS.length} active · ${Number(d.total_events).toLocaleString()} events</div>
    <div id="areaList">${AREA_UNITS.map((u) => areaCard(u, max)).join("")}</div>`;
  panel.querySelectorAll(".area-card").forEach((c) => (c.onclick = () => drillArea(c.dataset.id)));
  panel.querySelectorAll(".crumb").forEach((c) => (c.onclick = () => gotoCrumb(+c.dataset.lvl, c.dataset.id)));
}
function areaCard(u, max) {
  const pct = Math.max(2, Math.round(100 * u.n_events / max));
  const drillable = AREA.level < 3 && u.n_events > 0;
  const hi = u.bands.High ? ` · <b class="area-hi">${u.bands.High} confirmed</b>` : "";
  return `<div class="area-card${u.n_events ? "" : " dim"}" data-id="${esc(u.admin_id)}">
    <div class="area-top">
      <span class="area-name">${esc(u.name)}</span>
      <span class="area-n">${Number(u.n_events).toLocaleString()}${drillable ? " ›" : ""}</span>
    </div>
    <div class="area-bar"><span style="width:${pct}%"></span></div>
    <div class="area-meta">${u.top_type ? esc(String(u.top_type).replace(/_/g, " ")) : "no recorded activity"}${hi}</div>
  </div>`;
}
function drillArea(id) {
  const u = AREA_UNITS.find((x) => x.admin_id === id);
  if (u) focusArea(u);
  openPlace(`admin:${id}`);   // any unit → its unified place view (Read + terrain + recent + drill-in)
}

// The place view: one area's Intelligence Read + terrain + recent activity + significant incidents +
// attention-ranked child areas. The children ARE the drill-down (region → district → community).
async function openPlace(ref) {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  let p;
  try { p = await api(`/area/${ref}`); }
  catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  if (!p) { panel.innerHTML = `<div class="empty">Area not found.</div>`; return; }
  drawEvents(p.recent || []);
  panel.innerHTML = placePanelHTML(p);
  document.getElementById("backArea").onclick = renderAreas;
  panel.querySelectorAll(".place-kid").forEach((c) => (c.onclick = () => openPlace(c.dataset.ref)));
  panel.querySelectorAll(".place-ev[data-id]").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
}

function placePanelHTML(p) {
  const att = ATT[(p.attention && p.attention.status) || "steady"] || ATT.steady;
  const t = p.terrain || {};
  const chips = Object.entries(t.landcover || {}).slice(0, 3)
    .map(([k, v]) => `<span class="terrain-chip">${esc(k)} ${Math.round(v * 100)}%</span>`);
  if (t.builtup_pct > 0.02) chips.push(`<span class="terrain-chip">${Math.round(t.builtup_pct * 100)}% built-up</span>`);
  if (t.road_unpaved_share > 0) chips.push(`<span class="terrain-chip">${Math.round(t.road_unpaved_share * 100)}% unpaved roads</span>`);
  const read = p.read || {};
  const readHtml = read.summary ? `<div class="read-panel">
      <div class="read-head"><span class="read-title">✦ Intelligence read</span>
        <span class="att-badge ${att.cls}">${att.arrow} ${att.label}</span></div>
      <div class="read-body">${esc(read.summary)}</div>
      ${(read.provenance || []).length ? `<div class="read-prov">Seen by ${esc((read.provenance || []).join(" · "))}</div>` : ""}
    </div>` : "";
  const recent = (p.recent || []).slice(0, 12).map(recentRow).join("") ||
    `<div class="muted">No activity in the last 30 days.</div>`;
  const sig = (p.significant || []).slice(0, 10).map(sigRow).join("") ||
    `<div class="muted">Nothing flagged as significant here.</div>`;
  const kids = (p.children || []).map(childRow).join("");
  return `
    <span class="backlink" id="backArea">‹ Back to areas</span>
    <h2 style="margin:.3em 0 .2em">${esc(p.area.label)}</h2>
    ${readHtml}
    ${chips.length ? `<div class="place-section"><h4>Terrain</h4><div class="terrain-chips">${chips.join("")}</div></div>` : ""}
    <div class="place-section"><h4>Recent activity <span class="muted">· last 30 days</span></h4>${recent}</div>
    <div class="place-section"><h4>Significant incidents</h4>${sig}</div>
    ${kids ? `<div class="place-section"><h4>Drill in</h4>${kids}</div>` : ""}`;
}

function recentRow(ev) {
  const b = BANDS[bandOf(ev)] || {};
  const date = String(ev.occurred_start || ev.occurred_at || "").slice(0, 10);
  const geo = ev.geo_context ? ` · <span class="muted">${esc(ev.geo_context)}</span>` : "";
  return `<div class="place-ev" data-id="${esc(ev.event_id)}">
    <span class="pe-dot" style="background:${b.color || "#888"}"></span>
    <span class="pe-type">${esc(String(ev.event_type).replace(/_/g, " "))}</span>
    <span class="pe-place">${esc(placeLabel(ev))}</span>
    <span class="pe-date">${esc(date)}</span>${geo}</div>`;
}

function sigRow(x) {
  const date = String(x.occurred_start || "").slice(0, 10);
  return `<div class="place-ev" data-id="${esc(x.event_id)}">
    <span class="pe-score">${(x.score || 0).toFixed(2)}</span>
    <span class="pe-type">${esc(String(x.event_type).replace(/_/g, " "))}</span>
    <span class="pe-date">${esc(date)}</span></div>`;
}

function childRow(c) {
  const att = ATT[(c.attention && c.attention.status) || "steady"] || ATT.steady;
  return `<div class="place-kid" data-ref="${esc(c.ref)}">
    <span class="pk-name">${esc(c.name)}</span>
    <span class="att-badge ${att.cls}">${att.arrow} ${att.label}</span>
    <span class="pk-n">${Number(c.n_events || 0).toLocaleString()} ›</span></div>`;
}
async function renderAreaEvents(unit) {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  focusArea(unit);
  const q = `admin=${encodeURIComponent(unit.admin_id)}&admin_level=3` + (UNTIL ? `&until=${UNTIL}T23:59:59Z` : "");
  let data;
  try { data = await api(`/events?theater_id=${THEATER}&limit=300&${q}`); }
  catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const events = (data && data.events) || [];
  drawEvents(events);
  const rank = { High: 0, Medium: 1, Low: 2, Rumored: 3 };
  events.sort((a, b) => rank[bandOf(a)] - rank[bandOf(b)] ||
    String(b.occurred_start || "").localeCompare(String(a.occurred_start || "")));
  panel.innerHTML = `
    <span class="backlink" id="backArea">‹ Back to areas</span>
    <h2 style="margin:.3em 0 0">${esc(unit.name)}</h2>
    <div class="hint">${events.length} event(s) in this community${UNTIL ? ", as of " + esc(UNTIL) : ""}. Tap one for its sources.</div>
    ${events.map(cardHTML).join("") || '<div class="muted">No events.</div>'}`;
  document.getElementById("backArea").onclick = renderAreas;
  panel.querySelectorAll(".card").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
}
function gotoCrumb(crumbLevel, id) {
  if (crumbLevel === 0) { AREA = { level: 1, parent: null }; }
  else { AREA = { level: crumbLevel + 1, parent: id }; }
  renderAreas();
}
function focusArea(u) {
  if (u.centroid && u.centroid.coordinates) {
    const [lon, lat] = u.centroid.coordinates;
    map.setView([lat, lon], Math.max(map.getZoom(), AREA.level === 1 ? 8 : AREA.level === 2 ? 9 : 11));
  }
}
function drawAreaChoropleth(units) {
  eventLayer.clearLayers();
  const max = Math.max(1, ...units.map((u) => u.n_events));
  let bounds = null;
  units.forEach((u) => {
    if (!u.geometry) return;
    const a = u.n_events / max;
    const fill = u.n_events === 0 ? 0.03 : 0.12 + 0.6 * Math.sqrt(a);   // sqrt = perceptual heat scale
    const lyr = L.geoJSON(u.geometry, { style: { color: "#ff7a6e", weight: 1, opacity: 0.45,
      fillColor: "#ff5a52", fillOpacity: fill } }).on("click", () => drillArea(u.admin_id)).addTo(eventLayer);
    bounds = bounds ? bounds.extend(lyr.getBounds()) : lyr.getBounds();
    if (u.centroid && u.centroid.coordinates && u.n_events > 0) {
      const [lon, lat] = u.centroid.coordinates;
      L.marker([lat, lon], { interactive: false, icon: L.divIcon({ className: "area-label",
        html: `${esc(u.name)}<b>${Number(u.n_events).toLocaleString()}</b>` }) }).addTo(eventLayer);
    }
  });
  loadControl(UNTIL);
  map.invalidateSize();   // container may have resized; without this fitBounds picks a bogus zoom
  if (bounds && bounds.isValid())
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: AREA.level === 1 ? 8 : AREA.level === 2 ? 10 : 12 });
  const chip = document.getElementById("mapChipN"); if (chip) chip.textContent = units.length;
}

/* ---- Events list ---- */
async function renderEvents() {
  const panel = document.getElementById("panel");
  const days = Math.floor((Date.now() - T_MIN) / 86400000);
  const cur = UNTIL ? Math.floor((Date.parse(UNTIL) - T_MIN) / 86400000) : days;
  panel.innerHTML = `
    <div class="scrubber">
      <div class="scrub-top">
        <span class="scrub-label" id="scrubLabel">${UNTIL ? "As of " + UNTIL : "Live — everything so far"}</span>
        <button class="scrub-reset" id="scrubReset"${UNTIL ? "" : " disabled"}>Live</button>
      </div>
      <input type="range" id="timeScrub" min="0" max="${days}" value="${cur}" step="1"
        aria-label="Show events up to this date" />
      <div class="scrub-ends"><span>Feb 2022</span><span>today</span></div>
      <label class="ctl-toggle"><input type="checkbox" id="ctlToggle"${SHOW_CONTROL ? " checked" : ""} />
        Tint the map by who controlled each area</label>
    </div>
    <label class="field"><span>Show events that are…</span>
      <select id="bandFilter">
        <option value="">All confidence</option><option>High</option><option>Medium</option>
        <option>Low</option><option>Rumored</option>
      </select></label>
    <div id="eventList" class="empty">Loading…</div>`;
  document.getElementById("bandFilter").onchange = loadEvents;
  const scrub = document.getElementById("timeScrub");
  scrub.oninput = () => {
    const v = +scrub.value;
    UNTIL = v >= days ? null : new Date(T_MIN + v * 86400000).toISOString().slice(0, 10);
    document.getElementById("scrubLabel").textContent = UNTIL ? "As of " + UNTIL : "Live — everything so far";
    document.getElementById("scrubReset").disabled = !UNTIL;
    clearTimeout(renderEvents._t); renderEvents._t = setTimeout(loadEvents, 140);
  };
  document.getElementById("scrubReset").onclick = () => { UNTIL = null; renderEvents(); };
  document.getElementById("ctlToggle").onchange = (e) => { SHOW_CONTROL = e.target.checked; loadControl(UNTIL); };
  await loadEvents();
}
async function loadEvents() {
  const band = document.getElementById("bandFilter")?.value || "";
  let data;
  const q = `${band ? `&band=${band}` : ""}${UNTIL ? `&until=${UNTIL}T23:59:59Z` : ""}`;
  try {
    data = await api(`/events?theater_id=${THEATER}&limit=500${q}`);
  } catch (e) { document.getElementById("eventList").innerHTML = `<div class="empty">Couldn't load events: ${esc(e.message)}</div>`; return; }
  const events = (data && data.events) || [];
  drawEvents(events);
  loadControl(UNTIL);              // keep the control tint in sync with the scrubbed date
  if (!band) setSummary(events);   // the top strip is a global overview — only refresh it when unfiltered
  const list = document.getElementById("eventList");
  if (!events.length) { list.className = "empty"; list.textContent = "No events to show for this area yet."; return; }
  list.className = "";
  // Corroborated events first (the named, high-confidence incidents lead the feed).
  const rank = { High: 0, Medium: 1, Low: 2, Rumored: 3 };
  events.sort((a, b) => rank[bandOf(a)] - rank[bandOf(b)]);
  list.innerHTML = `<div class="hint">${events.length} event(s). Tap one to see where it came from.</div>` +
    events.map(cardHTML).join("");
  list.querySelectorAll(".card").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
}
function placeLabel(ev) { return (ev.place && ev.place.label) || ("area " + ev.cell_id); }
function cardHTML(ev) {
  const band = bandOf(ev);
  const warn = (ev.flags || []).some((f) => UNCORROBORATED.includes(f)) ? "⚠ " : "";
  return `<div class="card" data-id="${esc(ev.event_id)}" data-band="${band}">
    <div class="card-top">
      <span class="etype">${warn}${esc(placeLabel(ev))}<span class="type"> · ${esc(ev.event_type)}</span></span>
      <span class="pill ${BANDS[band].cls}">${BANDS[band].plain}</span>
    </div>
    <div class="meta">${esc(ev.n_independent_families ?? 0)} independent source(s)</div>
  </div>`;
}

/* ---- Event detail ---- */
async function selectEvent(id) {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  let ev;
  try { ev = await api(`/events/${id}`); } catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  if (!ev) { panel.innerHTML = `<div class="empty">Event not found.</div>`; return; }
  focusEvent(ev);

  const band = bandOf(ev);
  const flags = ev.flags || [];
  const conf = typeof ev.confidence === "number" ? Math.round(ev.confidence * 100) + "%" : "—";
  const warns = flags.filter((f) => FLAG_PLAIN[f]).map((f) => `<div class="warnflag">⚑ ${FLAG_PLAIN[f]}</div>`).join("");

  const evidence = (ev.observations || []).map((o) => {
    const s = srcKind(o);
    return `<div class="evidence src-${s.k}">
      <div class="when"><span class="src-tag ${s.k}">${s.label}</span> · ${esc(o.occurred_at || "time unknown")}</div>
      ${o.excerpt ? `<blockquote>${esc(o.excerpt)}</blockquote>` : ""}
    </div>`;
  }).join("") || `<div class="muted">No reports attached.</div>`;

  // Provenance — the distinct sensor types that saw this event (makes the multi-modal fusion visible).
  const _seen = [...new Map((ev.observations || []).map((o) => { const s = srcKind(o); return [s.label, s]; })).values()];
  const provenance = _seen.length
    ? `<div class="provenance"><span class="prov-label">Seen by</span>${_seen.map((s) =>
        `<span class="prov ${s.k}">${s.label}</span>`).join("")}</div>`
    : "";

  const ctxFields = [["label","Area name"],["admin_l1","Region"],["admin_l2","District"],
    ["admin_l3","Locality"],["landcover_label","Land type"],["builtup_pct","Built-up %"],
    ["has_bridge","Bridge nearby"],["nearest_road_class","Nearest road"]];
  const ctx = ev.context || {};
  const ctxRows = ctxFields.filter(([k]) => ctx[k] != null)
    .map(([k, lbl]) => `<tr><td>${lbl}</td><td>${esc(ctx[k])}</td></tr>`).join("");

  panel.innerHTML = `
    <span class="backlink" id="back">‹ Back to events</span>
    <h2 style="margin:.2em 0">${esc(placeLabel(ev))} <span class="pill ${BANDS[band].cls}">${BANDS[band].plain}</span></h2>
    <div class="muted" style="text-transform:capitalize;margin-bottom:4px">${esc(ev.event_type)}${ev.place && ev.place.distance_km != null ? ` · ~${ev.place.distance_km} km from ${esc(ev.place.name)}` : ""}</div>
    <div class="code">area ${esc(ev.cell_id)} · id ${esc(ev.event_id)}</div>
    ${warns}
    ${provenance}
    <div class="cellhist"><span class="backlink" id="cellHist">▤ See this area's full history over time ›</span></div>
    <div class="why" data-band="${band}"><p><b>Why “${BANDS[band].plain}”?</b> ${BANDS[band].meaning}.</p></div>
    <div class="metrics">
      <div class="metric"><div class="v">${esc(ev.n_sources ?? 0)}</div><div class="k">reports</div></div>
      <div class="metric"><div class="v">${esc(ev.n_independent_families ?? 0)}</div><div class="k">independent sources</div></div>
      <div class="metric"><div class="v">${conf}</div><div class="k">confidence</div></div>
    </div>
    <div class="section-title">Where this came from</div>
    <div class="hint">Each report behind this event. Two <em>different</em> source types is stronger than one repeated.</div>
    ${evidence}
    <div class="section-title">About this location</div>
    ${ctxRows ? `<table class="ctx">${ctxRows}</table>` : `<div class="muted">No background info for this area.</div>`}
    <div class="section-title">Your decision</div>
    <div class="hint"><strong>Keep</strong> = one real event · <strong>Split</strong> = actually several · <strong>Remove</strong> = not a real event.</div>
    <label class="field"><span>Note (saved with your decision)</span><input id="rvReason" placeholder="why you're keeping / splitting / removing"></label>
    <div class="btnrow">
      <button class="btn primary" data-act="confirm">Keep</button>
      <button class="btn" data-act="split">Split</button>
      <button class="btn danger" data-act="reject">Remove</button>
    </div>`;
  document.getElementById("back").onclick = renderEvents;
  document.getElementById("cellHist").onclick = () => renderCellHistory(ev.cell_id, placeLabel(ev));
  panel.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => review(id, b.dataset.act)));
}

/* ---- Per-cell history (the chronology of one 1km area) ---- */
async function renderCellHistory(cellId, label) {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  let c;
  try { c = await api(`/cells/${encodeURIComponent(cellId)}`); }
  catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const events = (((c && c.events) || []).slice())
    .sort((a, b) => String(a.occurred_start || "").localeCompare(String(b.occurred_start || "")));
  const timeline = events.map((e) => {
    const band = bandOf(e);
    return `<div class="tl-item" data-band="${band}">
      <div class="tl-date">${esc(String(e.occurred_start || "").slice(0, 10) || "—")}</div>
      <div class="tl-body"><span class="pill ${BANDS[band].cls}">${BANDS[band].plain}</span>
        <span class="tl-type">${esc(e.event_type)}</span></div>
    </div>`;
  }).join("") || `<div class="muted">No history recorded for this area.</div>`;
  panel.innerHTML = `
    <span class="backlink" id="back">‹ Back to events</span>
    <h2 style="margin:.2em 0">${esc(label || ("area " + cellId))}</h2>
    <div class="code">area ${esc(cellId)} · ${events.length} event(s) over time</div>
    <div class="hint">Everything recorded in this 1&nbsp;km area, oldest first — the area's full history.</div>
    <div class="timeline">${timeline}</div>`;
  document.getElementById("back").onclick = renderEvents;
}
async function review(id, action) {
  const reason = document.getElementById("rvReason")?.value || "";
  try {
    await api("/review", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: id, action, reason, analyst: analyst() }) });
    toast({ confirm: "Kept.", split: "Marked for splitting.", reject: "Removed." }[action] + " Saved.");
  } catch (e) { toast("Couldn't save: " + e.message, true); }
}

/* ---- Needs review ---- */
async function renderReview() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="hint">Events the system wants a person to look at — least-certain first.</div><div id="rq" class="empty">Loading…</div>`;
  let data;
  try { data = await api(`/verify-queue?theater_id=${THEATER}&limit=50`); }
  catch (e) { document.getElementById("rq").innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const events = (data && data.events) || [];
  drawEvents(events);
  const rq = document.getElementById("rq");
  if (!events.length) { rq.className = "empty"; rq.textContent = "All clear — nothing waiting for review."; return; }
  rq.className = "";
  rq.innerHTML = `<div class="hint">${events.length} waiting.</div>` + events.map(cardHTML).join("");
  rq.querySelectorAll(".card").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
}

/* ---- Add event ---- */
function renderAdd() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `
    <div class="hint">Tell the system about a real event you already know happened. This builds the
      examples it's checked against, so be accurate. It's offline practice data — nothing goes live.</div>
    <label class="field"><span>Short name for this event</span>
      <input id="evName" placeholder="e.g. avdiivka-strike-2024-03-10"></label>
    <div class="two">
      <label class="field"><span>What happened?</span>
        <select id="evType">${Object.keys(EVENT_TYPES).map((t) => `<option>${t}</option>`).join("")}</select></label>
      <label class="field"><span>How well-confirmed?</span>
        <select id="evConf">${Object.keys(CONFIDENCE).map((t) => `<option>${t}</option>`).join("")}</select></label>
    </div>
    <div class="two">
      <label class="field"><span>Date it happened</span><input id="evDate" type="date"></label>
      <label class="field"><span>Approx. time (UTC)</span><input id="evTime" type="time" value="12:00"></label>
    </div>
    <label class="field"><span>Where — place name</span><input id="evPlace" placeholder="e.g. Avdiivka coke plant"></label>
    <div class="two">
      <label class="field"><span>Longitude (optional)</span><input id="evLon" placeholder="37.75"></label>
      <label class="field"><span>Latitude (optional)</span><input id="evLat" placeholder="48.14"></label>
    </div>
    <label class="field"><span>The reports — one per line (each line = one source)</span>
      <textarea id="evReports" placeholder="Russian strike hit the plant, large fire reported&#10;Satellite image shows new damage to the main building"></textarea></label>
    <div class="btnrow">
      <button class="btn primary" id="evSave">Save this event</button>
      <button class="btn" id="evRegen" title="Fold saved events into the checked example set">Update examples</button>
    </div>`;
  document.getElementById("evSave").onclick = saveEvent;
  document.getElementById("evRegen").onclick = async () => {
    try { await api("/fixtures/regenerate", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      toast("Examples updated."); } catch (e) { toast("Couldn't update: " + e.message, true); }
  };
}
async function saveEvent() {
  const name = document.getElementById("evName").value.trim();
  const reports = document.getElementById("evReports").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const place = document.getElementById("evPlace").value.trim();
  const lonS = document.getElementById("evLon").value.trim(), latS = document.getElementById("evLat").value.trim();
  if (!name) return toast("Give the event a short name.", true);
  if (!reports.length) return toast("Add at least one report (one line).", true);
  let lon = null, lat = null;
  if (lonS || latS) { lon = parseFloat(lonS); lat = parseFloat(latS);
    if (isNaN(lon) || isNaN(lat)) return toast("Longitude/latitude must be numbers.", true); }
  if (lon === null && !place) return toast("Give a place name or coordinates.", true);

  const date = document.getElementById("evDate").value || new Date().toISOString().slice(0, 10);
  const time = document.getElementById("evTime").value || "12:00";
  const iso = `${date}T${time}:00Z`;
  const type = EVENT_TYPES[document.getElementById("evType").value];
  const observations = reports.map((text, i) => {
    const o = { ref: `obs-${i + 1}`, source_id: `source-${i + 1}`, type, time: iso, text };
    if (place) o.place = place;
    if (lon !== null) { o.lon = lon; o.lat = lat; }
    return o;
  });
  const payload = { incident_id: name, expect: { band: CONFIDENCE[document.getElementById("evConf").value], n_families: observations.length },
    must_not_merge_with: [], observations };
  try {
    await api("/label", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "incident_label", payload, analyst: analyst(), versions: null, run_id: null }) });
    toast(`Saved “${name}”. Click ‘Update examples’ when you're done adding.`);
  } catch (e) { toast("Couldn't save: " + e.message, true); }
}

/* ---- Health ---- */
async function renderHealth() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  let h, rej;
  try { h = await api("/healthz"); rej = await api(`/rejections?theater_id=${THEATER}`); }
  catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const summary = (rej && rej.summary) || { total: 0, by_reason: {} };
  const reasons = Object.entries(summary.by_reason || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("");
  panel.innerHTML = `
    <div class="section-title">System</div>
    <table class="ctx">
      <tr><td>Status</td><td>${h.status === "ok" ? "✓ running" : esc(h.status)}</td></tr>
      <tr><td>Live feeds</td><td>${h.live_feeds_enabled ? "on" : "off"}</td></tr>
      <tr><td>Area</td><td>${esc(h.theater)}</td></tr>
    </table>
    <div class="section-title">Skipped reports</div>
    <div class="hint">Reports the system set aside (outside the area, or exact duplicates). Nothing is thrown away silently.</div>
    <div class="metric" style="margin-bottom:10px"><div class="v">${summary.total || 0}</div><div class="k">set aside (with a reason)</div></div>
    ${reasons ? `<table class="ctx">${reasons}</table>` : `<div class="muted">Nothing set aside.</div>`}`;
}

/* ---- Watch areas: feature layers + named areas of interest ---- */
const FEATURE_KINDS = {
  water:   { color: "#4aa3ff", label: "Rivers / water" },
  road:    { color: "#d3a429", label: "Roads" },
  forest:  { color: "#3fae6a", label: "Forests" },
  builtup: { color: "#9b8cff", label: "Built-up" },
};
let FEATURE_ON = {};
const _featGroups = {};

async function renderWatchAreas() {
  const panel = document.getElementById("panel");
  panel.innerHTML = `
    <div class="hint">The areas you're watching, ranked by what needs attention. Each carries an
      intelligence read of its recent activity. Draw a new one, or add a region from “By area”.</div>
    <div class="section-title">Your areas <button class="btn small" id="drawAoi">+ Mark an area</button></div>
    <div id="aoiList" class="empty">Loading…</div>
    <details class="ftoggle-wrap"><summary>Geography layers</summary>
    <div class="ftoggles">${Object.entries(FEATURE_KINDS).map(([k, d]) =>
      `<label class="ftoggle"><input type="checkbox" data-fk="${k}"${FEATURE_ON[k] ? " checked" : ""}>
        <span class="fdot" style="background:${d.color}"></span>${d.label}</label>`).join("")}</div></details>`;
  panel.querySelectorAll("[data-fk]").forEach((c) => (c.onchange = () => toggleFeature(c.dataset.fk, c.checked)));
  document.getElementById("drawAoi").onclick = startDrawAoi;
  for (const k of Object.keys(FEATURE_ON)) if (FEATURE_ON[k]) drawFeatures(k);
  await loadAois();
}

const ATT = {
  escalating: { cls: "esc", label: "Escalating", arrow: "▲" },
  steady: { cls: "steady", label: "Steady", arrow: "—" },
  quieting: { cls: "quiet", label: "Quieting", arrow: "▼" },
};
async function loadAois() {
  let d;
  try { d = await api(`/watch?theater_id=${THEATER}`); }
  catch (e) { document.getElementById("aoiList").innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  const areas = (d && d.areas) || [];
  aoiLayer.clearLayers();
  areas.forEach((a) => {
    if (!a.centroid) return;
    const [lon, lat] = a.centroid.coordinates;
    L.marker([lat, lon], { interactive: false, icon: L.divIcon({ className: "aoi-pin",
      html: `${esc(a.label)}<b>${a.n_events}</b>` }) }).addTo(aoiLayer);
  });
  const list = document.getElementById("aoiList");
  if (!areas.length) { list.className = "empty"; list.textContent = "No areas yet — draw one on the map with “Mark an area”."; return; }
  list.className = "";
  const need = d.needs_attention || 0;
  const banner = need ? `<div class="watch-summary">▲ ${need} area${need > 1 ? "s" : ""} need attention</div>` : "";
  list.innerHTML = banner + areas.map(aoiCard).join("");
  list.querySelectorAll(".aoi-card").forEach((c) => (c.onclick = () => focusAoi(+c.dataset.id)));
}
function aoiCard(a) {
  const att = ATT[(a.attention && a.attention.status) || "steady"] || ATT.steady;
  const prov = (a.provenance || []).join(" · ");
  return `<div class="aoi-card att-${att.cls}" data-id="${a.aoi_id}">
    <div class="aoi-top"><span class="aoi-name">${esc(a.label)}</span>
      <span class="att-badge ${att.cls}">${att.arrow} ${att.label}</span></div>
    ${a.read ? `<div class="aoi-read">${esc(a.read)}</div>` : ""}
    <div class="aoi-meta">${a.n_events} events · ${a.n_cells} cells${prov ? " · seen by " + esc(prov) : ""}</div>
  </div>`;
}

async function toggleFeature(kind, on) {
  FEATURE_ON[kind] = on;
  if (on) await drawFeatures(kind); else clearFeatures(kind);
}
async function drawFeatures(kind) {
  let d;
  try { d = await api(`/features?theater_id=${THEATER}&kind=${kind}`); } catch (e) { return; }
  clearFeatures(kind);
  const g = L.layerGroup(), color = FEATURE_KINDS[kind].color;
  (d.features || []).forEach((f) => {
    if (!f.geometry) return;
    L.geoJSON(f.geometry, { style: { color, weight: kind === "water" ? 2 : 1.2, opacity: 0.7,
      fillColor: color, fillOpacity: 0.1 } })
      .bindTooltip(f.name || FEATURE_KINDS[kind].label, { sticky: true })
      .on("click", () => promoteFeature(f, kind)).addTo(g);
  });
  g.addTo(featureLayer); _featGroups[kind] = g;
}
function clearFeatures(kind) {
  if (_featGroups[kind]) { featureLayer.removeLayer(_featGroups[kind]); delete _featGroups[kind]; }
}

async function promoteFeature(f, kind) {
  const name = prompt(`Name this ${FEATURE_KINDS[kind].label.toLowerCase()} as an area of interest:`, f.name || "");
  if (!name) return;
  const aoiKind = kind === "water" ? "obstacle" : kind === "road" ? "corridor" : "named_feature";
  await createAoi({ kind: aoiKind, label: name, source: "derived", source_feature_id: f.feature_id });
}
function startDrawAoi() {
  if (!window.L || !L.Draw) { toast("Drawing tool didn't load.", true); return; }
  toast("Click on the map to draw a shape; double-click to finish.");
  drawHandler = new L.Draw.Polygon(map, { shapeOptions: { color: "#ffd24a", weight: 2 } });
  drawHandler.enable();
}
async function onAoiDrawn(e) {
  const geometry = e.layer.toGeoJSON().geometry;
  const name = prompt("Name this area of interest:");
  if (!name) return;
  await createAoi({ kind: "grid_of_interest", label: name, source: "drawn", geometry });
}
async function createAoi(body) {
  body.theater_id = THEATER; body.created_by = analyst();
  try {
    const r = await api("/aois", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    toast(`Marked “${body.label}” — ${r.n_cells} cells.`);
    if (document.querySelector('.tabs button.active')?.dataset.tab === "watch") loadAois();
  } catch (e) { toast("Couldn't create: " + e.message, true); }
}

async function focusAoi(id) {
  const panel = document.getElementById("panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  let a;
  try { a = await api(`/aois/${id}`); } catch (e) { panel.innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; return; }
  aoiLayer.clearLayers();
  if (a.geometry) {
    const lyr = L.geoJSON(a.geometry, { style: { color: "#ffd24a", weight: 2, fillColor: "#ffd24a", fillOpacity: 0.12 } }).addTo(aoiLayer);
    try { map.invalidateSize(); map.fitBounds(lyr.getBounds(), { padding: [40, 40], maxZoom: 12 }); } catch (_) {}
  }
  let ev = [], read = null;
  try { const d = await api(`/events?theater_id=${THEATER}&aoi=${id}&limit=300`); ev = (d && d.events) || []; } catch (_) {}
  try { read = await api(`/aois/${id}/read`); } catch (_) {}
  drawEvents(ev);
  const rank = { High: 0, Medium: 1, Low: 2, Rumored: 3 };
  ev.sort((x, y) => rank[bandOf(x)] - rank[bandOf(y)]);
  const metrics = Object.entries(a.bands || {}).map(([b, n]) =>
    `<div class="metric"><div class="v">${n}</div><div class="k">${BANDS[b] ? BANDS[b].plain : b}</div></div>`).join("");
  const att = read ? (ATT[read.indicators] || ATT.steady) : null;
  const readHtml = read ? `<div class="read-panel">
      <div class="read-head"><span class="read-title">✦ Intelligence read</span>
        <span class="att-badge ${att.cls}">${att.arrow} ${att.label}</span></div>
      <p class="read-body">${esc(read.summary)}</p>
      ${(read.provenance || []).length ? `<div class="read-prov">Seen by ${esc((read.provenance || []).join(" · "))}</div>` : ""}
    </div>` : "";
  panel.innerHTML = `
    <span class="backlink" id="back">‹ Back to my watch</span>
    <h2 style="margin:.3em 0 0">${esc(a.label)}</h2>
    <div class="muted" style="text-transform:capitalize">${esc(String(a.kind).replace(/_/g, " "))} · ${a.n_cells} cells</div>
    ${a.note ? `<div class="hint">${esc(a.note)}</div>` : ""}
    ${readHtml}
    <div class="metrics">${metrics}</div>
    <div class="section-title">Events here <button class="btn small danger" id="delAoi">Delete area</button></div>
    <div class="hint">${ev.length} event(s) inside this area. Tap one for its sources.</div>
    ${ev.map(cardHTML).join("") || '<div class="muted">No events recorded here yet.</div>'}`;
  document.getElementById("back").onclick = () => { aoiLayer.clearLayers(); renderWatchAreas(); };
  document.getElementById("delAoi").onclick = async () => {
    if (!confirm(`Delete “${a.label}”?`)) return;
    try { await api(`/aois/${id}`, { method: "DELETE" }); toast("Deleted."); aoiLayer.clearLayers(); renderWatchAreas(); }
    catch (e) { toast("Couldn't delete: " + e.message, true); }
  };
  panel.querySelectorAll(".card").forEach((c) => (c.onclick = () => selectEvent(c.dataset.id)));
}

/* ---- theaters: switch the board between regions (Ukraine land, Black Sea maritime, …) ---- */
async function loadTheaters() {
  const sel = document.getElementById("theaterSel");
  try { const d = await api("/theaters"); THEATERS = (d && d.theaters) || []; } catch (e) { THEATERS = []; }
  if (!sel) return;
  if (!THEATERS.length) { sel.innerHTML = `<option>${esc("Ukraine — " + THEATER)}</option>`; return; }
  sel.innerHTML = THEATERS.map((t) =>
    `<option value="${esc(t.theater_id)}">${esc(t.label)} · ${t.n_events.toLocaleString()} events</option>`).join("");
  if (!THEATERS.some((t) => t.theater_id === THEATER)) THEATER = THEATERS[0].theater_id;
  sel.value = THEATER;
  sel.onchange = () => switchTheater(sel.value);
}
function switchTheater(tid) {
  THEATER = tid;
  UNTIL = null;
  const sel = document.getElementById("theaterSel");
  if (sel && sel.value !== tid) sel.value = tid;   // keep the dropdown in sync on programmatic switch
  const t = THEATERS.find((x) => x.theater_id === tid);
  if (t && t.bbox && map) {
    const [w, s, e, n] = t.bbox;
    try { map.invalidateSize(); map.fitBounds([[s, w], [n, e]], { padding: [20, 20] }); } catch (_) {}
  }
  if (aoiLayer) aoiLayer.clearLayers();
  FEATURE_ON = {};                        // feature layers are per-theater
  const active = document.querySelector(".tabs button.active")?.dataset.tab || "insights";
  setTab(active);
}

/* ---- boot ---- */
async function boot() {
  initMap();
  document.getElementById("tabs").addEventListener("click", (e) => { if (e.target.dataset.tab) setTab(e.target.dataset.tab); });
  const saved = localStorage.getItem("analyst"); if (saved) document.getElementById("analyst").value = saved;
  document.getElementById("analyst").addEventListener("change", (e) => localStorage.setItem("analyst", e.target.value));

  const status = document.getElementById("status");
  try {
    const h = await api("/healthz");
    THEATER = (h && h.theater) || THEATER;
    status.textContent = STATIC_MODE ? "● live demo (snapshot)" : "● connected";
    status.className = "status ok";
    if (STATIC_MODE) toast("Live demo — a read-only snapshot of the board. Drawing & filters are disabled.");
    await loadTheaters();
  } catch (e) {
    status.textContent = "● backend offline"; status.className = "status off";
    toast("Can't reach the backend. Start it with: uvicorn api.main:app", true);
  }
  setTab("watch");
}
boot();
