from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from config import AppConfig, BacktestConfig, FetchConfig, SimulationConfig, StrategyConfig
from domain.enums.timeframe import Timeframe
from strategy.factory import build_strategy
from strategy.post_pump_absorption.config import (
    PostPumpAbsorptionParams,
    PostPumpAbsorptionRuntime,
    build_post_pump_absorption_grid,
    build_post_pump_absorption_runtime,
)
from strategy.post_pump_absorption.engine import PostPumpAbsorptionEngine


def _row(
    timestamp: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    taker_ratio: float | None,
) -> dict[str, float | int]:
    payload: dict[str, float | int] = {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    if taker_ratio is not None:
        payload["taker_buy_ratio"] = taker_ratio
        payload["taker_buy_volume"] = volume * taker_ratio
    return payload


def _seed_post_pump_frame(
    timeframe: Timeframe,
    runtime: PostPumpAbsorptionRuntime,
) -> tuple[list[dict[str, float | int]], int, int]:
    rows: list[dict[str, float | int]] = []
    timestamp = 0
    step_ms = timeframe.to_milliseconds()

    baseline_bars = runtime.pump_baseline_window_bars + 10
    for idx in range(baseline_bars):
        price = 100.0 + (0.02 * (idx % 5))
        rows.append(
            _row(
                timestamp,
                open_price=price,
                high=price + 0.50,
                low=price - 0.50,
                close=price + 0.05,
                volume=100.0,
                taker_ratio=0.50,
            )
        )
        timestamp += step_ms

    pump_closes = np.linspace(101.0, 106.0, runtime.pump_window_bars)
    for idx, close in enumerate(pump_closes):
        rows.append(
            _row(
                timestamp,
                open_price=close - 0.60,
                high=close + 0.70,
                low=close - 0.80,
                close=close,
                volume=160.0 + idx,
                taker_ratio=0.52,
            )
        )
        timestamp += step_ms

    return rows, timestamp, step_ms


def _build_mbb_frame(
    timeframe: Timeframe,
    runtime: PostPumpAbsorptionRuntime,
    *,
    breakout_spike_high: float | None = None,
    second_cycle: bool = False,
) -> tuple[pd.DataFrame, float]:
    rows, timestamp, step_ms = _seed_post_pump_frame(timeframe, runtime)

    decline_bars = max(runtime.range_min_bars - runtime.micro_base_bars, 2)
    range_rows: list[tuple[float, float, float, float, float, float]] = []
    for idx in range(decline_bars):
        close = 105.1 - (1.7 * ((idx + 1) / max(decline_bars, 1)))
        range_rows.append((close + 0.25, close + 0.55, close - 0.35, close, 118.0 - idx, 0.48))

    for idx in range(runtime.micro_base_bars):
        close = 103.45 + (0.18 * ((idx + 1) / max(runtime.micro_base_bars, 1)))
        range_rows.append((close - 0.02, close + 0.12, close - 0.20, close, 108.0 + idx, 0.50))

    expected_range_high = max(high for _, high, _, _, _, _ in range_rows)
    breakout_high = breakout_spike_high if breakout_spike_high is not None else 103.95
    range_rows.extend(
        [
            (103.68, breakout_high, 103.30, 103.92, 180.0, 0.75),
            (103.92, 104.25, 103.90, 104.12, 150.0, 0.62),
            (104.12, 105.40, 104.05, 105.10, 165.0, 0.60),
            (105.10, 105.90, 104.90, 105.60, 170.0, 0.60),
        ]
    )

    if second_cycle:
        range_rows.extend(
            [
                (105.10, 105.20, 104.10, 104.35, 120.0, 0.47),
                (104.25, 104.28, 104.05, 104.18, 108.0, 0.49),
                (104.20, 104.35, 104.00, 104.18, 109.0, 0.50),
                (104.18, 104.30, 104.02, 104.22, 111.0, 0.50),
                (104.22, 104.38, 104.05, 104.24, 112.0, 0.50),
                (104.24, 104.80, 104.00, 104.45, 260.0, 0.82),
                (104.45, 105.15, 104.35, 104.95, 155.0, 0.60),
                (104.95, 105.95, 104.80, 105.80, 172.0, 0.60),
            ]
        )

    for open_price, high, low, close, volume, taker_ratio in range_rows:
        rows.append(
            _row(
                timestamp,
                open_price=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                taker_ratio=taker_ratio,
            )
        )
        timestamp += step_ms

    return pd.DataFrame(rows), expected_range_high


def _build_lsb_frame(timeframe: Timeframe, runtime: PostPumpAbsorptionRuntime) -> pd.DataFrame:
    rows, timestamp, step_ms = _seed_post_pump_frame(timeframe, runtime)

    range_rows = [
        (104.80, 105.00, 104.40, 104.60, 118.0, 0.48),
        (104.60, 104.00, 103.80, 103.90, 116.0, 0.47),
        (103.90, 104.15, 103.70, 104.00, 114.0, 0.49),
        (104.00, 103.90, 103.55, 103.70, 112.0, 0.48),
        (103.70, 103.95, 103.60, 103.75, 111.0, 0.50),
        (103.75, 103.80, 103.52, 103.65, 110.0, 0.50),
        (103.65, 104.05, 103.60, 103.99, 182.0, 0.78),
        (103.99, 104.40, 103.95, 104.28, 155.0, 0.63),
        (104.28, 104.92, 104.15, 104.75, 168.0, 0.60),
        (104.75, 105.05, 104.60, 104.95, 170.0, 0.60),
    ]

    for open_price, high, low, close, volume, taker_ratio in range_rows:
        rows.append(
            _row(
                timestamp,
                open_price=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                taker_ratio=taker_ratio,
            )
        )
        timestamp += step_ms

    return pd.DataFrame(rows)


@pytest.mark.parametrize("timeframe", [Timeframe.M1, Timeframe.M3, Timeframe.M5])
def test_post_pump_absorption_engine_generates_trade_on_micro_base_breakout(timeframe: Timeframe) -> None:
    engine = PostPumpAbsorptionEngine()
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=timeframe,
        entry_timeframe=timeframe,
    )
    runtime = build_post_pump_absorption_runtime(params)
    frame, _ = _build_mbb_frame(timeframe, runtime)

    trades = engine.generate_events(frame, params)

    assert len(trades) == 1
    assert trades[0].metadata is not None
    assert trades[0].metadata["setup_type"] == "MBB"
    assert trades[0].result_type.value in {"TP1_BE", "TP2"}
    assert trades[0].pnl > 0


