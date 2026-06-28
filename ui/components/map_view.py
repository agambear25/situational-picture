"""
Map screen for the COP: a pydeck map of confirmed/candidate events over the Donbas theater,
colored by confidence band. This is decision-support, not targeting — every event we draw is
already coarsened by the API to a 1km grid cell (centroid + cell polygon derived from cell_id),
so there are no precise coordinates anywhere in this module. We only ever read 'centroid' /
'geometry' off the event dict; we never construct or display a lat/lon ourselves.

Single-source / echo-only events are intentionally drawn in the Rumored color so an analyst can
see at a glance which dots are *not* yet corroborated — surfacing weakness, not hiding it.

Component contract (see app.py): exposes exactly `render(client, events=None)`.
"""
from __future__ import annotations

import streamlit as st

# Band -> RGB. Matches ui.api_client.BAND_COLORS guidance so the map agrees with every other
# screen. Kept here (not imported) so this component renders even if BAND_COLORS is absent on an
# older client build; the band strings are the API's canonical confidence_band values.
BAND_COLORS: dict[str, list[int]] = {
    "High": [34, 197, 94],      # green  — multi-family corroborated
    "Medium": [234, 179, 8],    # amber  — partial corroboration
    "Low": [249, 115, 22],      # orange — weak signal
    "Rumored": [148, 163, 184], # slate  — single-source / echo-only / uncorroborated
}
# Anything we can't classify falls back to the Rumored color: an unknown band is, by definition,
# not something we can stand behind — fail toward "treat as uncorroborated", never toward "High".
_DEFAULT_COLOR = BAND_COLORS["Rumored"]

# Initial camera over the ua_donbas AOI. Derived from the theater bbox [36.0,46.8,39.5,49.5]
# (config/theaters.yaml) -> center ~lon 37.8 / lat 48.2; zoom 6 frames the whole oblast cluster.
_VIEW_LON = 37.8
_VIEW_LAT = 48.2
_VIEW_ZOOM = 6

# Flags (from the event dict) that mean "not independently corroborated". If an event carries any
# of these we force the Rumored color regardless of its self-reported band, so the map can never
# imply more confidence than the evidence supports.
_UNCORROBORATED_FLAGS = ("single-source", "echo-only", "verification-needed")

# Substrate layers we offer in the toggle. These are the layer *names* the read-only API exposes
# via GET /layers/{layer}; we list them rather than fetching a catalog to keep the first paint
# cheap (each selected layer is fetched lazily only when the analyst ticks it).
_SUBSTRATE_LAYERS = ("admin", "hydrorivers", "osm_ukraine", "landcover")


def _band_color(event: dict) -> list[int]:
    """Pick the RGB for an event, demoting any uncorroborated event to the Rumored color."""
    flags = event.get("flags") or []
    if any(f in flags for f in _UNCORROBORATED_FLAGS):
        return BAND_COLORS["Rumored"]
    return BAND_COLORS.get(event.get("confidence_band"), _DEFAULT_COLOR)


def _event_polygon(event: dict):
    """Return the cell polygon ring [[lon,lat],...] if the API supplied one, else None.

    Truer to the 1km cell than a point; we prefer it and fall back to a scatter dot only when an
    event somehow lacks geometry (e.g. a cell_id that didn't resolve).
    """
    geom = event.get("geometry") or {}
    if geom.get("type") == "Polygon" and geom.get("coordinates"):
        return geom["coordinates"][0]  # outer ring
    return None


def _tooltip_fields(event: dict) -> dict:
    """Flatten the fields the tooltip shows into plain strings.

    pydeck tooltips interpolate `{key}` against each datum, so list/None values must be pre-rendered
    here. Deliberately excludes anything coordinate-like — cell_id is the finest locator we expose.
    """
    flags = event.get("flags") or []
    return {
        "event_type": str(event.get("event_type", "—")),
        "confidence_band": str(event.get("confidence_band", "—")),
        "n_independent_families": str(event.get("n_independent_families", 0)),
        "flags": ", ".join(flags) if flags else "none",
        "cell_id": str(event.get("cell_id", "—")),
    }


# Plain-English meaning for each confidence colour, shown in the legend.
_BAND_MEANING = {
    "High": "confirmed by several independent sources",
    "Medium": "partly confirmed",
    "Low": "weak signal",
    "Rumored": "single source / not yet confirmed",
}


