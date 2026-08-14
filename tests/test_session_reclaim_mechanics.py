from __future__ import annotations

import pandas as pd

from anomaly_science.strategy.session_reclaim.mechanics import MechanicsVariant, simulate_variant
from anomaly_science.strategy.session_reclaim.spec import (
    EntryPolicy,
    IS_START_MS,
    InvalidationPolicy,
    MechanicsConfig,
    ProfitPolicy,
)


def _event() -> pd.Series:
    return pd.Series(
        {
            "event_id": "event-1",
            "symbol": "TESTUSDT",
            "signal_time_ms": IS_START_MS,
            "order_activation_time_ms": IS_START_MS,
            "reference_high": 110.0,
            "reference_mid": 100.0,
            "reference_low": 90.0,
            "poke_high": 112.0,
            "upper_structure_price": 120.0,
        }
    )


def _path(rows: list[tuple[float, float, float, float]], *, start_ms: int = IS_START_MS) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": start_ms + index * 60_000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
            }
            for index, (open_price, high, low, close) in enumerate(rows)
        ]
    )


def test_same_minute_stop_target_race_uses_adverse_stop_ordering() -> None:
    variant = MechanicsVariant(
        "market_poke_mid",
        EntryPolicy.MARKET_RECLAIM,
        InvalidationPolicy.POKE_TOUCH,
        ProfitPolicy.MID_FULL,
    )
    path = _path([(108.0, 112.5, 99.0, 105.0)])

    result = simulate_variant(_event(), path, variant, config=MechanicsConfig(horizon_minutes=1))

    assert result["status"] == "resolved"
    assert result["exit_reason"] == "hard_stop"
    assert result["gross_bps"] < 0


def test_close_invalidation_survives_wick_and_exits_at_next_minute_open() -> None:
    variant = MechanicsVariant(
        "market_close1_mid",
        EntryPolicy.MARKET_RECLAIM,
        InvalidationPolicy.CLOSE1_UPPERCAT,
        ProfitPolicy.MID_FULL,
    )
    rows = [(108.0, 111.0, 106.0, 108.0)] * 59
    rows.append((108.0, 115.0, 107.0, 111.0))
    rows.append((109.0, 110.0, 108.0, 109.0))
    path = _path(rows)

    result = simulate_variant(_event(), path, variant, config=MechanicsConfig(horizon_minutes=61))

    assert result["status"] == "resolved"
    assert result["exit_reason"] == "close_invalidation"
    assert result["exit_time_ms"] == IS_START_MS + 60 * 60_000
    assert result["exit_price"] == 109.0


def test_maker_primary_requires_strict_trade_through_not_a_touch() -> None:
    variant = MechanicsVariant(
        "maker_upper_mid",
        EntryPolicy.MAKER_REFERENCE_HIGH,
        InvalidationPolicy.UPPER_STRUCTURE,
        ProfitPolicy.MID_FULL,
    )
    path = _path(
        [
            (108.0, 110.0, 107.0, 109.0),
            (109.0, 110.1, 108.5, 109.5),
            (109.5, 110.0, 99.0, 100.0),
        ]
    )

    result = simulate_variant(_event(), path, variant, config=MechanicsConfig(horizon_minutes=3))

    assert result["status"] == "resolved"
    assert result["entry_time_ms"] == IS_START_MS + 60_000
    assert result["entry_price"] == 110.0
    assert result["entry_liquidity"] == "maker_trade_through"
    assert result["exit_reason"] == "target_mid"


def test_unresolved_short_path_is_retained_as_censored() -> None:
    variant = MechanicsVariant(
        "market_upper_mid",
        EntryPolicy.MARKET_RECLAIM,
        InvalidationPolicy.UPPER_STRUCTURE,
        ProfitPolicy.MID_FULL,
    )
    path = _path([(108.0, 109.0, 107.0, 108.0)] * 5)

    result = simulate_variant(_event(), path, variant, config=MechanicsConfig(horizon_minutes=60))

    assert result["status"] == "censored"
    assert result["open_size_at_censor"] == 1.0


def test_complete_horizon_uses_explicit_time_exit() -> None:
    variant = MechanicsVariant(
        "market_upper_mid",
        EntryPolicy.MARKET_RECLAIM,
        InvalidationPolicy.UPPER_STRUCTURE,
        ProfitPolicy.MID_FULL,
    )
    path = _path([(108.0, 109.0, 107.0, 107.5)] * 5)

    result = simulate_variant(_event(), path, variant, config=MechanicsConfig(horizon_minutes=5))

    assert result["status"] == "resolved"
    assert result["exit_reason"] == "time_exit"
    assert result["duration_minutes"] == 5
