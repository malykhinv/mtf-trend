from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_long.builder import build_pump_long_outcomes
from anomaly_science.strategy.pump_long.spec import (
    STOP_VARIANT_CONFIRMED_SWING_LOW,
    STOP_VARIANT_EVENT_BASE,
)


def _write_symbol_market(path: Path) -> None:
    rows = []
    price = 100.0
    for index in range(40):
        open_ = price
        if index < 15:
            close = price + 0.01
        elif index <= 17:
            close = price + 2.0  # pump: 100 -> ~106 by bar 17
        elif index <= 20:
            close = price - 0.5
        else:
            close = price - 2.0  # collapse through the base
        high = max(open_, close) + 0.1
        low = min(open_, close) - 0.1
        rows.append(
            {
                "timestamp": index * 60_000,
                "open": open_, "high": high, "low": low, "close": close,
                "quote_volume": 100.0, "trade_count": 10,
            }
        )
        price = close
    pd.DataFrame(rows).to_parquet(path, index=False)


def _write_decisions(path: Path, *, symbol: str, event_id: str) -> None:
    pd.DataFrame(
        [
            {
                "event_id": event_id,
                "group": event_id,
                "symbol": symbol,
                "snapshot_time_ms": 18 * 60_000,  # decision bar 17, entry bar 18
                "decision_index": 1,
                "base_level": 100.0,
                "anchor_high": 106.2,
                "pump_elapsed_min": 3.0,
                "recurrence_chain_id": f"{symbol}:chain",
                "n_prior_48h": 0,
                "last_prior_faded": float("nan"),
                "frac_prior_faded_48h": float("nan"),
                "min_since_last_prior": float("nan"),
                "remaining_to_base": 0.05,
                "is_nature_anchor": True,
                "y": 1,
                "label_available": True,
            }
        ]
    ).to_parquet(path, index=False)


def test_builder_emits_both_stop_variants_with_causal_entry(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_symbol_market(cache_dir / "TEST.parquet")
    decisions_path = tmp_path / "decisions.parquet"
    _write_decisions(decisions_path, symbol="TEST", event_id="TEST:900000")
    output_path = tmp_path / "outcomes.parquet"

    stats = build_pump_long_outcomes(
        decisions_path=decisions_path,
        cache_dir=cache_dir,
        output_path=output_path,
        workers=1,
    )

    assert len(stats) == 1
    outcomes = pd.read_parquet(output_path)
    assert set(outcomes["stop_variant"]) == {
        STOP_VARIANT_EVENT_BASE,
        STOP_VARIANT_CONFIRMED_SWING_LOW,
    }
    assert len(outcomes) == 2
    base_row = outcomes.loc[outcomes["stop_variant"] == STOP_VARIANT_EVENT_BASE].iloc[0]
    assert base_row["status"] == "filled"
    assert base_row["entry_time_ms"] == 18 * 60_000
    # Entry fills at bar-18 open plus registered slippage.
    expected_entry = pytest.approx(base_row["entry_price"], rel=1e-9)
    market = pd.read_parquet(cache_dir / "TEST.parquet")
    assert market.loc[18, "open"] * (1.0 + 5.0 / 10_000.0) == expected_entry
    assert bool(base_row["entry_eligible"])
    assert base_row["exit_reason"] in {"initial_stop", "trailing_stop"}
    assert base_row["net_return"] < 0.0  # this synthetic pump collapses
    assert np.isfinite(base_row["net_r"])
