from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.production import build_hourly_asia_pump_production_artifacts


def _month_trade_timestamps() -> list[pd.Timestamp]:
    timestamps: list[pd.Timestamp] = []
    for month in range(1, 13):
        base_day = 2
        for offset in range(5):
            timestamps.append(
                pd.Timestamp(year=2025, month=month, day=base_day + offset * 5, hour=3, minute=5, tz="UTC")
            )
    return timestamps


def test_build_hourly_asia_pump_production_artifacts_passes_gate(tmp_path: Path) -> None:
    static_combo_dir = tmp_path / "static_combo"
    static_combo_dir.mkdir(parents=True, exist_ok=True)

    great_catalog = pd.DataFrame(
        [
            {
                "combo_id": "combo_a",
                "combo_variant": "A",
                "component_ids": "mb5_01_shallow,mb3_03_raw,mb5_06_shallow,tf7_raw,conf00_trg65_pb50",
                "top_k_per_timestamp": 99,
                "trades_count": 60,
                "trade_days_count": 60,
                "trades_per_year": 60.0,
                "mean_return_pct": 0.030,
                "median_return_pct": 0.028,
                "win_rate": 0.80,
                "profit_factor": 4.2,
                "annualized_sum_return_pct": 1.80,
                "mean_pos_trade_pct": 0.055,
                "mean_neg_trade_pct": -0.020,
                "positive_months_count": 12,
                "non_positive_months_count": 0,
                "max_drawdown_pct": 0.08,
                "priority_score": 9.0,
                "combo_priority": 1,
            },
            {
                "combo_id": "combo_d",
                "combo_variant": "D",
                "component_ids": "mb5_01_shallow,mb3_03_trg_q75,mb5_06_shallow,tf7_raw,conf00_trg65_pb50",
                "top_k_per_timestamp": 2,
                "trades_count": 44,
                "trade_days_count": 44,
                "trades_per_year": 44.0,
                "mean_return_pct": 0.038,
                "median_return_pct": 0.032,
                "win_rate": 0.82,
                "profit_factor": 5.0,
                "annualized_sum_return_pct": 1.67,
                "mean_pos_trade_pct": 0.062,
                "mean_neg_trade_pct": -0.021,
                "positive_months_count": 10,
                "non_positive_months_count": 2,
                "max_drawdown_pct": 0.09,
                "priority_score": 8.0,
                "combo_priority": 2,
            },
            {
                "combo_id": "combo_h",
                "combo_variant": "H",
                "component_ids": "mb5_01_shallow,mb5_06_shallow,mb5_03_raw,tf7_raw,conf00_trg65_pb50",
                "top_k_per_timestamp": 99,
                "trades_count": 48,
                "trade_days_count": 48,
                "trades_per_year": 48.0,
                "mean_return_pct": 0.032,
                "median_return_pct": 0.029,
                "win_rate": 0.77,
                "profit_factor": 3.8,
                "annualized_sum_return_pct": 1.54,
                "mean_pos_trade_pct": 0.057,
                "mean_neg_trade_pct": -0.024,
                "positive_months_count": 10,
                "non_positive_months_count": 2,
                "max_drawdown_pct": 0.11,
                "priority_score": 7.0,
                "combo_priority": 3,
            },
            {
                "combo_id": "combo_mo",
                "combo_variant": "MO",
                "component_ids": "mb5_01_shallow,tf7_raw,conf00_trg65_pb50",
                "top_k_per_timestamp": 99,
                "trades_count": 36,
                "trade_days_count": 36,
                "trades_per_year": 36.0,
                "mean_return_pct": 0.026,
                "median_return_pct": 0.021,
                "win_rate": 0.72,
                "profit_factor": 3.1,
                "annualized_sum_return_pct": 0.94,
                "mean_pos_trade_pct": 0.046,
                "mean_neg_trade_pct": -0.021,
                "positive_months_count": 9,
                "non_positive_months_count": 3,
                "max_drawdown_pct": 0.13,
                "priority_score": 6.0,
                "combo_priority": 4,
            },
        ]
    )
    great_catalog.to_csv(static_combo_dir / "great_combo_catalog.csv", index=False)

    robustness = pd.DataFrame(
        [
            {"combo_id": "combo_a", "robustness_label": "strong", "local_goal_rate": 0.80, "local_mean_return_p25_pct": 0.024, "local_dd_p75_pct": 0.12, "local_annualized_median_pct": 1.40},
            {"combo_id": "combo_d", "robustness_label": "strong", "local_goal_rate": 0.71, "local_mean_return_p25_pct": 0.029, "local_dd_p75_pct": 0.14, "local_annualized_median_pct": 1.35},
            {"combo_id": "combo_h", "robustness_label": "strong", "local_goal_rate": 0.69, "local_mean_return_p25_pct": 0.025, "local_dd_p75_pct": 0.16, "local_annualized_median_pct": 1.22},
            {"combo_id": "combo_mo", "robustness_label": "strong", "local_goal_rate": 0.63, "local_mean_return_p25_pct": 0.021, "local_dd_p75_pct": 0.18, "local_annualized_median_pct": 1.05},
        ]
    )
    robustness.to_csv(static_combo_dir / "combo_robustness_summary.csv", index=False)

    trade_manifest = pd.DataFrame(columns=["chart_path", "chart_status"])
    trade_manifest.to_csv(static_combo_dir / "priority_trade_chart_manifest.csv", index=False)

    returns_pattern = [0.08, 0.05, 0.04, -0.02, 0.03]
    combo_event_rows: list[dict[str, object]] = []
    for index, entry_time in enumerate(_month_trade_timestamps()):
        timestamp_ms = int((entry_time - pd.Timedelta(minutes=5)).timestamp() * 1000)
        entry_timestamp_ms = int(entry_time.timestamp() * 1000)
        exit_return_pct = returns_pattern[index % len(returns_pattern)]
        base_row = {
            "symbol": f"S{index:03d}/USDT",
            "timestamp_ms": timestamp_ms,
            "entry_timestamp_ms": entry_timestamp_ms,
            "entry_timestamp_utc": entry_time.isoformat(),
            "date_utc": entry_time.strftime("%Y-%m-%d"),
            "month_utc": entry_time.strftime("%Y-%m"),
            "hour_utc": entry_time.hour,
            "exit_return_pct": exit_return_pct,
        }
        combo_event_rows.append({"combo_id": "combo_a", **base_row})
        if index % 2 == 0:
            combo_event_rows.append({"combo_id": "combo_d", **base_row, "exit_return_pct": exit_return_pct * 0.95})
        if index % 3 == 0:
            combo_event_rows.append({"combo_id": "combo_h", **base_row, "exit_return_pct": exit_return_pct * 0.90})
        if index % 4 == 0:
            combo_event_rows.append({"combo_id": "combo_mo", **base_row, "exit_return_pct": exit_return_pct * 0.85})
    pd.DataFrame(combo_event_rows).to_csv(static_combo_dir / "combo_events.csv", index=False)

    artifacts = build_hourly_asia_pump_production_artifacts(static_combo_dir=static_combo_dir)

    summary = pd.read_csv(artifacts["production_default_summary"])
    monthly = pd.read_csv(artifacts["production_default_monthly"])
    holdout = pd.read_csv(artifacts["production_default_holdout"])
    kpi = pd.read_csv(artifacts["production_kpi_validation"])

    assert bool(artifacts["gate_passed"]) is True
    assert artifacts["report"].exists()
    assert float(summary.iloc[0]["annualized_sum_return_pct"]) >= 1.0
    assert float(summary.iloc[0]["mean_return_pct"]) >= 0.02
    assert float(summary.iloc[0]["win_rate"]) >= 0.40
    assert float(summary.iloc[0]["trades_per_year"]) >= 50.0
    assert float(summary.iloc[0]["max_drawdown_pct"]) <= 0.30
    assert int(summary.iloc[0]["positive_months_count"]) >= 9
    assert len(monthly) == 12
    assert set(holdout["split_id"].astype(str)) == {"train8_test4", "train9_test3"}
    assert bool(kpi["passed"].all()) is True