def test_post_pump_absorption_engine_generates_trade_on_local_structure_break() -> None:
    engine = PostPumpAbsorptionEngine()
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M3,
        entry_timeframe=Timeframe.M3,
    )
    runtime = build_post_pump_absorption_runtime(params)
    frame = _build_lsb_frame(Timeframe.M3, runtime)

    trades = engine.generate_events(frame, params)

    assert len(trades) == 1
    assert trades[0].metadata is not None
    assert trades[0].metadata["setup_type"] == "LSB"
    assert trades[0].metadata["stop_range_fraction"] < 0.45
    assert trades[0].pnl > 0


def test_post_pump_absorption_locks_range_before_trigger_bar() -> None:
    engine = PostPumpAbsorptionEngine()
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M3,
        entry_timeframe=Timeframe.M3,
    )
    runtime = build_post_pump_absorption_runtime(params)
    frame, expected_range_high = _build_mbb_frame(Timeframe.M3, runtime, breakout_spike_high=106.50)

    trades = engine.generate_events(frame, params)

    assert len(trades) == 1
    assert trades[0].metadata is not None
    assert trades[0].metadata["range_high"] == pytest.approx(expected_range_high)


def test_post_pump_absorption_can_generate_reentries_within_single_regime() -> None:
    engine = PostPumpAbsorptionEngine()
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M3,
        entry_timeframe=Timeframe.M3,
    )
    runtime = build_post_pump_absorption_runtime(params)
    frame, _ = _build_mbb_frame(Timeframe.M3, runtime, second_cycle=True)

    trades = engine.generate_events(frame, params)
    diagnostics = engine.consume_last_generation_diagnostics()

    assert len(trades) == 2
    assert diagnostics["reentries_generated"] == 1
    assert diagnostics["trades_generated"] == 2


