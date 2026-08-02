import numpy as np
from anomaly_science.strategy.daily_anomaly_hold.research.zone_metrics import zone_birth_features, zone_outcome


def test_zone_metrics_keep_future_outcome_separate_from_birth_features():
    n = 30
    o = c = np.full(n, 100.0); h = np.full(n, 101.0); l = np.full(n, 99.0)
    v = t = np.ones(n)
    features = zone_birth_features(high=h, low=l, open_price=o, close=c, volume=v, trades=t, start=20, end=21, lower=99, upper=101, tf_minutes=15)
    assert features["feature_cutoff_index"] == 22
    assert zone_outcome(high=h, low=l, close=c, touch_index=23, lower=99, upper=101, kind="demand") == "consumed"
