from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.pump_fade.aggtrades_minute import (
    PUMP_FADE_AGGTRADES_MINUTE_FEATURES,
    build_minute_aggtrades_features,
)


def test_minute_aggtrades_splits_by_minute_and_computes_distribution() -> None:
    # Minute 0: three buys (aggressor bought, is_buyer_maker=False), price rises.
    # Minute 1: two aggressive sells (is_buyer_maker=True), price drops.
    price = np.asarray([100.0, 100.5, 101.0, 100.0, 99.0])
    quantity = np.asarray([1.0, 1.0, 2.0, 5.0, 5.0])
    transact_time_ms = np.asarray([1_000, 20_000, 40_000, 61_000, 90_000])
    is_buyer_maker = np.asarray([False, False, False, True, True])

    frame = build_minute_aggtrades_features(
        price=price,
        quantity=quantity,
        transact_time_ms=transact_time_ms,
        is_buyer_maker=is_buyer_maker,
    )

    assert set(frame.columns) == {"timestamp", *PUMP_FADE_AGGTRADES_MINUTE_FEATURES}
    assert len(frame) == 2
    assert frame.loc[0, "timestamp"] == 0
    assert frame.loc[1, "timestamp"] == 60_000

    minute0 = frame.loc[0]
    assert minute0["aggtrades_trade_count"] == pytest.approx(3.0)
    assert minute0["side_flip_rate"] == pytest.approx(0.0)
    assert minute0["same_side_run_mean"] == pytest.approx(3.0)
    assert minute0["sell_impact_per_notional"] == pytest.approx(0.0)
    assert minute0["buy_impact_per_notional"] > 0.0

    minute1 = frame.loc[1]
    assert minute1["aggtrades_trade_count"] == pytest.approx(2.0)
    assert minute1["buy_impact_per_notional"] == pytest.approx(0.0)
    assert minute1["sell_impact_per_notional"] > 0.0


def test_minute_aggtrades_empty_input_returns_empty_frame_with_schema() -> None:
    frame = build_minute_aggtrades_features(
        price=np.asarray([]),
        quantity=np.asarray([]),
        transact_time_ms=np.asarray([]),
        is_buyer_maker=np.asarray([]),
    )

    assert set(frame.columns) == {"timestamp", *PUMP_FADE_AGGTRADES_MINUTE_FEATURES}
    assert len(frame) == 0


def test_minute_aggtrades_rejects_misaligned_arrays() -> None:
    with pytest.raises(ValueError):
        build_minute_aggtrades_features(
            price=np.asarray([1.0, 2.0]),
            quantity=np.asarray([1.0]),
            transact_time_ms=np.asarray([0, 1]),
            is_buyer_maker=np.asarray([False, True]),
        )
