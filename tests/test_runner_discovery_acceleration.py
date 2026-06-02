import pandas as pd

from research_tools.htf_ltf_runner_discovery import (
    HtfLtfRunnerDiscoveryConfig,
    TRADE_ARTIFACT_COLUMNS,
    _artifact_frame,
    _build_selected_signal_post_entry_backfill_plan,
    _pre_seed_context_candles_from_htf,
    _post_entry_fetch_window_for_signal,
    _signal_entry_tail_ms,
)
from research_tools.runner_coarse_prefilter import (
    coarse_minute_pair_prefilter,
    prepare_minute_coarse_frame,
)


def _minute_frame(start_ms: int, rows: int, *, quote: float, trades: float, close_step: float = 0.0) -> pd.DataFrame:
    data = []
    price = 100.0
    for index in range(rows):
        open_price = price
        close_price = price + close_step
        high = max(open_price, close_price)
        low = min(open_price, close_price)
        data.append(
            {
                "timestamp": start_ms + index * 60_000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": 1.0,
                "quote_volume": quote,
                "number_of_trades": trades,
            }
        )
        price = close_price
    return pd.DataFrame(data)


def test_coarse_minute_prefilter_rejects_only_proven_impossible_window() -> None:
    frame = prepare_minute_coarse_frame(_minute_frame(0, 12, quote=100.0, trades=10.0))

    verdict = coarse_minute_pair_prefilter(
        frame,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert verdict.possible is False
    assert verdict.reason.startswith("impossible_minute_")
    assert verdict.bounds["coarse_prefilter_trading_signal"] is False


def test_coarse_minute_prefilter_keeps_uncertain_or_possible_window() -> None:
    possible_frame = _minute_frame(0, 12, quote=5_000.0, trades=500.0, close_step=0.5)
    possible_frame.loc[4, "high"] = 106.0
    frame = prepare_minute_coarse_frame(possible_frame)

    verdict = coarse_minute_pair_prefilter(
        frame,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert verdict.possible is True
    assert verdict.reason == "possible_by_1m_bounds"

    incomplete = prepare_minute_coarse_frame(possible_frame.drop(index=3))
    uncertain = coarse_minute_pair_prefilter(
        incomplete,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert uncertain.possible is True
    assert uncertain.reason == "not_checked_incomplete_1m_coverage"


def test_targeted_post_entry_replay_plan_uses_only_selected_signals() -> None:
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="5m", ltf_timeframe="30s", max_hold_candles=10, runner_horizon_minutes=60)
    selected = {
        "symbol": "AAA/USDT:USDT",
        "signal_verdict": "selected",
        "timestamp_ms": 0,
        "htf_close_ms": 300_000,
        "entry_timestamp_ms": 420_000,
    }
    rejected = {
        "symbol": "BBB/USDT:USDT",
        "signal_verdict": "rejected",
        "timestamp_ms": 0,
        "htf_close_ms": 300_000,
        "entry_timestamp_ms": 420_000,
    }

    window = _post_entry_fetch_window_for_signal(selected, config=config, ltf_ms=30_000)
    assert window == (300_000, 3_899_999)

    plan, windows = _build_selected_signal_post_entry_backfill_plan([selected, rejected], config=config, ltf_ms=30_000)

    assert windows == {"AAA/USDT:USDT": [(300_000, 3_899_999)]}
    assert int(plan.iloc[0]["selected_signals"]) == 1
    assert plan.loc[plan["targeted_ltf_plan_status"].eq("planned"), "signal_scope"].tolist() == ["core_selected_signal"]


def test_signal_entry_tail_is_confirm_plus_next_open_only() -> None:
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="3m", ltf_timeframe="15s")

    assert _signal_entry_tail_ms(config, 15_000) == 195_000


def test_empty_trade_artifact_keeps_csv_headers() -> None:
    frame = _artifact_frame([], columns=TRADE_ARTIFACT_COLUMNS)

    assert frame.empty
    assert "status" in frame.columns
    assert "entry_timestamp_ms" in frame.columns


def test_pre_seed_context_uses_closed_htf_cache_without_subminute_history() -> None:
    htf_ms = 300_000
    seed_open_ms = 288 * htf_ms
    htf = pd.DataFrame(
        [
            {
                "timestamp": index * htf_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1.0,
                "quote_volume": 1000.0 + index,
                "number_of_trades": 100 + index,
            }
            for index in range(288)
        ]
    )

    context = _pre_seed_context_candles_from_htf(htf, seed_open_ms=seed_open_ms, tf_set="5m_30s", htf_ms=htf_ms)

    assert len(context) == 288
    assert context[0].open_time_ms == 0
    assert context[-1].close_time_ms == seed_open_ms
