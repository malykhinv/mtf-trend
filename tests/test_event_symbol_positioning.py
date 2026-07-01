from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.market_context import (
    EventScopedPositioningContextConfig,
    attach_event_positioning_context,
    build_event_positioning_features,
    derive_event_metrics_scope,
    event_positioning_feature_names,
)
from anomaly_science.market_context.metrics_archive import (
    REFERENCE_METRICS_SCHEMA_VERSION,
)
from anomaly_science.strategy.pump_fade.market_context import (
    PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
)
from anomaly_science.strategy.pump_fade.market_context_probability import (
    load_pump_fade_market_context_probability_config,
)


MINUTE = 60_000


def _metrics(symbol: str, *, multiplier: float = 1.0) -> pd.DataFrame:
    source = np.arange(0, 100 * MINUTE, 5 * MINUTE, dtype=np.int64)
    trend = np.arange(len(source), dtype=float)
    return pd.DataFrame(
        {
            "source_time_ms": source,
            "available_time_ms": source + 5 * MINUTE,
            "symbol": symbol,
            "alias": "symbol",
            "toptrader_account_long_short_ratio": multiplier * np.exp(0.010 * trend),
            "toptrader_position_long_short_ratio": multiplier * np.exp(0.015 * trend),
            "global_account_long_short_ratio": multiplier * np.exp(0.005 * trend),
            "taker_long_short_volume_ratio": multiplier * np.exp(-0.004 * trend),
            "metrics_schema_version": REFERENCE_METRICS_SCHEMA_VERSION,
        }
    )


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["AAA:1", "BBB:1"],
            "symbol": ["AAAUSDT", "BBBUSDT"],
            "ignition_time_ms": [35 * MINUTE, 35 * MINUTE],
            "snapshot_time_ms": [70 * MINUTE, 70 * MINUTE],
            "feature_cutoff_time_ms": [70 * MINUTE, 70 * MINUTE],
        }
    )


def test_same_symbol_positioning_is_backward_asof_and_event_relative() -> None:
    config = PUMP_FADE_SYMBOL_POSITIONING_CONTEXT
    aaa = build_event_positioning_features(
        _metrics("AAAUSDT"), symbol="AAAUSDT", config=config
    )
    bbb = build_event_positioning_features(
        _metrics("BBBUSDT", multiplier=2.0), symbol="BBBUSDT", config=config
    )

    joined = attach_event_positioning_context(_rows(), (aaa, bbb), config=config)

    assert joined["symbol_positioning_complete"].all()
    assert (joined["metrics_available_time_ms"] == 70 * MINUTE).all()
    assert (joined["ignition_metrics_available_time_ms"] == 35 * MINUTE).all()
    aaa_row = joined.loc[joined["symbol"] == "AAAUSDT"].iloc[0]
    assert aaa_row["symbol_pos_toptrader_account_log_change_15m"] == pytest.approx(0.03)
    assert aaa_row["symbol_pos_toptrader_account_log_change_60m"] == pytest.approx(0.12)
    assert aaa_row[
        "symbol_pos_toptrader_account_log_change_since_ignition"
    ] == pytest.approx(0.07)
    assert aaa_row["symbol_pos_toptrader_position_minus_global"] == pytest.approx(0.13)


def test_event_positioning_future_tail_cannot_change_online_row() -> None:
    config = PUMP_FADE_SYMBOL_POSITIONING_CONTEXT
    original = _metrics("AAAUSDT")
    changed = original.copy()
    changed.loc[changed["available_time_ms"] > 70 * MINUTE, [
        "toptrader_account_long_short_ratio",
        "toptrader_position_long_short_ratio",
        "global_account_long_short_ratio",
        "taker_long_short_volume_ratio",
    ]] *= 1000.0
    row = _rows().iloc[[0]].copy()

    first = attach_event_positioning_context(
        row,
        (build_event_positioning_features(original, symbol="AAAUSDT", config=config),),
        config=config,
    )
    second = attach_event_positioning_context(
        row,
        (build_event_positioning_features(changed, symbol="AAAUSDT", config=config),),
        config=config,
    )

    pd.testing.assert_frame_equal(first, second)


def test_event_metrics_scope_includes_history_and_cross_midnight_source_day(
    tmp_path: Path,
) -> None:
    snapshot = int(pd.Timestamp("2026-01-02T00:08:00Z").timestamp() * 1000)
    anchor = int(pd.Timestamp("2026-01-01T23:50:00Z").timestamp() * 1000)
    path = tmp_path / "rows.parquet"
    pd.DataFrame(
        {
            "symbol": ["AAAUSDT"],
            "snapshot_time_ms": [snapshot],
            "ignition_time_ms": [anchor],
        }
    ).to_parquet(path, index=False)

    scope = derive_event_metrics_scope(
        (path,),
        positioning=PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
        symbol_column="symbol",
        snapshot_time_column="snapshot_time_ms",
        anchor_time_column="ignition_time_ms",
    )

    assert len(scope) == 1
    assert tuple(day.isoformat() for day in scope[0].days) == (
        "2026-01-01",
        "2026-01-02",
    )
    start, end = scope[0].available_intervals_ms[0]
    assert start == anchor - 70 * MINUTE
    assert end == snapshot


def test_symbol_positioning_protocol_is_exactly_strategy_declared() -> None:
    config = load_pump_fade_market_context_probability_config(
        Path("research/pump_fade_symbol_positioning_probability.json")
    )

    assert config.context_family == "event_scoped_symbol_positioning_v1"
    assert config.context_features == event_positioning_feature_names(
        PUMP_FADE_SYMBOL_POSITIONING_CONTEXT
    )
    assert config.required_true_columns == ("symbol_positioning_complete",)
    assert len(config.context_features) == 19


def test_event_positioning_config_rejects_misaligned_lags() -> None:
    with pytest.raises(ValueError, match="align"):
        EventScopedPositioningContextConfig(
            schema_version="bad",
            change_lags_minutes=(7,),
        )
