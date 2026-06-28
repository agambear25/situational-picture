"""
Hard license enforcement test.
MUST PASS before any control overlay feature is merged.
"""
import pytest
from geo.control import assert_source_permitted, FORBIDDEN_SOURCES


def test_isw_forbidden():
    with pytest.raises(ValueError, match="FORBIDDEN"):
        assert_source_permitted("isw")


def test_deepstatemap_forbidden():
    with pytest.raises(ValueError, match="FORBIDDEN"):
        assert_source_permitted("deepstatemap")


def test_deepstate_variant_forbidden():
    with pytest.raises(ValueError, match="FORBIDDEN"):
        assert_source_permitted("DeepState")


def test_institute_for_study_of_war_forbidden():
    with pytest.raises(ValueError, match="FORBIDDEN"):
        assert_source_permitted("Institute_for_the_Study_of_War")


def test_own_derived_permitted():
    # Should NOT raise
    assert_source_permitted("own_derived")


def test_ua_mod_permitted():
    assert_source_permitted("ua_mod_press")


def test_all_forbidden_sources_blocked():
    for source in FORBIDDEN_SOURCES:
        with pytest.raises(ValueError, match="FORBIDDEN"):
            assert_source_permitted(source)
