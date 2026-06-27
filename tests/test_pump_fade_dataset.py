from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_fade.builder import (
    _BarrierIndex,
    _race,
    PumpFadeBuildError,
    build_pump_fade_symbol,
)
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig


def _config() -> PumpFadeDecisionConfig:
    return PumpFadeDecisionConfig(
        baseline_window_minutes=30,
        baseline_min_periods=10,
        activity_ignition_multiple=10.0,
        quiet_peak_fraction=0.25,
        quiet_confirmation_minutes=5,
        maximum_period_minutes=20,
        minimum_pump_size=0.05,
        minimum_atr_multiple=3.0,
        minimum_turnover=1_000.0,
    )


def _write_market(path: Path) -> None:
    rows: list[dict[str, float | int]] = []
    price = 100.0
    for index in range(40):
        timestamp = index * 60_000
        open_ = price
        close = price + 0.02
        high = max(open_, close) + 0.04
        low = min(open_, close) - 0.04
        quote_volume = 100.0
        trade_count = 10
        if index == 14:  # last red strictly before ignition
            open_, close, high, low = 100.1, 100.0, 100.15, 99.9
            price = close
        if index == 15:
            open_, close, high, low = 100.0, 102.0, 102.2, 99.95
            quote_volume, trade_count = 1_200.0, 120
            price = close
        elif index == 16:
            open_, close, high, low = 102.0, 105.5, 106.0, 101.9
            quote_volume, trade_count = 1_200.0, 120
            price = close
        elif 17 <= index <= 21:
            open_, close, high, low = price, price - 0.01, price + 0.02, price - 0.03
            quote_volume, trade_count = 20.0, 2
            price = close
        elif index == 22:
            # Exact documented invalidation: close > anchor high, but < high*1.005.
            open_, close, high, low = price, 106.1, 106.2, price - 0.02
            quote_volume, trade_count = 100.0, 10
            price = close
        else:
            price = close
        rows.append(
            {
                "timestamp": timestamp,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "quote_volume": quote_volume,
                "trade_count": trade_count,
                "taker_buy_quote_volume": quote_volume * 0.5,
            }
        )
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_builder_emits_only_new_high_decisions_with_closed_bar_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)

    result = build_pump_fade_symbol(path, config=_config())

    assert len(result) == 1
    row = result.iloc[0]
    assert row["decision_index"] == 2
    assert row["snapshot_time_ms"] == 17 * 60_000
    assert row["feature_cutoff_time_ms"] == row["snapshot_time_ms"]
    assert row["future_start_time_ms"] == row["snapshot_time_ms"] + 60_000
    assert row["anchor_high"] == 106.0
    assert row["ignition_minute_of_hour"] == 15
    assert row["ignition_minutes_from_round_hour"] == 15
    assert row["y"] == 0
    assert row["resolution_time_ms"] == 23 * 60_000


def test_early_causal_qualification_is_not_removed_by_final_period_atr(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)

    result = build_pump_fade_symbol(path, config=_config())

    # Five later quiet/low-range candles make the final-period median TR small.
    # The decision remains because qualification is absorbing and evaluated as-of.
    assert not result.empty
    assert result.iloc[0]["atr_mult"] >= 3.0


def test_close_race_has_no_fixed_1440_minute_horizon() -> None:
    closes = np.full(1_600, 105.0)
    closes[1_500] = 99.0
    barriers = _BarrierIndex(closes)

    label, resolution = _race(
        barriers,
        start=1,
        stop=len(closes),
        base=100.0,
        anchor_high=110.0,
    )

    assert label == 1
    assert resolution == 1_500


def test_close_race_invalidates_at_anchor_high_without_buffer() -> None:
    barriers = _BarrierIndex(np.asarray([105.0, 110.1, 99.0]))

    label, resolution = _race(
        barriers,
        start=1,
        stop=3,
        base=100.0,
        anchor_high=110.0,
    )

    assert label == 0
    assert resolution == 1


def test_unresolved_row_is_censored_at_first_market_data_gap(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    frame = pd.read_parquet(path)
    frame.loc[22:, "timestamp"] += 60_000
    frame.to_parquet(path, index=False)

    result = build_pump_fade_symbol(path, config=_config())

    assert len(result) == 1
    assert not bool(result.iloc[0]["label_available"])
    assert pd.isna(result.iloc[0]["y"])
    assert pd.isna(result.iloc[0]["resolution_time_ms"])


def test_pump_fade_cli_routes_progress_to_dataset_builder(
    monkeypatch, tmp_path: Path
) -> None:
    import anomaly_science.cli as cli

    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return kwargs["output_path"]

    monkeypatch.setattr(cli, "run_pump_fade_dataset_build", fake_run)
    output = tmp_path / "decisions.parquet"

    result = cli.main(
        [
            "build-pump-fade-dataset",
            "--cache-dir",
            str(tmp_path),
            "--out",
            str(output),
            "--limit-symbols",
            "30",
            "--progress-every",
            "5",
        ]
    )

    assert result == 0
    assert captured["limit_symbols"] == 30
    assert captured["progress_every"] == 5


def test_builder_rejects_invalid_market_rows(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    frame = pd.read_parquet(path)
    frame.loc[3, "high"] = frame.loc[3, "low"] - 1.0
    frame.to_parquet(path, index=False)

    with pytest.raises(PumpFadeBuildError, match="invalid OHLC"):
        build_pump_fade_symbol(path, config=_config())
