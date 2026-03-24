from pathlib import Path

from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
from vectorbt_runner.backtest_runner import BacktestRunner


def _trade(*, entry_ts: int, exit_ts: int, pnl: float) -> TradeResult:
    return TradeResult(
        entry_price=Price(100.0),
        exit_price=Price(101.0),
        entry_timestamp_ms=entry_ts,
        exit_timestamp_ms=exit_ts,
        result_type=TradeResultType.TP2 if pnl > 0 else TradeResultType.SL,
        pnl=pnl,
        pnl_percent=Percentage(pnl / 10.0),
        pump_to_peak_bars=4,
        pump_to_peak_minutes=60.0,
    )


def test_build_metrics_row_includes_max_drawdown_pct() -> None:
    row = BacktestRunner._build_metrics_row(
        {"bite_deposit": 1_000.0},
        [
            _trade(entry_ts=1, exit_ts=2, pnl=100.0),
            TradeResult(
                entry_price=Price(100.0),
                exit_price=Price(101.0),
                entry_timestamp_ms=3,
                exit_timestamp_ms=4,
                result_type=TradeResultType.SL,
                pnl=-50.0,
                pnl_percent=Percentage(-5.0),
                pump_to_peak_bars=6,
                pump_to_peak_minutes=90.0,
            ),
            TradeResult(
                entry_price=Price(100.0),
                exit_price=Price(101.0),
                entry_timestamp_ms=5,
                exit_timestamp_ms=6,
                result_type=TradeResultType.SL,
                pnl=-150.0,
                pnl_percent=Percentage(-15.0),
                pump_to_peak_bars=8,
                pump_to_peak_minutes=120.0,
            ),
        ],
    )

    assert row["max_dd"] == 200.0
    assert row["max_drawdown_pct"] == 18.181818
    assert row["median_pump_to_peak_bars"] == 6.0
    assert row["median_pump_to_peak_minutes"] == 90.0


def test_build_metrics_row_returns_zero_drawdown_pct_without_trades() -> None:
    runner = BacktestRunner(results_dir=Path("."), results_file_name="results.csv")

    row = runner._build_metrics_row({"bite_deposit": 1_000.0}, [])

    assert row["max_drawdown_pct"] == 0.0
    assert row["median_pump_to_peak_bars"] is None
    assert row["median_pump_to_peak_minutes"] is None


def test_build_metrics_row_includes_post_pump_absorption_metadata_metrics() -> None:
    row = BacktestRunner._build_metrics_row(
        {"ppa_deposit": 1_000.0},
        [
            TradeResult(
                entry_price=Price(100.0),
                exit_price=Price(104.0),
                entry_timestamp_ms=1,
                exit_timestamp_ms=2,
                result_type=TradeResultType.TP2,
                pnl=40.0,
                pnl_percent=Percentage(4.0),
                metadata={
                    "setup_type": "LSB",
                    "entry_range_fraction": 0.25,
                    "aggression_ratio": 0.68,
                    "aggression_volume_mult": 1.8,
                    "range_mid_hit": True,
                    "range_high_hit": True,
                    "tp1_hit": True,
                    "tp2_hit": True,
                    "mfe_r": 2.1,
                    "mae_r": 0.4,
                    "holding_bars": 5,
                },
            ),
            TradeResult(
                entry_price=Price(100.0),
                exit_price=Price(102.0),
                entry_timestamp_ms=3,
                exit_timestamp_ms=4,
                result_type=TradeResultType.TP1_BE,
                pnl=20.0,
                pnl_percent=Percentage(2.0),
                metadata={
                    "setup_type": "MBB",
                    "entry_range_fraction": 0.35,
                    "aggression_ratio": 0.74,
                    "aggression_volume_mult": 2.2,
                    "range_mid_hit": True,
                    "range_high_hit": False,
                    "tp1_hit": True,
                    "tp2_hit": False,
                    "mfe_r": 1.6,
                    "mae_r": 0.5,
                    "holding_bars": 7,
                },
            ),
        ],
    )

    assert row["ppa_setup_lsb_count"] == 1
    assert row["ppa_setup_mbb_count"] == 1
    assert row["ppa_range_mid_hit_count"] == 2
    assert row["ppa_range_high_hit_count"] == 1
    assert row["ppa_median_entry_range_fraction"] == 0.3
    assert row["ppa_median_aggression_ratio"] == 0.71
    assert row["ppa_median_holding_bars"] == 6.0
