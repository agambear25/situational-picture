"""
Thin HTTP client the Streamlit screens use to reach the read-only API. One place owns the
endpoint contract; components never build URLs. httpx is imported lazily so importing a
component for a unit test does not require the full stack.

Endpoint contract (all JSON; geometry is always cell-only — see api/coarsen.py):
  GET  /healthz
  GET  /events?theater_id&status&band&flag&limit&offset    -> {events, count}
  GET  /events/{event_id}                                  -> event | 404
  GET  /cells/{cell_id}                                    -> cell  | 404
  GET  /layers/{layer}?theater_id&limit                    -> {layer, features}
  GET  /verify-queue?theater_id&limit                      -> {events}
  GET  /rejections?theater_id&limit                        -> {summary, rejections}
  POST /review        {event_id, action, reason, analyst}  -> {review_id}
  GET  /gray-band/{run_id}                                 -> fusion snapshot
  POST /label         {kind, payload, analyst, versions?, run_id?} -> {label_id}
  POST /gray-verdict  {content_hash_a, ..., same, confidence, analyst, run_id} -> {label_id}
  POST /fixtures/regenerate                                -> {paths, counts}
  POST /admin/replay?theater_id                            -> {bit_identical, dropped_obs, ...}
"""
from __future__ import annotations

from typing import Any, Optional


class CopApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", theater_id: str = "ua_donbas",
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.theater_id = theater_id
        self.timeout = timeout

    # ---- low level ----
    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        import httpx  # lazy
        r = httpx.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict, params: Optional[dict] = None) -> Any:
        import httpx  # lazy
        r = httpx.post(f"{self.base_url}{path}", json=json, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- reads ----
    def healthz(self) -> dict:
        return self._get("/healthz")

    def get_events(self, status=None, band=None, flag=None, limit=200, offset=0) -> dict:
        return self._get("/events", {
            "theater_id": self.theater_id, "status": status, "band": band, "flag": flag,
            "limit": limit, "offset": offset,
        }) or {"events": [], "count": 0}

    def get_event(self, event_id: str) -> Optional[dict]:
        return self._get(f"/events/{event_id}")

    def get_cell(self, cell_id: str) -> Optional[dict]:
        return self._get(f"/cells/{cell_id}")

    def get_layer(self, layer: str, limit=1000) -> dict:
        return self._get(f"/layers/{layer}", {"theater_id": self.theater_id, "limit": limit}) \
            or {"layer": layer, "features": []}

    def get_verify_queue(self, limit=50) -> dict:
        return self._get("/verify-queue", {"theater_id": self.theater_id, "limit": limit}) \
            or {"events": []}

    def get_rejections(self, limit=200) -> dict:
        return self._get("/rejections", {"theater_id": self.theater_id, "limit": limit}) \
            or {"summary": {"total": 0, "by_reason": {}}, "rejections": []}

    def get_gray_band(self, run_id: str = "synthetic_v1") -> dict:
        return self._get(f"/gray-band/{run_id}") or {"pairs": [], "gray_pairs": []}

    # ---- writes (append-only) ----
    def post_review(self, event_id: str, action: str, reason: str, analyst: str) -> dict:
        return self._post("/review", {
            "event_id": event_id, "action": action, "reason": reason, "analyst": analyst,
        })

    def post_label(self, kind: str, payload: dict, analyst: str,
                   versions: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
        return self._post("/label", {
            "kind": kind, "payload": payload, "analyst": analyst,
            "versions": versions, "run_id": run_id,
        })

    def post_gray_verdict(self, content_hash_a: str, content_hash_b: str,
                          obs_type_a: str, obs_type_b: str, same: bool, confidence: float,
                          analyst: str, rationale: str = "", run_id: Optional[str] = None) -> dict:
        return self._post("/gray-verdict", {
            "content_hash_a": content_hash_a, "content_hash_b": content_hash_b,
            "obs_type_a": obs_type_a, "obs_type_b": obs_type_b,
            "same": same, "confidence": confidence, "rationale": rationale,
            "analyst": analyst, "run_id": run_id,
        })

    def regenerate_fixtures(self) -> dict:
        return self._post("/fixtures/regenerate", {})

    def admin_replay(self) -> dict:
        return self._post("/admin/replay", {}, params={"theater_id": self.theater_id})
