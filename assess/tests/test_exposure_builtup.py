from assess.config import load_assessment_config
from assess.exposure import exposure


def _cfg():
    return load_assessment_config()


def test_builtup_beats_proxy_when_present():
    cfg = _cfg()
    settlements = [{"name": "Town", "lon": 37.0, "lat": 48.0}]
    hi = {"lon": 37.05, "lat": 48.0, "event_type": "strike", "builtup_pct": 0.9}
    lo = {"lon": 37.05, "lat": 48.0, "event_type": "strike", "builtup_pct": 0.0}
    assert exposure(hi, settlements, cfg)["score"] > exposure(lo, settlements, cfg)["score"]


def test_falls_back_to_proxy_when_builtup_none():
    cfg = _cfg()
    settlements = [{"name": "Town", "lon": 37.0, "lat": 48.0}]
    ev = {"lon": 37.0, "lat": 48.0, "event_type": "strike", "builtup_pct": None}
    assert exposure(ev, settlements, cfg) is not None    # proxy path still works
