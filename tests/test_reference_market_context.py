from __future__ import annotations

from pathlib import Path
from datetime import date
import io
import zipfile
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from anomaly_science.market_context import (
    ReferenceMarketContextConfig,
    ReferenceMarketSpec,
    attach_reference_market_context,
    build_reference_market_features,
    reference_market_feature_names,
    ReferenceMetricsArchiveConfig,
    attach_reference_positioning_context,
    build_reference_positioning_features,
    reference_positioning_feature_names,
)
from anomaly_science.market_context.metrics_archive import (
    REFERENCE_METRICS_SCHEMA_VERSION,
    _parse_metrics_zip,
)
from anomaly_science.strategy.pump_fade.market_context import (
    PUMP_FADE_REFERENCE_MARKET_CONTEXT,
    PUMP_FADE_REFERENCE_POSITIONING_CONTEXT,
)
from anomaly_science.strategy.pump_fade.market_context_probability import (
    load_pump_fade_market_context_probability_config,
)


def _config() -> ReferenceMarketContextConfig:
    return ReferenceMarketContextConfig(
        schema_version="reference-unit-v1",
        references=(ReferenceMarketSpec("BTCUSDT", "btc"),),
        return_lags_minutes=(5, 15),
        oi_lags_minutes=(15,),
        taker_windows_minutes=(15,),
        range_window_minutes=15,
        activity_window_minutes=15,
        activity_baseline_minutes=30,
    )


def _market(count: int = 100) -> pd.DataFrame:
    timestamp = np.arange(count, dtype=np.int64) * 60_000
    close = 100.0 + np.arange(count, dtype=float) * 0.1
    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "quote_volume": 1_000.0 + np.arange(count, dtype=float),
            "taker_buy_quote_volume": 550.0 + np.arange(count, dtype=float) * 0.5,
            "open_interest": 10_000.0 + np.arange(count, dtype=float) * 2.0,
            "oi_available": True,
        }
    )


def test_reference_features_are_closed_minute_causal_and_future_invariant() -> None:
    config = _config()
    original = _market()
    changed = original.copy()
    changed.loc[80:, ["close", "high", "low", "quote_volume", "open_interest"]] *= 10.0

    first = build_reference_market_features(
        original, reference=config.references[0], config=config
    )
    second = build_reference_market_features(
        changed, reference=config.references[0], config=config
    )

    assert_frame_equal(first.iloc[:80], second.iloc[:80], check_exact=True)
    row = first.iloc[79]
    assert row["snapshot_time_ms"] == original.loc[79, "timestamp"] + 60_000
    assert row["btc_return_5m"] == (
        original.loc[79, "close"] / original.loc[74, "close"] - 1.0
    )


def test_reference_context_join_is_many_to_one_and_schema_explicit() -> None:
    config = _config()
    features = build_reference_market_features(
        _market(), reference=config.references[0], config=config
    )
    rows = pd.DataFrame(
        {
            "group": ["a", "b", "c"],
            "snapshot_time_ms": [60_000, 60_000, 99 * 60_000],
            "future_label": [1, 0, 1],
        }
    )

    result = attach_reference_market_context(rows, (features,), config=config)

    assert len(result) == len(rows)
    assert result["future_label"].tolist() == rows["future_label"].tolist()
    assert result["has_btc_context"].all()
    assert set(result["market_context_schema_version"]) == {"reference-unit-v1"}


def test_pump_fade_declares_btc_eth_context_without_core_branching() -> None:
    config = PUMP_FADE_REFERENCE_MARKET_CONTEXT

    assert [(item.symbol, item.alias) for item in config.references] == [
        ("BTCUSDT", "btc"),
        ("ETHUSDT", "eth"),
    ]
    names = reference_market_feature_names(config)
    assert "btc_return_60m" in names
    assert "eth_oi_change_240m" in names
    assert "btc_taker_imbalance_15m" in names
    experiment = load_pump_fade_market_context_probability_config(
        Path("research/pump_fade_market_context_probability.json")
    )
    assert experiment.context_features == names
    positioning_experiment = load_pump_fade_market_context_probability_config(
        Path("research/pump_fade_positioning_context_probability.json")
    )
    assert positioning_experiment.context_features == reference_positioning_feature_names(
        PUMP_FADE_REFERENCE_POSITIONING_CONTEXT
    )


