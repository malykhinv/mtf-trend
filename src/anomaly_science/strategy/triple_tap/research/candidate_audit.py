"""IS-only audit for the frozen triple-tap stable-income candidate.

This module intentionally does not touch any OOS period. It evaluates the
already frozen IS candidate:

* confirmed close entry;
* structural 50% target partial close;
* 50% runner to the original structural measured-move target;
* causal prior-forward-score q70 selection;
* max 3 concurrent positions, one position per symbol;
* trader-style risk sizing with max 1x notional cap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_long.research.context import DEV_END_MS
from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.audit import (
    _calendar_stats,
    _causal_quantile_selection,
    _ledger,
    _metrics,
)
from anomaly_science.strategy.triple_tap.research.economics import COST_RATE, DEPOSIT, RES
from anomaly_science.strategy.triple_tap.research.policy_ev_model import (
    score_path,
    shuffled_score_path,
)

CANDIDATE_POLICY = "structure_0.50_part_0.50_runner_initial_stop_be_0.50_inactive_6h"
MAX_NOTIONAL_MULTIPLE = 1.0
OUT_JSON = Path("research/triple_tap_frozen_candidate_is_audit.json")
OUT_MD = Path("research/triple_tap_frozen_candidate_is_audit.md")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Interval):
        return str(value)
    if pd.isna(value):
        return None
    return value


def _load_candidate_frame() -> pd.DataFrame:
    policy_labels = pd.read_parquet(RES / "execution_policies.parquet")
    labels = policy_labels[
        (policy_labels["entry_mode"] == "close")
        & (policy_labels["exit_policy"] == CANDIDATE_POLICY)
        & policy_labels["r_multiple"].notna()
    ].copy()
    if labels.duplicated(TRADE_KEY).any():
        raise ValueError("candidate labels are not unique by TRADE_KEY")

    features = pd.read_parquet(RES / "features.parquet")
    if features.duplicated(TRADE_KEY).any():
        raise ValueError("features are not unique by TRADE_KEY")

    base_labels = pd.read_parquet(RES / "labels_close.parquet")
    base_times = base_labels[
        TRADE_KEY + ["feature_cutoff_time_ms", "future_start_time_ms"]
    ].copy()
    if base_times.duplicated(TRADE_KEY).any():
        raise ValueError("base close labels are not unique by TRADE_KEY")

    scores = pd.read_parquet(score_path(CANDIDATE_POLICY))
    if scores.duplicated(TRADE_KEY).any():
        raise ValueError("candidate scores are not unique by TRADE_KEY")

    frame = labels.merge(features, on=TRADE_KEY, validate="one_to_one")
    frame = frame.merge(base_times, on=TRADE_KEY, validate="one_to_one")
    frame = frame.merge(scores, on=TRADE_KEY, validate="one_to_one")
    frame["dist_stop"] = frame["dist_stop"].astype(float)
    return frame.sort_values(["fill_time_ms", "symbol", "tf"]).reset_index(drop=True)


def _selected_frame(frame: pd.DataFrame) -> pd.DataFrame:
    selected = _causal_quantile_selection(frame, 0.70)
    return frame.loc[selected].copy()


def _ledger_for(
    frame: pd.DataFrame,
    *,
    risk_pct: float,
    slip_bps: float = 50,
    max_notional_multiple: float | None = MAX_NOTIONAL_MULTIPLE,
) -> tuple[pd.DataFrame, dict[str, int]]:
    return _ledger(
        frame,
        risk_pct=risk_pct,
        max_open=3,
        slip_pct=slip_bps / 10_000,
        max_notional_multiple=max_notional_multiple,
    )


def _with_trade_result(ledger: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "r_multiple",
        "dist_stop",
        "model_score_r",
        "hour",
        "dow",
        "ignition_rise",
        "pullback_depth",
        "form_over_pump_vol",
        "formation_vol_ratio",
        "pre_brk_vol_ramp",
        "vol_step_ratio",
        "up_down_vol_ratio",
        "trades_vs_btc",
        "trvbtc_lastleg",
        "pump_over_sleep_vol",
        "setup_family",
        "span_h",
        "rr",
        "brk_close_over_lvl",
        "n_taps",
    ]
    return ledger.merge(frame[TRADE_KEY + cols], on=TRADE_KEY, validate="one_to_one")


def _group_stats(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value, group in frame.groupby(column, observed=True, dropna=False):
        pnl = group["pnl"].to_numpy(float)
        net_r = group["net_r"].to_numpy(float)
        gross_loss = -float(pnl[pnl < 0].sum())
        rows.append(
            {
                "value": str(value),
                "trades": int(len(group)),
                "win_rate_pct": float((pnl > 0).mean() * 100),
                "expectancy_r": float(net_r.mean()),
                "net_r": float(net_r.sum()),
                "pnl": float(pnl.sum()),
                "profit_factor": float(pnl[pnl > 0].sum() / gross_loss)
                if gross_loss > 0
                else None,
            }
        )
    return rows


def _quantile_stats(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    valid = frame.loc[frame[column].notna()].copy()
    if valid[column].nunique() < 4:
        return []
    valid[f"{column}_bucket"] = pd.qcut(valid[column], 4, duplicates="drop")
    return _group_stats(valid, f"{column}_bucket")


def _daily_stats(ledger: pd.DataFrame) -> dict[str, Any]:
    days = (
        ledger.assign(day=pd.to_datetime(ledger["fill_time_ms"], unit="ms", utc=True).dt.date)
        .groupby("day")
        .size()
    )
    return {
        "calendar_first_trade_day": str(days.index.min()),
        "calendar_last_trade_day": str(days.index.max()),
        "fill_trading_days": int(len(days)),
        "fill_trades_per_day_mean": float(days.mean()),
        "fill_trades_per_day_median": float(days.median()),
        "fill_trades_per_day_p90": float(days.quantile(0.90)),
        "fill_trades_per_day_max": int(days.max()),
    }


def _overlap_stats(ledger: pd.DataFrame) -> dict[str, Any]:
    events: list[tuple[int, int, str]] = []
    for row in ledger.itertuples(index=False):
        events.append((int(row.fill_time_ms), 1, str(row.symbol)))
        events.append((int(row.exit_time_ms), -1, str(row.symbol)))
    events.sort(key=lambda x: (x[0], x[1]))
    open_symbols: set[str] = set()
    max_open = 0
    same_symbol_overlap = False
    for _, delta, symbol in events:
        if delta < 0:
            open_symbols.discard(symbol)
        else:
            same_symbol_overlap = same_symbol_overlap or symbol in open_symbols
            open_symbols.add(symbol)
            max_open = max(max_open, len(open_symbols))
    return {
        "max_simultaneous_positions": int(max_open),
        "same_symbol_overlap_in_taken_trades": bool(same_symbol_overlap),
    }


def _top_winner_removal(ledger: pd.DataFrame) -> dict[str, Any]:
    pnl = ledger["pnl"].to_numpy(float)
    positive = np.sort(pnl[pnl > 0])[::-1]
    total = float(pnl.sum())
    if total <= 0 or len(positive) == 0:
        return {"count": 0, "pct_of_trades": 0.0}
    count = int(np.searchsorted(np.cumsum(positive), total, side="left") + 1)
    count = min(count, len(positive))
    return {"count": count, "pct_of_trades": float(100 * count / len(ledger))}


def _winner_loser_signatures(frame: pd.DataFrame) -> list[dict[str, Any]]:
    features = [
        "model_score_r",
        "ignition_rise",
        "pullback_depth",
        "form_over_pump_vol",
        "formation_vol_ratio",
        "pre_brk_vol_ramp",
        "vol_step_ratio",
        "up_down_vol_ratio",
        "trades_vs_btc",
        "trvbtc_lastleg",
        "pump_over_sleep_vol",
        "span_h",
        "rr",
        "brk_close_over_lvl",
        "n_taps",
        "dist_stop",
    ]
    wins = frame["pnl"] > 0
    rows: list[dict[str, Any]] = []
    for col in features:
        if col not in frame or frame[col].dropna().nunique() < 4:
            continue
        iqr = float(frame[col].quantile(0.75) - frame[col].quantile(0.25))
        if iqr <= 0 or not np.isfinite(iqr):
            continue
        win_median = float(frame.loc[wins, col].median())
        loss_median = float(frame.loc[~wins, col].median())
        rows.append(
            {
                "feature": col,
                "winner_median": win_median,
                "loser_median": loss_median,
                "median_delta_iqr": float((win_median - loss_median) / iqr),
            }
        )
    return sorted(rows, key=lambda row: abs(row["median_delta_iqr"]), reverse=True)


def _threshold_plateau(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for quantile in (0.65, 0.70, 0.75, 0.80, 0.85):
        selected = _causal_quantile_selection(frame, quantile)
        ledger, rejects = _ledger_for(frame.loc[selected], risk_pct=0.02)
        months = _calendar_stats(ledger, "M")
        metric = _metrics(ledger)
        rows.append(
            {
                "prior_score_quantile": quantile,
                **metric,
                "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
                "months": int(len(months)),
                "rejects": rejects,
            }
        )
    return rows


def _risk_stress(frame: pd.DataFrame, *, cap: float | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for risk_pct in (0.02, 0.03, 0.04, 0.05):
        ledger, rejects = _ledger_for(
            frame,
            risk_pct=risk_pct,
            max_notional_multiple=cap,
        )
        metrics = _metrics(ledger)
        risk = ledger["risk_dollars"].to_numpy(float)
        rows.append(
            {
                "risk_pct": float(100 * risk_pct),
                "max_notional_multiple": cap,
                "sizing_mode": "risk_sized_position_capped_by_notional",
                **metrics,
                "effective_risk_pct_p50": float(np.median(risk / DEPOSIT * 100)),
                "effective_risk_pct_p90": float(np.quantile(risk / DEPOSIT * 100, 0.90)),
                "effective_risk_pct_max": float(np.max(risk / DEPOSIT * 100)),
                "rejects": rejects,
            }
        )
    return rows


def _slippage_stress(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bps in (25, 50, 75, 100, 150):
        ledger, _ = _ledger_for(frame, risk_pct=0.02, slip_bps=bps)
        months = _calendar_stats(ledger, "M")
        rows.append(
            {
                "slippage_bps": bps,
                **_metrics(ledger),
                "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
                "months": int(len(months)),
            }
        )
    return rows


def _family_diagnostics(selected: pd.DataFrame) -> list[dict[str, Any]]:
    """Family-only diagnostics.

    These ledgers intentionally are not additive with the combined portfolio:
    each family is simulated alone to inspect its standalone character, while
    the deployable portfolio simulation remains the combined global ledger.
    """
    rows: list[dict[str, Any]] = []
    for family, group in selected.groupby("setup_family", observed=True):
        ledger, rejects = _ledger_for(group, risk_pct=0.02)
        months = _calendar_stats(ledger, "M")
        rows.append(
            {
                "setup_family": str(family),
                **_metrics(ledger),
                "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
                "months": int(len(months)),
                "rejects": rejects,
                "diagnostic_only": True,
            }
        )
    return rows


def _negative_controls(frame: pd.DataFrame, taken_n: int) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    shuffled_path = shuffled_score_path(CANDIDATE_POLICY)
    if shuffled_path.exists():
        shuffled = pd.read_parquet(shuffled_path)[TRADE_KEY + ["model_score_r"]]
        shuffled_frame = frame.drop(columns=["model_score_r"]).merge(
            shuffled, on=TRADE_KEY, validate="one_to_one"
        )
        selected = _causal_quantile_selection(shuffled_frame, 0.70)
        ledger, rejects = _ledger_for(shuffled_frame.loc[selected], risk_pct=0.02)
        controls["shuffled_train_target_q70"] = {
            "score_spearman": float(
                shuffled_frame["r_multiple"].corr(
                    shuffled_frame["model_score_r"], method="spearman"
                )
            ),
            **_metrics(ledger),
            "rejects": rejects,
        }

    rng = np.random.default_rng(20260709)
    random_rows = []
    for i in range(200):
        sample_idx = rng.choice(frame.index.to_numpy(), size=taken_n, replace=False)
        sample = frame.loc[sample_idx].copy()
        sample["model_score_r"] = rng.permutation(sample["model_score_r"].to_numpy())
        ledger, _ = _ledger_for(sample, risk_pct=0.02)
        metric = _metrics(ledger)
        random_rows.append(
            {
                "return_pct": metric["return_pct"],
                "max_drawdown_pct": metric["max_drawdown_pct"],
                "profit_factor": metric["profit_factor"],
                "win_rate_pct": metric["win_rate_pct"],
            }
        )
    random_frame = pd.DataFrame(random_rows)
    controls["random_same_count_200x"] = {
        "return_pct_p05": float(random_frame["return_pct"].quantile(0.05)),
        "return_pct_p50": float(random_frame["return_pct"].quantile(0.50)),
        "return_pct_p95": float(random_frame["return_pct"].quantile(0.95)),
        "profit_factor_p50": float(random_frame["profit_factor"].quantile(0.50)),
        "win_rate_pct_p50": float(random_frame["win_rate_pct"].quantile(0.50)),
    }
    return controls


def run(out_json: Path = OUT_JSON, out_md: Path = OUT_MD) -> dict[str, Any]:
    frame = _load_candidate_frame()
    selected = _selected_frame(frame)
    baseline_ledger, baseline_rejects = _ledger_for(selected, risk_pct=0.02)
    taken = _with_trade_result(baseline_ledger, selected)
    stamps = pd.to_datetime(taken["fill_time_ms"], unit="ms", utc=True)
    taken["session_utc"] = pd.cut(
        stamps.dt.hour,
        bins=[-1, 7, 15, 23],
        labels=["Asia 00-08", "Europe 08-16", "US 16-24"],
    )

    base_metrics = _metrics(baseline_ledger)
    months = _calendar_stats(baseline_ledger, "M")
    report = {
        "scope": "IS only; OOS untouched; frozen candidate audit",
        "candidate": {
            "policy": CANDIDATE_POLICY,
            "entry": "confirmed_close",
            "selection": "causal prior-forward-score q70",
            "max_open": 3,
            "one_position_per_symbol": True,
            "sizing_mode": "risk_sized_position_capped_by_notional",
            "max_notional_multiple": MAX_NOTIONAL_MULTIPLE,
            "deposit": DEPOSIT,
            "round_trip_fee_bps": COST_RATE * 10_000,
            "baseline_slippage_bps": 50,
        },
        "hypothesis_freeze": {
            "physical_levels": "entry, stop, first target and runner target are point-in-time structural anchors",
            "first_target": "level + 0.50 * (original_structural_target - level)",
            "partial": "50% closed at first target, 50% runner to original structural target",
            "runner_stop": "original structural stop",
            "break_even": "after +0.50R, with trigger floored by 2x fees+slippage in R",
            "inactivity_exit": "after 6h, if volume falls back and price has not reached the effective BE trigger: exit losers at market; non-losers arm BE",
            "model": "CatBoost expected-R, expanding weekly-forward, one frozen model/week",
            "threshold": "q70 from prior forward-scored weeks only",
            "position_sizing": "position_notional = min(risk_dollars / structural_stop_distance, deposit * max_notional_multiple); this reduces effective risk when the stop is too tight",
        },
        "population": {
            "candidate_policy_rows": int(len(frame)),
            "q70_selected_before_portfolio": int(len(selected)),
            "taken_after_portfolio_constraints": int(len(baseline_ledger)),
        },
        "time_and_identity_checks": {
            "unique_trade_key": bool(not frame.duplicated(TRADE_KEY).any()),
            "feature_cutoff_before_future_start": bool(
                (frame["feature_cutoff_time_ms"] < frame["future_start_time_ms"]).all()
            ),
            "outcomes_after_fill": bool((frame["outcome_time_ms"] > frame["fill_time_ms"]).all()),
            "train_targets_resolved_by_freeze": bool(
                (
                    frame["train_max_target_resolution_time_ms"]
                    <= frame["model_freeze_time_ms"]
                ).all()
            ),
            "model_frozen_before_fill": bool(
                (frame["model_freeze_time_ms"] <= frame["fill_time_ms"]).all()
            ),
            "max_resolved_outcome_utc": pd.Timestamp(
                int(frame["outcome_time_ms"].max()), unit="ms", tz="UTC"
            ).isoformat(),
            "dev_cutoff_utc": pd.Timestamp(DEV_END_MS, unit="ms", tz="UTC").isoformat(),
            "all_outcomes_inside_is_cutoff": bool((frame["outcome_time_ms"] <= DEV_END_MS).all()),
        },
        "prediction_controls": {
            "real_score_spearman": float(
                frame["r_multiple"].corr(frame["model_score_r"], method="spearman")
            ),
            **_negative_controls(frame, len(baseline_ledger)),
        },
        "baseline_2pct_1x_notional_cap": {
            **base_metrics,
            "positive_day_pct": float(
                100 * base_metrics["positive_days"] / base_metrics["trading_days"]
            ),
            "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
            "months": int(len(months)),
            "monthly_pnl": {row["period"]: row["net_pnl"] for row in months},
            "monthly_net_r": {row["period"]: row["net_r"] for row in months},
            "rejects": baseline_rejects,
            **_daily_stats(baseline_ledger),
            **_overlap_stats(baseline_ledger),
            "top_winner_removal": _top_winner_removal(baseline_ledger),
        },
        "risk_stress_1x_notional_cap": _risk_stress(selected, cap=MAX_NOTIONAL_MULTIPLE),
        "risk_stress_without_notional_cap_diagnostic_only": _risk_stress(selected, cap=None),
        "family_only_diagnostics_not_additive": _family_diagnostics(selected),
        "family_simulation_contract": {
            "portfolio": "combined breakout+cap signal stream",
            "overlap_control": "global max_open=3 and one_position_per_symbol across both families",
            "family_only_tables": "diagnostic only; do not sum as deployable portfolio PnL",
        },
        "slippage_stress": _slippage_stress(selected),
        "threshold_plateau": _threshold_plateau(frame),
        "monthly_stability": months,
        "setup_family_distribution": _group_stats(taken, "setup_family"),
        "session_distribution": _group_stats(taken, "session_utc"),
        "timeframe_distribution": _group_stats(taken, "tf"),
        "pump_size_quartiles": _quantile_stats(taken, "ignition_rise"),
        "pullback_depth_quartiles": _quantile_stats(taken, "pullback_depth"),
        "volume_dynamics_quartiles": {
            col: _quantile_stats(taken, col)
            for col in (
                "form_over_pump_vol",
                "formation_vol_ratio",
                "pre_brk_vol_ramp",
                "vol_step_ratio",
                "up_down_vol_ratio",
                "trvbtc_lastleg",
            )
        },
        "winner_loser_signatures": _winner_loser_signatures(taken)[:15],
        "invalidated_evidence_not_used": [
            "fixed +8% target classifier: arbitrary descriptive sensitivity, not candidate proof",
            "global same-sample score quantiles: future score distribution lookahead",
            "GroupKFold/OOF economics: folds train on future weeks",
            "(symbol, entry_time_ms)-only joins: many-to-many across timeframes",
            "late-December outcomes crossing into January 2026: excluded by IS cutoff clamp",
        ],
        "limits": [
            "policy family was selected post-hoc inside IS",
            "threshold/policy plateau is IS robustness evidence, not independent validation",
            "2026 is not treated as pristine OOS in this run",
        ],
    }

    out_json.write_text(
        json.dumps(_jsonable(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# Triple-tap frozen candidate IS audit",
        "",
        "Scope: IS only. OOS was not touched.",
        "",
        "## Frozen hypothesis",
        "",
        f"- Policy: `{CANDIDATE_POLICY}`.",
        "- Entry: confirmed close above point-in-time breakout level.",
        "- Exit: 50% at structural 50% measured-move target; 50% runner to original structural target.",
        "- Protection: BE after +0.50R; 6h inactivity exits losers or arms BE for non-losers.",
        "- Selection: causal q70 from prior forward-scored weeks only.",
        "- Portfolio: max 3 concurrent, one position per symbol, risk sizing capped at 1x notional.",
        "",
        "## Baseline IS result",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Trades | {base_metrics['trades']} |",
        f"| Win rate | {base_metrics['win_rate_pct']:.1f}% |",
        f"| Trading days | {base_metrics['trading_days']} |",
        f"| Positive days | {base_metrics['positive_days']} ({report['baseline_2pct_1x_notional_cap']['positive_day_pct']:.1f}%) |",
        f"| Return | {base_metrics['return_pct']:+.1f}% |",
        f"| Max DD | {base_metrics['max_drawdown_pct']:.1f}% |",
        f"| Profit factor | {base_metrics['profit_factor']:.2f} |",
        f"| Median duration | {base_metrics['duration_hours_p50']:.1f}h |",
        f"| P90 duration | {base_metrics['duration_hours_p90']:.1f}h |",
        f"| Trades/day mean | {base_metrics['trades_per_day_mean']:.2f} |",
        f"| Trades/day max | {base_metrics['trades_per_day_max']} |",
        "",
        "## Risk stress with 1x notional cap",
        "",
        "| Risk target | Effective risk p50 | Return | Max DD | PF |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in report["risk_stress_1x_notional_cap"]:
        lines.append(
            f"| {row['risk_pct']:.0f}% | {row['effective_risk_pct_p50']:.2f}% | "
            f"{row['return_pct']:+.1f}% | {row['max_drawdown_pct']:.1f}% | "
            f"{row['profit_factor']:.2f} |"
        )
    lines += [
        "",
        "## Monthly stability",
        "",
        "| Month | Trades | PnL | Net R | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in months:
        pf = row["profit_factor"]
        lines.append(
            f"| {row['period']} | {row['n']} | ${row['net_pnl']:+.0f} | "
            f"{row['net_r']:+.1f} | {pf:.2f} |"
        )
    lines += [
        "",
        "## Honesty checks",
        "",
        f"- Real score/realised-R Spearman: {report['prediction_controls']['real_score_spearman']:.3f}.",
        f"- Shuffled-train q70 control return: {report['prediction_controls']['shuffled_train_target_q70']['return_pct']:+.1f}%.",
        f"- Random same-count median return: {report['prediction_controls']['random_same_count_200x']['return_pct_p50']:+.1f}%.",
        "- All listed time-contract checks passed.",
        "",
        "Full tables are in the JSON artifact.",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_json} and {out_md}", flush=True)
    return report


if __name__ == "__main__":
    run()
