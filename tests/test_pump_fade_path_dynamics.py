from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.pump_fade.path_dynamics import (
    PUMP_FADE_PATH_DYNAMICS_FEATURES,
    build_path_dynamics_features,
)


def test_path_dynamics_detects_recent_flow_and_price_reversal() -> None:
    open_ = np.asarray([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    close = np.asarray([101.0, 102.0, 103.0, 104.0, 103.0, 101.0])
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    quote = np.asarray([100.0, 100.0, 100.0, 100.0, 200.0, 300.0])
    taker_buy = np.asarray([70.0, 70.0, 70.0, 70.0, 40.0, 30.0])
    history = np.linspace(99.0, 100.0, 120)

    features = build_path_dynamics_features(
        event_open=open_,
        event_high=high,
        event_low=low,
        event_close=close,
        event_quote_volume=quote,
        event_trade_count=np.full(6, 10.0),
        event_taker_buy_quote=taker_buy,
        pre_open=history,
        pre_high=history + 0.05,
        pre_low=history - 0.05,
        pre_close=history + 0.01,
    )

    assert set(features) == set(PUMP_FADE_PATH_DYNAMICS_FEATURES)
    assert features["recent_red_fraction_3m"] == pytest.approx(2.0 / 3.0)
    assert features["recent_return_3m"] < 0.0
    assert features["event_downside_path_share"] > 0.0
    assert features["recent_taker_imbalance_5m"] < features["prior_taker_imbalance_5m"]
    assert features["taker_imbalance_change_5m"] < 0.0


def test_path_dynamics_preconditioning_is_missing_without_history() -> None:
    unit = np.asarray([1.0])
    features = build_path_dynamics_features(
        event_open=unit,
        event_high=unit,
        event_low=unit,
        event_close=unit,
        event_quote_volume=unit,
        event_trade_count=unit,
        event_taker_buy_quote=unit * 0.5,
        pre_open=np.asarray([]),
        pre_high=np.asarray([]),
        pre_low=np.asarray([]),
        pre_close=np.asarray([]),
    )

    assert np.isnan(features["pre_realized_volatility_60m"])
    assert features["event_return_sign_entropy"] == pytest.approx(1.0)
