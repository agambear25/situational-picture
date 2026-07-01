from geo.layers.transport import classify_surface


def test_explicit_unpaved():
    assert classify_surface("primary", "dirt") == "unpaved"
    assert classify_surface("secondary", "gravel") == "unpaved"


def test_explicit_paved():
    assert classify_surface("primary", "asphalt") == "paved"
    assert classify_surface("residential", "paved") == "paved"


def test_track_is_unpaved_by_default():
    assert classify_surface("track", None) == "unpaved"
    assert classify_surface("path", None) == "unpaved"


def test_major_road_unknown_surface_defaults_paved():
    assert classify_surface("motorway", None) == "paved"
    assert classify_surface("primary", None) == "paved"


def test_unknown_minor():
    assert classify_surface("unclassified", None) == "unknown"