def _legend() -> None:
    """Small color->band legend so the map is readable without a separate key."""
    st.caption("What the colours mean — how well-confirmed each event is:")
    for band, rgb in BAND_COLORS.items():
        swatch = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
        st.markdown(
            f"<span style='display:inline-block;width:12px;height:12px;background:{swatch};"
            f"border-radius:2px;margin-right:6px;vertical-align:middle;'></span>"
            f"<span style='vertical-align:middle;'>{band} — {_BAND_MEANING.get(band, '')}</span>",
            unsafe_allow_html=True,
        )


def render(client, events=None) -> None:
    """Render the COP map. `events` may be pre-fetched by app.py; otherwise we load a page here."""
    import pydeck as pdk  # heavy + optional; import lazily so unit tests can import this module

    # ---- sidebar controls (band filter + substrate toggle) ----
    band_choice = st.sidebar.selectbox(
        "Show events that are…", ["All", "High", "Medium", "Low", "Rumored"], index=0,
        help="Filter the map by how well-confirmed events are.",
    )
    substrate_choice = st.sidebar.multiselect(
        "Background map layers", list(_SUBSTRATE_LAYERS),
        help="Add context to the map — borders, rivers, roads, land type.",
    )

    # ---- load events (only when app.py didn't hand us a list) ----
    if events is None:
        try:
            # band=None for "All" so the API does the filtering when a specific band is chosen.
            api_band = None if band_choice == "All" else band_choice
            events = client.get_events(band=api_band, limit=500).get("events", [])
        except Exception as exc:  # API may be down; degrade to an empty, explained map
            st.error(f"Could not load events from the API: {exc}")
            return
    elif band_choice != "All":
        # Caller pre-fetched everything; filter client-side to honor the selectbox.
        events = [e for e in events if e.get("confidence_band") == band_choice]

    if not events:
        st.info("No events to show for this area yet.")
        return

    # ---- build event layers (polygon where we have a cell ring, scatter as fallback) ----
    polygon_rows, point_rows = [], []
    for ev in events:
        color = _band_color(ev)
        tip = _tooltip_fields(ev)
        ring = _event_polygon(ev)
        if ring is not None:
            polygon_rows.append({"polygon": ring, "color": color, **tip})
        else:
            centroid = (ev.get("centroid") or {}).get("coordinates")
            if not centroid:
                continue  # no geometry at all — nothing safe to draw
            point_rows.append({"position": centroid, "color": color, **tip})

    layers = []
    if polygon_rows:
        layers.append(pdk.Layer(
            "PolygonLayer", data=polygon_rows, get_polygon="polygon",
            get_fill_color="color", get_line_color=[30, 41, 59],
            line_width_min_pixels=1, opacity=0.45, stroked=True, filled=True, pickable=True,
        ))
    if point_rows:
        layers.append(pdk.Layer(
            "ScatterplotLayer", data=point_rows, get_position="position",
            get_fill_color="color", get_radius=600,  # ~cell-scale dot for events lacking a ring
            radius_min_pixels=4, pickable=True,
        ))

    # ---- substrate overlays (lazy: only fetch the layers the analyst ticked) ----
    for layer_name in substrate_choice:
        try:
            features = client.get_layer(layer_name).get("features", [])
        except Exception as exc:
            st.warning(f"Could not load substrate layer '{layer_name}': {exc}")
            continue
        if not features:
            continue
        # GeoJsonLayer renders whatever the API returns (already cell-coarsened); muted gray so it
        # reads as background context, never competing with the colored event dots.
        layers.append(pdk.Layer(
            "GeoJsonLayer", data={"type": "FeatureCollection", "features": features},
            get_fill_color=[100, 116, 139, 40], get_line_color=[100, 116, 139, 160],
            line_width_min_pixels=1, stroked=True, filled=True, pickable=False,
        ))

    tooltip = {
        "html": (
            "<b>{event_type}</b><br/>"
            "Confidence: {confidence_band}<br/>"
            "Independent sources: {n_independent_families}<br/>"
            "Notes: {flags}<br/>"
            "Area: {cell_id}"
        ),
        "style": {"backgroundColor": "#0f172a", "color": "white"},
    }
    view_state = pdk.ViewState(longitude=_VIEW_LON, latitude=_VIEW_LAT, zoom=_VIEW_ZOOM)

    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view_state, tooltip=tooltip))
    _legend()
