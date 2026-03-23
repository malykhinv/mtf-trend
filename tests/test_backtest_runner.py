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
