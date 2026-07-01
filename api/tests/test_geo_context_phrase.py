from api.geo_phrase import geo_phrase


def test_landcover_only():
    assert geo_phrase("cropland", None, None) == "on cropland"


def test_landcover_and_unpaved_road():
    assert geo_phrase("trees", "track", "unpaved") == "on woodland · along an unpaved track"


def test_paved_road_not_called_dirt():
    out = geo_phrase("built-up", "primary", "paved")
    assert "unpaved" not in out


def test_nothing_known():
    assert geo_phrase(None, None, None) is None