def test_reference_metrics_parser_applies_conservative_publication_lag(tmp_path: Path) -> None:
    csv = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2026-06-01 00:05:00,BTCUSDT,100,1000,1.5,1.4,1.3,0.8\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-metrics-2026-06-01.csv", csv)
    config = ReferenceMetricsArchiveConfig(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        references=(ReferenceMarketSpec("BTCUSDT", "btc"),),
        output_dir=tmp_path,
        workers=1,
    )

    frame = _parse_metrics_zip(buffer.getvalue(), config.references[0], config)

    source = int(pd.Timestamp("2026-06-01 00:05:00", tz="UTC").timestamp() * 1000)
    assert frame.loc[0, "source_time_ms"] == source
    assert frame.loc[0, "available_time_ms"] == source + 5 * 60_000
    assert frame.loc[0, "metrics_schema_version"] == REFERENCE_METRICS_SCHEMA_VERSION
    assert frame.loc[0, "toptrader_account_long_short_ratio"] == 1.5


def test_reference_metrics_parser_drops_only_invalid_ratio_rows_with_audit(
    tmp_path: Path,
) -> None:
    csv = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2026-06-01 00:05:00,BTCUSDT,100,1000,0,1.4,1.3,0.8\n"
        "2026-06-01 00:10:00,BTCUSDT,100,1000,1.5,1.4,1.3,0.8\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-metrics-2026-06-01.csv", csv)
    config = ReferenceMetricsArchiveConfig(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        references=(ReferenceMarketSpec("BTCUSDT", "btc"),),
        output_dir=tmp_path,
        workers=1,
    )

    frame = _parse_metrics_zip(buffer.getvalue(), config.references[0], config)

    assert len(frame) == 1
    assert frame.attrs["invalid_ratio_row_count"] == 1
    assert frame.iloc[0]["toptrader_account_long_short_ratio"] == 1.5


def test_positioning_context_uses_backward_available_time_only() -> None:
    config = PUMP_FADE_REFERENCE_POSITIONING_CONTEXT
    reference = config.references[0]
    count = 60
    source = np.arange(count, dtype=np.int64) * 5 * 60_000
    metrics = pd.DataFrame(
        {
            "source_time_ms": source,
            "available_time_ms": source + 5 * 60_000,
            "symbol": reference.symbol,
            "metrics_schema_version": REFERENCE_METRICS_SCHEMA_VERSION,
            "toptrader_account_long_short_ratio": np.linspace(1.1, 1.5, count),
            "toptrader_position_long_short_ratio": np.linspace(1.2, 1.6, count),
            "global_account_long_short_ratio": np.linspace(1.0, 1.2, count),
            "taker_long_short_volume_ratio": np.linspace(0.8, 1.3, count),
        }
    )
    features = build_reference_positioning_features(
        metrics, reference=reference, config=config
    )
    eth_metrics = metrics.copy()
    eth_metrics["symbol"] = config.references[1].symbol
    eth_features = build_reference_positioning_features(
        eth_metrics, reference=config.references[1], config=config
    )
    snapshot = int(features.loc[40, "available_time_ms"] + 2 * 60_000)
    rows = pd.DataFrame({"snapshot_time_ms": [snapshot], "group": ["g"]})

    joined = attach_reference_positioning_context(
        rows, (features, eth_features),
        config=config,
    )

    assert joined.loc[0, "btc_metrics_available_time_ms"] == features.loc[40, "available_time_ms"]
    assert joined.loc[0, "btc_metrics_available_time_ms"] <= snapshot
    assert joined.loc[0, "btc_positioning_age_minutes"] == 2.0
    assert joined.loc[0, "has_btc_positioning_context"]
    assert len(reference_positioning_feature_names(config)) == 38
