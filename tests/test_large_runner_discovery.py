from pathlib import Path

import pandas as pd

from data.storage.parquet_storage import ParquetStorage
from research_tools.large_runner_discovery import (
    LargeRunnerDiscoveryConfig,
    match_large_runner_arms,
    run_large_runner_discovery,
)


def _write_cache_frame(cache_dir: Path, symbol: str, timeframe: str, frame: pd.DataFrame) -> None:
    path = cache_dir / ParquetStorage.encode_symbol_for_path(symbol) / timeframe / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _synthetic_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows_5m = []
    price = 100.0
    for index in range(40):
        ts = index * 5 * 60_000
        open_price = price
        close = open_price
        high = open_price * 1.002
        low = open_price * 0.998
        quote = 1000.0
        trades = 100.0
        oi = 100.0
        if index == 20:
            close = open_price * 1.06
            high = open_price * 1.07
            low = open_price * 0.995
            quote = 10_000.0
            trades = 1000.0
        rows_5m.append(
            {
                "timestamp": ts,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
                "quote_volume": quote,
                "number_of_trades": trades,
                "taker_buy_quote_volume": quote * 0.58,
                "open_interest": oi,
            }
        )
        price = close
    frame_5m = pd.DataFrame(rows_5m)

    rows_1m = []
    price = 100.0
    for index in range(260):
        ts = index * 60_000
        open_price = price
        close = open_price
        high = open_price * 1.001
        low = open_price * 0.999
        quote = 200.0
        trades = 20.0
        if 100 <= index < 105:
            step = [0.010, 0.008, 0.009, 0.012, 0.020][index - 100]
            close = open_price * (1.0 + step)
            high = close * 1.003
            low = open_price * 0.998
            quote = [2000.0, 1500.0, 1000.0, 2500.0, 3000.0][index - 100]
            trades = [200.0, 160.0, 120.0, 260.0, 320.0][index - 100]
        elif 105 <= index < 150:
            close = open_price * 1.004
            high = close * 1.002
            low = open_price * 0.998
            quote = 700.0
            trades = 70.0
        rows_1m.append(
            {
                "timestamp": ts,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
                "quote_volume": quote,
                "number_of_trades": trades,
                "taker_buy_quote_volume": quote * 0.58,
            }
        )
        price = close
    return frame_5m, pd.DataFrame(rows_1m)


def test_large_runner_arm_matching_ignores_future_labels() -> None:
    features = {
        "early_return_pct": 0.056,
        "early_quote_ratio_24h_scaled": 8.0,
        "early_trade_ratio_24h_scaled": 5.0,
        "current_vs_prior_max_quote_24h": 0.8,
        "pre60_range_pct": 0.03,
        "pre60_min_path_pct": -0.01,
        "no_short_covering_oi": True,
        "m1_sustain_mid": True,
        "m1_sustain_strict": True,
        "confirm10_valid": True,
        "confirm15_valid": True,
        "close_ret_10m": 0.071,
        "high_ret_10m": 0.085,
        "wick_ret_10m": 0.014,
        "close_ret_15m": 0.11,
        "high_ret_15m": 0.13,
        "wick_ret_15m": 0.02,
    }

    base = match_large_runner_arms(features)
    with_future = match_large_runner_arms(
        {
            **features,
            "future60_high_return_pct": -0.50,
            "runner_high20_next60": False,
            "net_return": -1.0,
        }
    )

    assert base == with_future
    assert "E5_ignition_strict" in base
    assert "E10_confirmed_runner" in base


def test_large_runner_discovery_smoke_writes_honest_artifacts(tmp_path: Path) -> None:
    symbol = "AAA/USDT:USDT"
    cache_dir = tmp_path / "cache"
    frame_5m, frame_1m = _synthetic_frames()
    _write_cache_frame(cache_dir, symbol, "5m", frame_5m)
    _write_cache_frame(cache_dir, symbol, "1m", frame_1m)

    output_dir = tmp_path / "results"
    result_dir = run_large_runner_discovery(
        LargeRunnerDiscoveryConfig(cache_dir=cache_dir, output_dir=output_dir, days=1, symbol_workers=1),
        progress_label="test large runner discovery",
    )

    matches = pd.read_csv(result_dir / "large_runner_rule_matches.csv")
    trades = pd.read_csv(result_dir / "large_runner_trade_grid.csv")
    config = pd.read_csv(result_dir / "run_config.csv")

    assert set(matches["arm_id"]) >= {"E5_ignition_strict", "E5_mass_ignition"}
    assert not trades.empty
    assert trades["future_label_available_at_entry"].eq(False).all()
    assert config.loc[0, "data_access_model"] == "cache_only_5m_1m_no_exchange_fetch"

    for name in [
        "large_runner_candidates_raw.csv",
        "large_runner_setups.csv",
        "large_runner_rule_matches.csv",
        "large_runner_trade_grid.csv",
        "large_runner_funnel.csv",
        "large_runner_top_growth_coverage.csv",
    ]:
        pd.read_csv(result_dir / name)
