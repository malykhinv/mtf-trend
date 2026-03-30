from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
import strategy.hourly_asia_pump.unified_edge as unified_edge_module
from strategy.hourly_asia_pump.unified_edge import (
    _build_execution_models,
    _build_rule_text,
    _select_combo_candidates,
    build_hourly_asia_pump_unified_edge_artifacts,
)


class _FakePreparer:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        return self._frames.get((symbol, timeframe.value), pd.DataFrame()).copy()


def _build_symbol_frames(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    bars_5m: list[dict[str, object]] = []
    bars_1m: list[dict[str, object]] = []
    for month in range(1, 11):
        for day_offset in range(6):
            start = pd.Timestamp(year=2025, month=month, day=1 + day_offset, hour=0, minute=0, tz="UTC")
            base_price = 100.0 + month + day_offset
            ohlc_rows = [
                (base_price, base_price + 0.6, base_price - 0.2, base_price + 0.2),
                (base_price + 0.2, base_price + 8.5, base_price - 0.1, base_price + 7.8),
                (base_price + 7.8, base_price + 9.2, base_price + 7.0, base_price + 8.8),
                (base_price + 8.8, base_price + 15.0, base_price + 8.4, base_price + 14.0),
                (base_price + 14.0, base_price + 17.0, base_price + 13.6, base_price + 16.2),
                (base_price + 16.2, base_price + 17.8, base_price + 15.8, base_price + 17.0),
                (base_price + 17.0, base_price + 17.4, base_price + 15.2, base_price + 15.7),
                (base_price + 15.7, base_price + 16.1, base_price + 14.8, base_price + 15.2),
                (base_price + 15.2, base_price + 15.5, base_price + 14.7, base_price + 15.0),
                (base_price + 15.0, base_price + 15.2, base_price + 14.6, base_price + 14.9),
            ]
            for idx, (open_price, high_price, low_price, close_price) in enumerate(ohlc_rows):
                timestamp = start + pd.Timedelta(minutes=idx * 5)
                bars_5m.append(
                    {
                        "timestamp": int(timestamp.timestamp() * 1000),
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": 1_000_000.0 if idx == 1 else 150_000.0,
                    }
                )
                for minute_idx in range(5):
                    minute_ts = timestamp + pd.Timedelta(minutes=minute_idx)
                    bars_1m.append(
                        {
                            "timestamp": int(minute_ts.timestamp() * 1000),
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "close": close_price,
                            "volume": 50_000.0,
                        }
                    )
    return pd.DataFrame(bars_5m), pd.DataFrame(bars_1m)


def _build_base_events_csv(path: Path, symbols: list[str], frames_by_symbol: dict[str, pd.DataFrame]) -> None:
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        frame = frames_by_symbol[symbol].reset_index(drop=True)
        for row_index in range(1, len(frame), 10):
            timestamp_ms = int(frame.iloc[row_index]["timestamp"])
            timestamp = pd.to_datetime(timestamp_ms, unit="ms", utc=True)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": "5m",
                    "row_index": row_index,
                    "timestamp_ms": timestamp_ms,
                    "timestamp_utc": timestamp.isoformat(),
                    "date_utc": timestamp.strftime("%Y-%m-%d"),
                    "hour_utc": int(timestamp.hour),
                    "trigger_open": float(frame.iloc[row_index]["open"]),
                    "trigger_high": float(frame.iloc[row_index]["high"]),
                    "trigger_low": float(frame.iloc[row_index]["low"]),
                    "trigger_close": float(frame.iloc[row_index]["close"]),
                    "trigger_volume": float(frame.iloc[row_index]["volume"]),
                    "trigger_return_pct": 0.078,
                    "trigger_range_pct": 0.086,
                    "range_atr": 8.2,
                    "body_atr": 7.1,
                    "volume_mult": 14.0,
                    "close_to_high_frac": 0.08,
                    "breakout_pct": 0.0,
                    "pre_base_range_pct_60m": 0.06,
                    "pre_base_drift_pct_60m": 0.03,
                    "pre_base_range_vs_trigger": 0.45,
                    "atr_window_minutes": 180,
                    "volume_window_minutes": 720,
                    "breakout_lookback_minutes": 60,
                    "max_follow_minutes": 720,
                    "peak_price_before_50pct_retrace": float(frame.iloc[row_index + 4]["high"]),
                    "peak_timestamp_ms": int(frame.iloc[row_index + 4]["timestamp"]),
                    "peak_timestamp_utc": pd.to_datetime(int(frame.iloc[row_index + 4]["timestamp"]), unit="ms", utc=True).isoformat(),
                    "peak_return_pct": 0.16,
                    "continuation_peak_return_pct": 0.08,
                    "bars_to_peak": 4,
                    "minutes_to_peak": 20,
                    "retraced_50pct_within_window": True,
                    "retrace_50_timestamp_ms": int(frame.iloc[row_index + 7]["timestamp"]),
                    "retrace_50_timestamp_utc": pd.to_datetime(int(frame.iloc[row_index + 7]["timestamp"]), unit="ms", utc=True).isoformat(),
                    "bars_to_50pct_retrace": 7,
                    "minutes_to_50pct_retrace": 35,
                    "max_return_next_5m_pct": 0.03,
                    "close_return_5m_pct": 0.02,
                    "survived_5m": True,
                    "max_return_next_15m_pct": 0.08,
                    "close_return_15m_pct": 0.05,
                    "survived_15m": True,
                    "max_return_next_30m_pct": 0.12,
                    "close_return_30m_pct": 0.08,
                    "survived_30m": True,
                    "max_return_next_60m_pct": 0.14,
                    "close_return_60m_pct": 0.10,
                    "survived_60m": True,
                    "grid_id": "grid",
                    "profile_id": "balanced",
                    "selection_min_range_atr": 2.5,
                    "selection_min_body_atr": 1.5,
                    "selection_min_volume_mult": 2.5,
                    "selection_max_close_to_high_frac": 0.25,
                    "selection_min_breakout_pct": 0.0,
                    "selection_profile": "balanced",
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_build_hourly_asia_pump_unified_edge_artifacts(tmp_path: Path, monkeypatch) -> None:
    symbols = ["AAA/USDT:USDT", "BBB/USDT:USDT"]
    frames_5m: dict[str, pd.DataFrame] = {}
    preparer_frames: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in symbols:
        frame_5m, frame_1m = _build_symbol_frames(symbol)
        frames_5m[symbol] = frame_5m
        preparer_frames[(symbol, "5m")] = frame_5m
        preparer_frames[(symbol, "1m")] = frame_1m

    base_events_path = tmp_path / "trade_model_events.csv"
    output_dir = tmp_path / "unified_edge"
    _build_base_events_csv(base_events_path, symbols, frames_5m)

    original_models = unified_edge_module._build_execution_models()
    monkeypatch.setattr(
        unified_edge_module,
        "_build_execution_models",
        lambda: original_models[:24],
    )
    monkeypatch.setattr(unified_edge_module, "_EDGE_COMBO_CANDIDATE_LIMIT", 8)
    monkeypatch.setattr(unified_edge_module, "_EDGE_MAX_COMBO_COMPONENTS", 2)

    artifacts = build_hourly_asia_pump_unified_edge_artifacts(
        base_events_path=base_events_path,
        output_dir=output_dir,
        commission_rate=0.0004,
        preparer=_FakePreparer(preparer_frames),
    )

    atomic_summary = pd.read_csv(artifacts["atomic_summary"])
    combo_summary = pd.read_csv(artifacts["combo_summary"])
    best_summary = pd.read_csv(artifacts["best_summary"])
    best_monthly_stability = pd.read_csv(artifacts["best_monthly_stability"])
    best_risk_ladder = pd.read_csv(artifacts["best_risk_ladder"])
    confirmed_best_summary = pd.read_csv(artifacts["confirmed_best_summary"])
    context = json.loads(Path(artifacts["context"]).read_text(encoding="utf-8"))

    assert not atomic_summary.empty
    assert not combo_summary.empty
    assert len(best_summary) == 1
    assert "equity_annualized_return_pct" in best_summary.columns
    assert "top3_symbol_pnl_share" in best_summary.columns
    assert "stable_positive_months_count" in best_summary.columns
    assert "equity_month_pnl_pct" in best_monthly_stability.columns
    assert "stable_positive_month" in best_monthly_stability.columns
    assert not best_risk_ladder.empty
    assert set(best_risk_ladder["risk_pct"].round(2).tolist()) == {3.0, 4.0, 5.0}
    assert "all_components_confirmed" in combo_summary.columns
    assert Path(artifacts["confirmed_best_summary"]).exists()
    assert Path(artifacts["confirmed_best_risk_ladder"]).exists()
    assert context["search_scope"]["uses_hour_utc_in_optimization"] is False
    assert context["search_scope"]["same_rules_for_all_xx00"] is True
    assert context["search_scope"]["risk_ladder_levels"] == [0.03, 0.04, 0.05]
    assert Path(artifacts["report"]).exists()
    assert Path(artifacts["behavior_by_stop"]).exists()
    assert Path(artifacts["behavior_by_trail"]).exists()
    assert Path(artifacts["top_winners_manifest"]).exists()
    assert Path(artifacts["top_losers_manifest"]).exists()
    assert confirmed_best_summary is not None


def test_select_combo_candidates_keeps_structural_high_mean_model() -> None:
    rows: list[dict[str, object]] = []
    for idx in range(36):
        rows.append(
            {
                "trade_model_id": f"smooth_{idx}",
                "trade_model_label": f"smooth_{idx}",
                "signal_profile_id": "sig_60",
                "entry_profile_id": "confirm_25g",
                "exit_profile_id": "tp5_full",
                "trades_per_year": 55.0 + (idx % 3),
                "mean_return_pct": 0.010 + idx * 0.0001,
                "median_return_pct": 0.020,
                "win_rate": 0.72,
                "annualized_unit_pnl_pct": 0.90,
                "max_drawdown_pct": 0.12,
                "positive_months_count": 10,
                "top1_trade_pnl_share": 0.08,
                "top3_trade_pnl_share": 0.18,
                "top5_trade_pnl_share": 0.28,
                "best_month_pnl_share": 0.20,
                "annualized_remove_top1_trade_pct": 0.80,
                "annualized_remove_top3_trade_pct": 0.65,
                "annualized_remove_top5_trade_pct": 0.55,
                "annualized_remove_best_month_pct": 0.60,
                "components_count": 1,
                "selection_score": 900.0 - idx,
            }
        )

    structural_families = [
        ("family_a", "confirm_25g", "partial6_be", 0.038),
        ("family_b", "confirm_25g_strong", "partial6_be", 0.037),
        ("family_c", "confirm_50g", "partial6_be", 0.036),
        ("family_d", "confirm_25g", "partial5_be", 0.035),
        ("family_e", "confirm_25g_strong", "partial5_be", 0.034),
        ("family_f", "confirm_50g", "partial5_be", 0.033),
        ("family_g", "next_open", "runner_mid_stop_be3", 0.032),
        ("family_h", "break_2", "runner_mid_stop_be3", 0.031),
    ]
    for idx, (name, entry_profile_id, exit_profile_id, mean_return_pct) in enumerate(structural_families):
        rows.append(
            {
                "trade_model_id": name,
                "trade_model_label": name,
                "signal_profile_id": "sig_70",
                "entry_profile_id": entry_profile_id,
                "exit_profile_id": exit_profile_id,
                "trades_per_year": 28.0,
                "mean_return_pct": mean_return_pct,
                "median_return_pct": 0.010,
                "win_rate": 0.58,
                "annualized_unit_pnl_pct": 0.78,
                "max_drawdown_pct": 0.18,
                "positive_months_count": 7,
                "top1_trade_pnl_share": 0.20,
                "top3_trade_pnl_share": 0.78,
                "top5_trade_pnl_share": 1.02,
                "best_month_pnl_share": 0.42,
                "annualized_remove_top1_trade_pct": 0.55,
                "annualized_remove_top3_trade_pct": 0.25,
                "annualized_remove_top5_trade_pct": 0.10,
                "annualized_remove_best_month_pct": 0.20,
                "components_count": 1,
                "selection_score": 150.0 - idx,
            }
        )

    rows.append(
        {
            "trade_model_id": "target_partial",
            "trade_model_label": "target_partial",
            "signal_profile_id": "sig_65",
            "entry_profile_id": "confirm_50g",
            "exit_profile_id": "partial4_be",
            "trades_per_year": 33.0,
            "mean_return_pct": 0.030,
            "median_return_pct": 0.008,
            "win_rate": 0.64,
            "annualized_unit_pnl_pct": 0.97,
            "max_drawdown_pct": 0.28,
            "positive_months_count": 8,
            "top1_trade_pnl_share": 0.24,
            "top3_trade_pnl_share": 0.75,
            "top5_trade_pnl_share": 1.04,
            "best_month_pnl_share": 0.37,
            "annualized_remove_top1_trade_pct": 0.70,
            "annualized_remove_top3_trade_pct": 0.32,
            "annualized_remove_top5_trade_pct": 0.14,
            "annualized_remove_best_month_pct": 0.24,
            "components_count": 1,
            "selection_score": 130.0,
        }
    )

    selected = _select_combo_candidates(pd.DataFrame(rows))

    assert "target_partial" in set(selected["trade_model_id"].astype(str))


def test_build_execution_models_includes_confirmed_pressure_profiles() -> None:
    models = _build_execution_models()
    labels = {model.label for model in models}
    rule_texts = {_build_rule_text(model) for model in models}

    assert any("confirm_50g_pos50_ext5" in label for label in labels)
    assert any("confirm_25g_strong_pos50_ext5_ret5" in label for label in labels)
    assert any("tp5_confirm_low" in label for label in labels)
    assert any("tp5_confirm_body" in label for label in labels)
    assert any("next_close_pos>=" in text for text in rule_texts)
    assert any("next_ext>=" in text for text in rule_texts)
    assert any("stop=confirmed_bar_low" in text for text in rule_texts)
    assert any("stop=confirmed_bar_body_low" in text for text in rule_texts)
