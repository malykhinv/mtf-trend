from dataclasses import replace

from research_tools.anomaly_live2.market_data.candles import Live2Candle
from research_tools.anomaly_live2.signal import Live2SignalEngine
from research_tools.anomaly_live2.state import Live2RollingSeedState
from research_tools.pump_decision_core import _build_seed_first_core_smoke_snapshot, evaluate_first_ltf_confirm_after_seed


def _live_candle(open_ms: int, close_ms: int) -> Live2Candle:
    return Live2Candle(
        timeframe_ms=close_ms - open_ms,
        open_time_ms=open_ms,
        close_time_ms=close_ms,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        base_volume=1.0,
        quote_volume=1_000.0,
        number_of_trades=100,
        taker_buy_quote_volume=700.0,
        first_trade_time_ms=open_ms,
        last_trade_time_ms=close_ms - 1,
        first_source="unit_test",
        last_source="unit_test",
    )


def _seed_state(verdict) -> Live2RollingSeedState:
    assert verdict.snapshot.rolling_seed is not None
    seed = verdict.snapshot.rolling_seed
    return Live2RollingSeedState(
        seed_key=f"{seed.tf_set}:{seed.seed_open_ms}:{seed.seed_close_ms}",
        tf_set=seed.tf_set,
        profile_rank=1,
        seed_open_ms=seed.seed_open_ms,
        seed_close_ms=seed.seed_close_ms,
        seed_candles=(),
        created_ms=seed.seed_close_ms,
    )


def _accepted_features(features: dict[str, object]) -> dict[str, object]:
    return {
        **features,
        "rolling_runner_tf_set": "5m_30s",
        "ltf_taker_buy_quote_share": 0.62,
        "pregrowth_min_path_return_pct": -0.003,
        "ltf_confirm_return_pct": 0.0085,
        "ltf_trade_pace_ratio": 6.0,
        "ltf_quote_pace_ratio": 8.0,
        "prior_spike_count_24h": 3,
        "htf_ltf_quote_top1_share": 0.45,
        "htf_ltf_trade_top1_share": 0.35,
        "htf_ltf_tail_quote_share": 0.50,
    }


def test_live2_signal_adapter_applies_trade_policy_acceptance() -> None:
    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    selected = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    selected = replace(selected, features=_accepted_features(dict(selected.features)))
    engine = Live2SignalEngine()
    decision = engine._live_decision_from_core_verdict(
        verdict=selected,
        seed=_seed_state(selected),
        post_seed=(_live_candle(0, 30_000), _live_candle(30_000, 60_000)),
        decision_candle=_live_candle(30_000, 60_000),
    )

    assert decision.verdict == "selected"
    assert decision.trade_policy_id == "clean_buyer_continuation_v1"
    assert decision.trade_policy_rule_id == "strong_high_win_clean_notdump"
    assert decision.exit_policy_id == "tp075_close75_structural_trail_v1"
    assert decision.tp1_close_fraction == 0.75
    assert decision.features["core_signal_verdict"] == "selected"
    assert decision.features["trade_policy_verdict"] == "accepted"


def test_live2_signal_adapter_rejects_core_selected_watchlist_only_policy() -> None:
    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    selected = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    selected = replace(
        selected,
        features={
            **dict(selected.features),
            "rolling_runner_tf_set": "5m_30s",
            "ltf_taker_buy_quote_share": 0.56,
            "pregrowth_min_path_return_pct": -0.003,
            "ltf_confirm_return_pct": 0.0045,
            "ltf_trade_pace_ratio": 3.2,
            "ltf_quote_pace_ratio": 8.0,
            "prior_spike_count_24h": 3,
            "htf_ltf_quote_top1_share": 0.70,
            "htf_ltf_trade_top1_share": 0.70,
            "htf_ltf_tail_quote_share": 0.70,
        },
    )
    engine = Live2SignalEngine()
    decision = engine._live_decision_from_core_verdict(
        verdict=selected,
        seed=_seed_state(selected),
        post_seed=(_live_candle(0, 30_000), _live_candle(30_000, 60_000)),
        decision_candle=_live_candle(30_000, 60_000),
    )

    assert decision.verdict == "rejected"
    assert decision.reason == "clean_buyer_continuation_watchlist_only"
    assert decision.features["core_signal_verdict"] == "selected"
    assert decision.features["trade_policy_verdict"] == "rejected"
    assert decision.features["trade_policy_watchlist_rule_ids"] == "broad_buyer55_notdump_watchlist"
