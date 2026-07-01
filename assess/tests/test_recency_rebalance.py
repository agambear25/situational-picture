from datetime import datetime, timedelta, timezone

from assess.config import load_assessment_config
from assess.significance import recency


def test_floor_and_tau_make_recent_outrank_old():
    cfg = load_assessment_config()
    assert cfg.recency_floor == 0.3
    assert cfg.recency_tau_days == 10.0
    now = datetime.now(timezone.utc)

    def factor(age_days):
        return cfg.recency_floor + (1 - cfg.recency_floor) * recency(
            now - timedelta(days=age_days), now, cfg.recency_tau_days)

    fresh, old = factor(2), factor(400)
    assert fresh > 0.8            # a 2-day-old event is near the top of the recency range
    assert old < 0.4             # a 400-day-old event is near the floor
    assert (fresh - old) > 0.4   # recency now clearly differentiates recent from historical