def test_post_pump_absorption_reports_missing_taker_data_without_silent_failure() -> None:
    engine = PostPumpAbsorptionEngine()
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M3,
        entry_timeframe=Timeframe.M3,
    )
    runtime = build_post_pump_absorption_runtime(params)
    frame, _ = _build_mbb_frame(Timeframe.M3, runtime)
    frame = frame.drop(columns=["taker_buy_ratio", "taker_buy_volume"])

    trades = engine.generate_events(frame, params)
    diagnostics = engine.consume_last_generation_diagnostics()

    assert trades == []
    assert diagnostics["missing_taker_data"] == 1


def test_post_pump_absorption_runtime_scales_windows_by_timeframe() -> None:
    runtime_m1 = build_post_pump_absorption_runtime(
        PostPumpAbsorptionParams(
            profile_id="balanced",
            symbol="TEST/USDT",
            levels_timeframe=Timeframe.M1,
            entry_timeframe=Timeframe.M1,
        )
    )
    runtime_m3 = build_post_pump_absorption_runtime(
        PostPumpAbsorptionParams(
            profile_id="balanced",
            symbol="TEST/USDT",
            levels_timeframe=Timeframe.M3,
            entry_timeframe=Timeframe.M3,
        )
    )
    runtime_m5 = build_post_pump_absorption_runtime(
        PostPumpAbsorptionParams(
            profile_id="balanced",
            symbol="TEST/USDT",
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M5,
        )
    )

    assert runtime_m1.pump_window_bars > runtime_m3.pump_window_bars > runtime_m5.pump_window_bars
    assert runtime_m1.range_max_bars > runtime_m3.range_max_bars > runtime_m5.range_max_bars
    assert runtime_m1.time_exit_bars > runtime_m3.time_exit_bars > runtime_m5.time_exit_bars


def test_post_pump_absorption_rejects_unsupported_timeframes() -> None:
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M15,
        entry_timeframe=Timeframe.M15,
    )

    with pytest.raises(ValueError, match="supports only entry_timeframe"):
        build_strategy(
            AppConfig(
                fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
                strategy=StrategyConfig(strategy_id="post_pump_absorption"),
                simulation=SimulationConfig(),
                backtest=BacktestConfig(
                    log_level="INFO",
                    cache_dir=Path("."),
                    logs_dir=Path("."),
                    results_dir=Path("."),
                    results_file_name="results.csv",
                    retry_attempts=1,
                    retry_backoff_seconds=0.0,
                ),
            )
        ).validate_config(params)


def test_post_pump_absorption_rejects_mismatched_levels_and_entry_timeframes() -> None:
    params = PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
    )

    with pytest.raises(ValueError, match="single-timeframe mode"):
        build_strategy(
            AppConfig(
                fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
                strategy=StrategyConfig(strategy_id="post_pump_absorption"),
                simulation=SimulationConfig(),
                backtest=BacktestConfig(
                    log_level="INFO",
                    cache_dir=Path("."),
                    logs_dir=Path("."),
                    results_dir=Path("."),
                    results_file_name="results.csv",
                    retry_attempts=1,
                    retry_backoff_seconds=0.0,
                ),
            )
        ).validate_config(params)


def test_build_strategy_supports_post_pump_absorption() -> None:
    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="post_pump_absorption"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=Path("."),
            logs_dir=Path("."),
            results_dir=Path("."),
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    strategy = build_strategy(config)

    assert strategy.__class__.__name__ == "PostPumpAbsorptionStrategy"


def test_post_pump_absorption_grid_has_multiple_named_variants() -> None:
    grid = build_post_pump_absorption_grid(profile_id="balanced")

    assert len(grid) >= 10
    assert len({params.grid_variant_id for params in grid}) == len(grid)
    assert any(params.grid_variant_id == "baseline" for params in grid)
