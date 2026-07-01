from fusion.config import load_fusion_config


def test_naval_transit_plausible_on_water_and_in_port():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("naval_transit", 80) == 1.0       # water OK
    assert cfg.landcover_penalty("naval_transit", 50) == 1.0       # built-up = a port cell, OK
    assert cfg.landcover_penalty("naval_transit", 40) == 0.2       # cropland (clearly inland) penalized


def test_damage_implausible_over_water():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("building_damaged", 80) == 0.5    # water penalized
    assert cfg.landcover_penalty("building_damaged", 50) == 1.0    # built-up OK


def test_no_rule_or_no_data_is_neutral():
    cfg = load_fusion_config()
    assert cfg.landcover_penalty("strike", 40) == 1.0              # no rule for this type
    assert cfg.landcover_penalty("naval_transit", None) == 1.0     # unpopulated cell → neutral
