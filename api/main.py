"""
FastAPI app factory — the read-only COP API.

Localhost-bound (config/api.yaml), read-only over the event-sourced read model, with the
coarsening boundary (api/coarsen.py) enforced on every geometry. The only writes are the two
append-only annotation endpoints (POST /review, POST /label, POST /gray-verdict).

Run:  uvicorn api.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.deps import get_settings
from api.routers import admin, cells, events, labeling, layers, review

# The static HTML operator UI (web/) is served by this same app at /ui/, so the front-end and
# the API share one origin (no CORS) and one command (`uvicorn api.main:app`) runs everything.
_WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="OSINT COP API",
        version="0.1.0",
        description=(
            "Read-only Common Operating Picture over the event-sourced read model. "
            "All geometry is 1km-cell only (analytical-not-targeting); the engine write-path "
            "is unreachable from this service."
        ),
    )
    if s.get("cors_origins"):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s["cors_origins"],
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
    # admin first (owns /healthz); order is cosmetic — paths are absolute.
    for module in (admin, events, cells, layers, review, labeling):
        app.include_router(module.router)

    # Operator UI at /ui/ (html=True serves index.html); "/" redirects there for convenience.
    if _WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def _root():
            return RedirectResponse(url="/ui/")

    return app


app = create_app()
