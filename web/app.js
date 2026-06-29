/* ============================================================================
   Situational Picture — front-end logic (vanilla JS, no build step).
   Talks to the read-only FastAPI on the same origin. All geometry it receives
   is already rounded to 1km cells by the API; this file never sees raw coords.
   Structure: tiny API helper -> map -> four tab renderers (events/review/add/
   health) -> event detail. Plain language only; see styles.css for the design.
   ============================================================================ */

const API = "";                       // same origin (served by the API at /ui/)
let THEATER = "ua_donbas";
let map, eventLayer, selectedLayer;
const T_MIN = Date.UTC(2022, 1, 24);  // 2022-02-24, the full-scale invasion — chronology start
let UNTIL = null;                      // null = live (everything); else "YYYY-MM-DD" cumulative cut-off

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
  if (m === "imagery" || f.includes("copernicus") || f.includes("sentinel")) return { k: "sat", label: "Satellite radar" };
  if (m === "thermal" || f.includes("firms") || f.includes("modis")) return { k: "thermal", label: "Thermal / fire" };
  return { k: "news", label: "News report" };
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

/* ---- tiny API helper ---- */
async function api(path, opts) {
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
  eventLayer = L.layerGroup().addTo(map);
  renderLegend();
}
function renderLegend() {
  document.getElementById("legend").innerHTML =
    `<h4>What the colours mean</h4>` +
    Object.entries(BANDS).map(([b, d]) =>
      `<div class="row"><span class="dot" style="background:${d.color}"></span><b>${d.plain}</b> — ${d.meaning}</div>`).join("");
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
const TABS = { events: renderEvents, review: renderReview, add: renderAdd, health: renderHealth };
function setTab(tab) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  (TABS[tab] || renderEvents)();
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

/* ---- boot ---- */
async function boot() {
  initMap();
  document.getElementById("tabs").addEventListener("click", (e) => { if (e.target.dataset.tab) setTab(e.target.dataset.tab); });
  const saved = localStorage.getItem("analyst"); if (saved) document.getElementById("analyst").value = saved;
  document.getElementById("analyst").addEventListener("change", (e) => localStorage.setItem("analyst", e.target.value));

  const status = document.getElementById("status");
  try {
    const h = await api("/healthz");
    THEATER = h.theater || THEATER;
    document.getElementById("theater").textContent = "Ukraine — " + THEATER;
    status.textContent = "● connected"; status.className = "status ok";
  } catch (e) {
    status.textContent = "● backend offline"; status.className = "status off";
    toast("Can't reach the backend. Start it with: uvicorn api.main:app", true);
  }
  setTab("events");
}
boot();
