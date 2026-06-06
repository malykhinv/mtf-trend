from research_tools.pump_trade_policy import (
    EXIT_POLICY_TP075_CLOSE75_TRAIL,
    RULE_5M15_ACTIVE_TAPE,
    RULE_DISTRIBUTED_TRADE_TOP1_CONFIRM08,
    RULE_5M_STRONG_BUYER_CONFIRM_HISTORY,
    RULE_STRONG_HIGH_WIN_CLEAN_NOTDUMP,
    evaluate_pump_trade_policy,
)


def _base_features(**overrides: object) -> dict[str, object]:
    features: dict[str, object] = {
        "rolling_runner_tf_set": "5m_30s",
        "ltf_taker_buy_quote_share": 0.61,
        "pregrowth_min_path_return_pct": -0.004,
        "ltf_confirm_return_pct": 0.0085,
        "ltf_trade_pace_ratio": 5.5,
        "ltf_quote_pace_ratio": 8.0,
        "prior_spike_count_24h": 4,
        "htf_ltf_quote_top1_share": 0.45,
        "htf_ltf_trade_top1_share": 0.35,
        "htf_ltf_tail_quote_share": 0.50,
    }
    features.update(overrides)
    return features


def test_clean_buyer_policy_accepts_strong_high_win_rule() -> None:
    decision = evaluate_pump_trade_policy(_base_features())

    assert decision.accepted is True
    assert decision.matched_rule_id == RULE_STRONG_HIGH_WIN_CLEAN_NOTDUMP
    assert RULE_5M_STRONG_BUYER_CONFIRM_HISTORY in decision.matched_rule_ids
    assert decision.exit_policy.policy_id == EXIT_POLICY_TP075_CLOSE75_TRAIL
    assert decision.exit_policy.tp1_r == 0.75
    assert decision.exit_policy.tp1_close_fraction == 0.75
    assert decision.exit_policy.runner_fraction == 0.25


def test_clean_buyer_policy_keeps_broad_buyer55_notdump_as_watchlist_only() -> None:
    decision = evaluate_pump_trade_policy(
        _base_features(
            ltf_taker_buy_quote_share=0.56,
            ltf_confirm_return_pct=0.0045,
            ltf_trade_pace_ratio=3.2,
            htf_ltf_quote_top1_share=0.70,
            htf_ltf_trade_top1_share=0.70,
            htf_ltf_tail_quote_share=0.70,
        )
    )

    assert decision.accepted is False
    assert decision.verdict == "rejected"
    assert decision.reason == "clean_buyer_continuation_watchlist_only"
    assert decision.matched_rule_ids == ()
    assert decision.watchlist_rule_ids == ("broad_buyer55_notdump_watchlist",)


def test_clean_buyer_policy_rejects_weak_5m15_active_tape_as_watchlist_only() -> None:
    decision = evaluate_pump_trade_policy(
        _base_features(
            rolling_runner_tf_set="5m_15s",
            ltf_taker_buy_quote_share=0.62,
            pregrowth_min_path_return_pct=-0.02,
            ltf_confirm_return_pct=0.0045,
            ltf_trade_pace_ratio=6.0,
            ltf_quote_pace_ratio=8.0,
            prior_spike_count_24h=20,
            htf_ltf_quote_top1_share=0.70,
            htf_ltf_trade_top1_share=0.70,
            htf_ltf_tail_quote_share=0.70,
        )
    )

    assert decision.accepted is False
    assert decision.verdict == "rejected"
    assert decision.reason == "clean_buyer_continuation_watchlist_only"
    assert decision.matched_rule_ids == ()
    assert decision.watchlist_rule_ids == (RULE_5M15_ACTIVE_TAPE,)


def test_clean_buyer_policy_rejects_distributed_confirm_without_no_dump_as_watchlist_only() -> None:
    decision = evaluate_pump_trade_policy(
        _base_features(
            rolling_runner_tf_set="3m_30s",
            ltf_taker_buy_quote_share=0.62,
            pregrowth_min_path_return_pct=-0.02,
            ltf_confirm_return_pct=0.009,
            ltf_trade_pace_ratio=4.0,
            ltf_quote_pace_ratio=8.0,
            prior_spike_count_24h=3,
            htf_ltf_quote_top1_share=0.70,
            htf_ltf_trade_top1_share=0.35,
            htf_ltf_tail_quote_share=0.70,
        )
    )

    assert decision.accepted is False
    assert decision.verdict == "rejected"
    assert decision.reason == "clean_buyer_continuation_watchlist_only"
    assert decision.matched_rule_ids == ()
    assert decision.watchlist_rule_ids == (RULE_DISTRIBUTED_TRADE_TOP1_CONFIRM08,)


def test_clean_buyer_policy_is_source_neutral_for_same_features() -> None:
    live_features = _base_features(source="live", adapter_source="ws")
    backtest_features = _base_features(source="backtest", adapter_source="archive")

    live_decision = evaluate_pump_trade_policy(live_features)
    backtest_decision = evaluate_pump_trade_policy(backtest_features)

    assert live_decision.verdict == backtest_decision.verdict
    assert live_decision.reason == backtest_decision.reason
    assert live_decision.matched_rule_id == backtest_decision.matched_rule_id
    assert live_decision.as_features() == backtest_decision.as_features()
