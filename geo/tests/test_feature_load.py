"""Offline gate for the OSM tag → (kind, subkind) classification (pure; no pbf, no DB)."""
from __future__ import annotations

from geo.feature_load import classify_feature


def test_river_is_water():
    assert classify_feature({"waterway": "river"}) == ("water", "river")
    assert classify_feature({"natural": "water"}) == ("water", "waterbody")


def test_forest_from_either_tag():
    assert classify_feature({"natural": "wood"}) == ("forest", "wood")
    assert classify_feature({"landuse": "forest"}) == ("forest", "forest")


def test_major_roads_only():
    assert classify_feature({"highway": "motorway"}) == ("road", "motorway")
    assert classify_feature({"highway": "primary"}) == ("road", "primary")
    assert classify_feature({"highway": "residential"}) is None   # minor road → skipped


def test_rail_and_builtup():
    assert classify_feature({"railway": "rail"}) == ("rail", "rail")
    assert classify_feature({"landuse": "industrial"}) == ("builtup", "industrial")


def test_untagged_skipped():
    assert classify_feature({}) is None
    assert classify_feature({"amenity": "cafe"}) is None


def test_water_precedence_over_landuse():
    # a waterway tag wins even if other keys are present
    assert classify_feature({"waterway": "canal", "landuse": "industrial"}) == ("water", "canal")
