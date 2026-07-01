import os

import pytest


@pytest.fixture
def conn():
    dsn = os.environ.get("DB_DSN")
    if not dsn:
        pytest.skip("no DB_DSN")
    import psycopg2
    c = psycopg2.connect(dsn)
    yield c
    c.close()


def test_admin_ref_resolves_cells(conn):
    from api.queries import _resolve_area, rollup
    units = rollup(conn, "ua_donbas", 1, None, None)
    if not units:
        pytest.skip("no admin units")
    aid = units[0]["admin_id"]
    a = _resolve_area(conn, f"admin:{aid}")
    assert a and a["kind"] == "admin" and a["theater"] == "ua_donbas"


def test_aoi_ref_matches_legacy(conn):
    from api.queries import gather_area_context, gather_area_context_ref, list_aois
    aois = list_aois(conn, "ua_donbas")
    if not aois:
        pytest.skip("no AOIs")
    aid = aois[0]["aoi_id"]
    legacy = gather_area_context(conn, aid)
    unified = gather_area_context_ref(conn, f"aoi:{aid}")
    assert unified["label"] == legacy["label"]
    assert len(unified["events"]) == len(legacy["events"])
    assert "terrain" in unified and "cell_ids" in unified


def test_unknown_ref_is_none(conn):
    from api.queries import _resolve_area
    assert _resolve_area(conn, "admin:does-not-exist") is None
    assert _resolve_area(conn, "aoi:999999") is None
