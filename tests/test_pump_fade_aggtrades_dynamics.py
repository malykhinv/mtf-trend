from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.pump_fade.aggtrades_dynamics import (
    PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES,
    build_aggtrades_dynamics_features,
)


def test_aggtrades_dynamics_returns_all_nan_when_sidecar_unavailable() -> None:
    unit = np.full(6, np.nan)
    features = build_aggtrades_dynamics_features(
        event_trade_notional_p90=unit,
        event_notional_gini=unit,
        event_same_side_run_mean=unit,
        event_side_sign_entropy=unit,
        event_side_flip_rate=unit,
        event_inter_arrival_ms_mean=unit,
        event_buy_impact_per_notional=unit,
        event_sell_impact_per_notional=unit,
        event_aggtrades_trade_count=unit,
        pre_trade_notional_p90=unit,
        pre_side_sign_entropy=unit,
        pre_inter_arrival_ms_mean=unit,
        pre_buy_impact_per_notional=unit,
        pre_sell_impact_per_notional=unit,
    )

    assert set(features) == set(PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES)
    assert features["aggtrades_available"] == pytest.approx(0.0)
    for name, value in features.items():
        if name == "aggtrades_available":
            continue
        assert np.isnan(value)


def test_aggtrades_dynamics_detects_buy_impact_decay_and_sell_impact_rise() -> None:
    n = 10
    trade_count = np.full(n, 20.0)
    notional_p90 = np.linspace(100.0, 200.0, n)
    gini = np.linspace(0.3, 0.5, n)
    run_mean = np.linspace(3.0, 2.0, n)
    sign_entropy = np.linspace(0.9, 0.6, n)
    flip_rate = np.linspace(0.4, 0.2, n)
    inter_arrival = np.linspace(500.0, 300.0, n)
    # Buy impact fades over the event (absorption); sell impact rises (thin book below).
    buy_impact = np.linspace(0.002, 0.0002, n)
    sell_impact = np.linspace(0.0005, 0.003, n)
    pre_history = np.linspace(1.0, 1.0, 60)

    features = build_aggtrades_dynamics_features(
        event_trade_notional_p90=notional_p90,
        event_notional_gini=gini,
        event_same_side_run_mean=run_mean,
        event_side_sign_entropy=sign_entropy,
        event_side_flip_rate=flip_rate,
        event_inter_arrival_ms_mean=inter_arrival,
        event_buy_impact_per_notional=buy_impact,
        event_sell_impact_per_notional=sell_impact,
        event_aggtrades_trade_count=trade_count,
        pre_trade_notional_p90=pre_history * 50.0,
        pre_side_sign_entropy=pre_history * 0.8,
        pre_inter_arrival_ms_mean=pre_history * 400.0,
        pre_buy_impact_per_notional=pre_history * 0.001,
        pre_sell_impact_per_notional=pre_history * 0.001,
    )

    assert set(features) == set(PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES)
    assert features["aggtrades_available"] == pytest.approx(1.0)
    assert features["buy_impact_decay_5m"] < 0.0
    assert features["sell_impact_change_5m"] > 0.0
    assert features["notional_gini_change_5m"] > 0.0
    assert features["pre_trade_notional_p90_60m"] == pytest.approx(50.0)
