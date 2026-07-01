from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_fade.cvd import (
    PUMP_FADE_CVD_MODEL_FEATURES,
    PUMP_FADE_CVD_SCHEMA_VERSION,
    build_pump_fade_cvd_features,
)
from anomaly_science.strategy.pump_fade.market_context_probability import (
    load_pump_fade_market_context_probability_config,
)


def test_cvd_path_features_match_registered_closed_minute_formulas() -> None:
    quote = np.full(10, 100.0)
    taker = np.array([60, 60, 60, 60, 60, 40, 40, 40, 40, 40], dtype=float)
    close = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109], dtype=float)

    result = build_pump_fade_cvd_features(
        quote_volume=quote,
        taker_buy_quote_volume=taker,
        close=close,
    )

    assert result["cvd_available"] is True
    assert result["cvd_imbalance_5m"] == pytest.approx(-0.2)
    assert result["cvd_imbalance_15m"] == pytest.approx(0.0)
    assert result["cvd_acceleration_5m"] == pytest.approx(-0.4)
    assert result["cvd_drawdown_from_peak"] == pytest.approx(-0.1)
    assert result["cvd_new_high_confirmation_margin"] == pytest.approx(-0.1)
    assert result["cvd_path_efficiency"] == pytest.approx(0.0)
    assert result["cvd_positive_minute_fraction"] == pytest.approx(0.5)
    assert result["price_up_cvd_down_5m"] is True
    assert result["cvd_failed_to_confirm_high"] is True


@pytest.mark.parametrize(
    ("quote", "taker", "close"),
    (
        ([100.0, 100.0], [50.0, np.nan], [100.0, 101.0]),
        ([100.0, 100.0], [50.0, 101.0], [100.0, 101.0]),
        ([100.0, -1.0], [50.0, 0.0], [100.0, 101.0]),
    ),
)
def test_invalid_cvd_source_is_explicitly_unavailable(
    quote: list[float], taker: list[float], close: list[float]
) -> None:
    result = build_pump_fade_cvd_features(
        quote_volume=np.asarray(quote),
        taker_buy_quote_volume=np.asarray(taker),
        close=np.asarray(close),
    )

    assert result["cvd_available"] is False
    assert all(pd.isna(result[name]) for name in PUMP_FADE_CVD_MODEL_FEATURES)


def test_cvd_prefix_is_invariant_to_unseen_future_tail() -> None:
    quote = np.full(12, 100.0)
    taker = np.linspace(40.0, 70.0, 12)
    close = np.linspace(100.0, 112.0, 12)
    cutoff = 8

    expected = build_pump_fade_cvd_features(
        quote_volume=quote[:cutoff],
        taker_buy_quote_volume=taker[:cutoff],
        close=close[:cutoff],
    )
    altered_taker = taker.copy()
    altered_close = close.copy()
    altered_taker[cutoff:] = 0.0
    altered_close[cutoff:] = 1_000.0
    actual = build_pump_fade_cvd_features(
        quote_volume=quote[:cutoff],
        taker_buy_quote_volume=altered_taker[:cutoff],
        close=altered_close[:cutoff],
    )

    assert actual == expected


def test_registered_cvd_probability_protocol_is_exact() -> None:
    path = Path("research/pump_fade_cvd_probability.json")
    config = load_pump_fade_market_context_probability_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert config.context_family == PUMP_FADE_CVD_SCHEMA_VERSION
    assert config.context_features == PUMP_FADE_CVD_MODEL_FEATURES
    assert config.required_true_columns == ("cvd_available",)
    assert raw["context_features"] == list(PUMP_FADE_CVD_MODEL_FEATURES)
