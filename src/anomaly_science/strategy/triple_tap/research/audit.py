"""Reproducible IS-only honesty and robustness audit for triple-tap.

The primary population is strict weekly-forward scores on confirmed close
entries.  Every policy decision uses only information available at fill time:
weekly models train on resolved historical labels, and score thresholds are
estimated from earlier forward-scored weeks only.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.economics import COST_RATE, DEPOSIT, RES
from anomaly_science.strategy.pump_long.research.context import DEV_END_MS

DEFAULT_OUT = Path("research/triple_tap_is_honesty_audit.json")
DEFAULT_MARKDOWN = Path("research/triple_tap_is_honesty_audit.md")


def _load() -> pd.DataFrame:
    labels = pd.read_parquet(RES / "labels_close.parquet")
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    setups = setups.loc[setups["valid_entry"], TRADE_KEY + ["stop"]]
    features = pd.read_parquet(RES / "features.parquet")
    scores = pd.read_parquet(RES / "ev_walkforward.parquet")
    frame = labels.merge(setups, on=TRADE_KEY, validate="one_to_one")
    frame = frame.merge(features, on=TRADE_KEY, validate="one_to_one", suffixes=("", "_feature"))
    frame = frame.merge(scores, on=TRADE_KEY, validate="one_to_one")
    frame["dist_stop"] = (frame["entry_price"] - frame["stop"]) / frame["entry_price"]
    return frame.sort_values(["fill_time_ms", "symbol", "tf"]).reset_index(drop=True)


def _causal_quantile_selection(frame: pd.DataFrame, quantile: float) -> pd.Series:
    selected = pd.Series(False, index=frame.index)
    weeks = frame["model_week"].drop_duplicates().tolist()
    for week in weeks:
        current = frame["model_week"] == week
        prior = frame["model_week"] < week
        if prior.any():
            threshold = float(frame.loc[prior, "model_score_r"].quantile(quantile))
            selected.loc[current] = frame.loc[current, "model_score_r"] >= threshold
    return selected


def _ledger(
    candidates: pd.DataFrame,
    *,
    risk_pct: float,
    max_open: int,
    slip_pct: float,
    compound: bool = False,
    priority_by_score: bool = True,
    max_notional_multiple: float | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    sort_cols = ["fill_time_ms"] + (["model_score_r"] if priority_by_score else []) + ["symbol", "tf"]
    ascending = [True] + ([False] if priority_by_score else []) + [True, True]
    order = candidates.sort_values(sort_cols, ascending=ascending)
    equity = float(DEPOSIT)
    open_positions: list[dict] = []
    closed: list[dict] = []
    rejects = {
        "global_concurrency": 0,
        "same_symbol": 0,
        "insolvent": 0,
    }
    for row in order.itertuples(index=False):
        now = int(row.fill_time_ms)
        due = sorted((p for p in open_positions if p["exit_time_ms"] <= now), key=lambda p: p["exit_time_ms"])
        for position in due:
            equity += position["pnl"]
            position["equity_after"] = equity
            closed.append(position)
        open_positions = [p for p in open_positions if p["exit_time_ms"] > now]
        if equity <= 0:
            rejects["insolvent"] += 1
            continue
        if any(p["symbol"] == row.symbol for p in open_positions):
            rejects["same_symbol"] += 1
            continue
        if len(open_positions) >= max_open:
            rejects["global_concurrency"] += 1
            continue
        net_r = float(row.r_multiple) - (COST_RATE + slip_pct) / float(row.dist_stop)
        capital_base = equity if compound else DEPOSIT
        risk_dollars = capital_base * risk_pct
        notional_dollars = risk_dollars / float(row.dist_stop)
        if max_notional_multiple is not None:
            risk_dollars = min(
                risk_dollars,
                capital_base * max_notional_multiple * float(row.dist_stop),
            )
            notional_dollars = risk_dollars / float(row.dist_stop)
        open_positions.append(
            {
                **{key: getattr(row, key) for key in TRADE_KEY},
                "fill_time_ms": now,
                "exit_time_ms": int(row.outcome_time_ms),
                "label": row.label,
                "net_r": net_r,
                "pnl": risk_dollars * net_r,
                "risk_dollars": risk_dollars,
                "notional_dollars": notional_dollars,
                "model_score_r": float(row.model_score_r),
            }
        )
    for position in sorted(open_positions, key=lambda p: p["exit_time_ms"]):
        equity += position["pnl"]
        position["equity_after"] = equity
        closed.append(position)
    return pd.DataFrame(closed).sort_values("exit_time_ms").reset_index(drop=True), rejects


def _metrics(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return {"trades": 0}
    pnl = ledger["pnl"].to_numpy(float)
    curve = DEPOSIT + np.r_[0.0, np.cumsum(pnl)]
    drawdown = curve / np.maximum.accumulate(curve) - 1.0
    wins = pnl > 0
    daily = ledger.assign(day=pd.to_datetime(ledger["exit_time_ms"], unit="ms", utc=True).dt.date).groupby("day")["pnl"].sum()
    durations_h = (ledger["exit_time_ms"] - ledger["fill_time_ms"]) / 3_600_000
    positive = np.sort(pnl[pnl > 0])[::-1]
    total = float(pnl.sum())
    top_n = int(np.searchsorted(np.cumsum(positive), total, side="left") + 1) if total > 0 else 0
    top_n = min(top_n, len(positive))
    gross_loss = -float(pnl[pnl < 0].sum())
    return {
        "trades": int(len(ledger)),
        "return_pct": float((curve[-1] / DEPOSIT - 1) * 100),
        "max_drawdown_pct": float(drawdown.min() * 100),
        "win_rate_pct": float(wins.mean() * 100),
        "expectancy_r": float(ledger["net_r"].mean()),
        "profit_factor": float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
        "trading_days": int(len(daily)),
        "positive_days": int((daily > 0).sum()),
        "trades_per_day_mean": float(ledger.assign(day=pd.to_datetime(ledger["fill_time_ms"], unit="ms", utc=True).dt.date).groupby("day").size().mean()),
        "trades_per_day_median": float(ledger.assign(day=pd.to_datetime(ledger["fill_time_ms"], unit="ms", utc=True).dt.date).groupby("day").size().median()),
        "trades_per_day_p90": float(ledger.assign(day=pd.to_datetime(ledger["fill_time_ms"], unit="ms", utc=True).dt.date).groupby("day").size().quantile(0.9)),
        "trades_per_day_max": int(ledger.assign(day=pd.to_datetime(ledger["fill_time_ms"], unit="ms", utc=True).dt.date).groupby("day").size().max()),
        "duration_hours_p50": float(durations_h.median()),
        "duration_hours_p90": float(durations_h.quantile(0.9)),
        "duration_hours_p99": float(durations_h.quantile(0.99)),
        "duration_hours_max": float(durations_h.max()),
        "top_winners_to_zero_count": top_n,
        "top_winners_to_zero_pct_of_trades": float(100 * top_n / len(ledger)),
    }


def _group_stats(frame: pd.DataFrame, column: str) -> list[dict]:
    rows: list[dict] = []
    for value, group in frame.groupby(column, observed=True):
        r = group["net_r_descriptive"].to_numpy(float)
        loss = -r[r < 0].sum()
        rows.append(
            {
                "value": str(value),
                "n": int(len(group)),
                "win_rate_pct": float((r > 0).mean() * 100),
                "expectancy_r": float(r.mean()),
                "profit_factor": float(r[r > 0].sum() / loss) if loss > 0 else None,
            }
        )
    return rows


def _quantile_stats(frame: pd.DataFrame, column: str) -> list[dict]:
    valid = frame.loc[frame[column].notna()].copy()
    if valid[column].nunique() < 4:
        return []
    valid["bucket"] = pd.qcut(valid[column], 4, duplicates="drop")
    return _group_stats(valid, "bucket")


def _winner_loser_signatures(frame: pd.DataFrame) -> list[dict]:
    excluded = set(
        TRADE_KEY
        + [
            "r_multiple", "label_win", "fwd_mfe_r", "fwd_mae_r", "fwd_mfe_pct",
            "fwd_mae_pct", "fwd_ret_pct", "ran_08", "ran_15", "bars_to_outcome",
            "outcome_time_ms", "net_r_descriptive", "model_score_r", "predicted_net_r",
            "cost_r_50bps", "vol_pct_rank", "vol_step_ratio", "vol_recent", "vol_base",
        ]
    )
    numeric = [c for c in frame.select_dtypes(include=[np.number]).columns if c not in excluded and not c.endswith("_time_ms")]
    wins = frame["net_r_descriptive"] > 0
    rows = []
    for col in numeric:
        scale = float(frame[col].quantile(0.75) - frame[col].quantile(0.25))
        if not np.isfinite(scale) or scale == 0:
            continue
        delta = float((frame.loc[wins, col].median() - frame.loc[~wins, col].median()) / scale)
        if np.isfinite(delta):
            rows.append({"feature": col, "median_delta_iqr": delta})
    return sorted(rows, key=lambda x: abs(x["median_delta_iqr"]), reverse=True)[:12]


def _calendar_stats(ledger: pd.DataFrame, freq: str) -> list[dict]:
    work = ledger.copy()
    stamps = pd.to_datetime(work["exit_time_ms"], unit="ms", utc=True).dt.tz_localize(None)
    work["period"] = stamps.dt.to_period(freq).astype(str)
    rows = []
    for period, group in work.groupby("period"):
        r = group["net_r"].to_numpy(float)
        loss = -r[r < 0].sum()
        rows.append(
            {
                "period": period,
                "n": int(len(group)),
                "net_r": float(r.sum()),
                "net_pnl": float(group["pnl"].sum()),
                "expectancy_r": float(r.mean()),
                "profit_factor": float(r[r > 0].sum() / loss) if loss > 0 else None,
            }
        )
    return rows


def _clustered_week_bootstrap(ledger: pd.DataFrame, seed: int = 7, draws: int = 4000) -> dict:
    work = ledger.copy()
    work["week"] = pd.to_datetime(work["fill_time_ms"], unit="ms", utc=True).dt.strftime("%G-W%V")
    clusters = [g["net_r"].to_numpy(float) for _, g in work.groupby("week")]
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        sample = [clusters[j] for j in rng.integers(0, len(clusters), len(clusters))]
        means[i] = np.concatenate(sample).mean()
    return {
        "weeks": len(clusters),
        "expectancy_r_ci05": float(np.quantile(means, 0.05)),
        "expectancy_r_ci50": float(np.quantile(means, 0.50)),
        "expectancy_r_ci95": float(np.quantile(means, 0.95)),
        "probability_expectancy_positive": float((means > 0).mean()),
    }


def run(out_path: Path = DEFAULT_OUT, markdown_path: Path = DEFAULT_MARKDOWN) -> dict:
    frame = _load()
    all_close_labels = pd.read_parquet(RES / "labels_close.parquet")
    resolved_outcomes = all_close_labels["outcome_time_ms"].dropna()
    shuffled_path = RES / "ev_walkforward_shuffled.parquet"
    shuffled_spearman = None
    if shuffled_path.exists():
        shuffled = pd.read_parquet(shuffled_path)[TRADE_KEY + ["model_score_r"]]
        shuffled = frame[TRADE_KEY + ["r_multiple"]].merge(
            shuffled, on=TRADE_KEY, validate="one_to_one"
        )
        shuffled_spearman = float(
            shuffled["r_multiple"].corr(shuffled["model_score_r"], method="spearman")
        )
    frame["cost_r_50bps"] = (COST_RATE + 0.005) / frame["dist_stop"]
    frame["predicted_net_r"] = frame["model_score_r"] - frame["cost_r_50bps"]
    frame["selected"] = frame["predicted_net_r"] > 0.0
    selected = frame.loc[frame["selected"]].copy()
    selected["net_r_descriptive"] = selected["r_multiple"] - (COST_RATE + 0.005) / selected["dist_stop"]

    time_checks = {
        "unique_trade_key": bool(not frame.duplicated(TRADE_KEY).any()),
        "features_before_future": bool((frame["feature_cutoff_time_ms"] < frame["future_start_time_ms"]).all()),
        "outcomes_after_features": bool((frame["outcome_time_ms"] > frame["feature_cutoff_time_ms"]).all()),
        "training_labels_resolved_by_freeze": bool(
            (frame["train_max_target_resolution_time_ms"] <= frame["model_freeze_time_ms"]).all()
        ),
        "model_frozen_before_fill": bool((frame["model_freeze_time_ms"] <= frame["fill_time_ms"]).all()),
    }

    risk_table = []
    baseline_ledger = pd.DataFrame()
    baseline_rejects: dict[str, int] = {}
    for risk in (0.02, 0.03, 0.04, 0.05):
        ledger, rejects = _ledger(selected, risk_pct=risk, max_open=3, slip_pct=0.005)
        if risk == 0.02:
            baseline_ledger, baseline_rejects = ledger, rejects
        risk_table.append({"risk_pct": risk * 100, **_metrics(ledger), "rejects": rejects})

    compounded_risk_table = []
    for risk in (0.02, 0.03, 0.04, 0.05):
        ledger, rejects = _ledger(
            selected, risk_pct=risk, max_open=3, slip_pct=0.005, compound=True
        )
        compounded_risk_table.append(
            {"risk_pct": risk * 100, **_metrics(ledger), "rejects": rejects}
        )

    blind_ledger, blind_rejects = _ledger(
        frame, risk_pct=0.02, max_open=3, slip_pct=0.005, priority_by_score=False
    )

    slippage = []
    for bps in (25, 50, 75, 100, 150):
        ledger, _ = _ledger(selected, risk_pct=0.02, max_open=3, slip_pct=bps / 10_000)
        slippage.append({"slippage_bps": bps, **_metrics(ledger)})
    threshold = []
    for margin_r in (0.0, 0.25, 0.50, 0.75, 1.0):
        mask = frame["predicted_net_r"] > margin_r
        ledger, _ = _ledger(frame.loc[mask], risk_pct=0.02, max_open=3, slip_pct=0.005)
        threshold.append({"minimum_predicted_net_r": margin_r, **_metrics(ledger)})

    capacity_quantiles = []
    for q in (0.65, 0.70, 0.75, 0.80, 0.85):
        mask = _causal_quantile_selection(frame, q)
        ledger, _ = _ledger(frame.loc[mask], risk_pct=0.02, max_open=3, slip_pct=0.005)
        capacity_quantiles.append({"prior_score_quantile": q, **_metrics(ledger)})

    taken = baseline_ledger[TRADE_KEY].merge(selected, on=TRADE_KEY, validate="one_to_one")
    taken["net_r_descriptive"] = taken["r_multiple"] - (COST_RATE + 0.005) / taken["dist_stop"]
    stamps = pd.to_datetime(taken["fill_time_ms"], unit="ms", utc=True)
    taken["session_utc"] = pd.cut(stamps.dt.hour, [-1, 7, 15, 23], labels=["Asia 00-08", "Europe 08-16", "US 16-24"])
    report = {
        "scope": "IS only; confirmed-close entries; strict expanding weekly-forward expected-R scores",
        "population": {"forward_scored": int(len(frame)), "positive_predicted_net_ev_candidates": int(len(selected))},
        "time_and_identity_checks": time_checks,
        "is_boundary_governance": {
            "cutoff_utc": pd.Timestamp(DEV_END_MS, unit="ms", tz="UTC").isoformat(),
            "unresolved_rows_excluded": int(all_close_labels["r_multiple"].isna().sum()),
            "max_resolved_outcome_utc": pd.Timestamp(
                int(resolved_outcomes.max()), unit="ms", tz="UTC"
            ).isoformat(),
            "all_resolved_outcomes_at_or_before_cutoff": bool(
                (resolved_outcomes <= DEV_END_MS).all()
            ),
        },
        "model_diagnostics": {
            "forward_spearman_realized_r": float(
                frame["r_multiple"].corr(frame["model_score_r"], method="spearman")
            ),
            "forward_mae_r": float((frame["r_multiple"] - frame["model_score_r"]).abs().mean()),
            "shuffled_train_target_forward_spearman": shuffled_spearman,
        },
        "baseline_2pct_max3": {**_metrics(baseline_ledger), "rejects": baseline_rejects},
        "blind_2pct_max3": {**_metrics(blind_ledger), "rejects": blind_rejects},
        "risk_sensitivity": risk_table,
        "compounded_risk_sensitivity_unlimited_capacity_assumption": compounded_risk_table,
        "slippage_sensitivity": slippage,
        "expected_ev_margin_sensitivity": threshold,
        "capacity_quantile_sensitivity": capacity_quantiles,
        "session_distribution": _group_stats(taken, "session_utc"),
        "timeframe_distribution": _group_stats(taken, "tf"),
        "setup_type_distribution": _group_stats(taken, "setup_type"),
        "pump_size_quartiles": _quantile_stats(taken, "ignition_rise"),
        "pullback_depth_quartiles": _quantile_stats(taken, "pullback_depth"),
        "post_high_volume_quartiles": {
            col: _quantile_stats(taken, col)
            for col in ("form_over_pump_vol", "pre_brk_vol_ramp", "trvbtc_lastleg", "up_down_vol_ratio")
        },
        "winner_loser_signatures": _winner_loser_signatures(taken),
        "monthly_stability": _calendar_stats(baseline_ledger, "M"),
        "weekly_cluster_bootstrap": _clustered_week_bootstrap(baseline_ledger),
        "known_invalid_evidence": [
            "break-entry economics: exact intrabar crossing order is unavailable",
            "GroupKFold OOF economics: folds train on future weeks",
            "global same-sample top-quantile threshold: uses future score distribution",
            "previous (symbol, entry_time_ms) joins: many-to-many across timeframes",
            "ran_08 classifier: arbitrary fixed-percent target, retained as historical sensitivity only",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base = report["baseline_2pct_max3"]
    lines = [
        "# Triple-tap IS honesty audit",
        "",
        report["scope"],
        "",
        f"Forward-scored rows: {len(frame)}; positive predicted net-EV candidates: {len(selected)}; taken: {base['trades']}.",
        f"Baseline (2% risk, max 3, one position/symbol, 50 bps slip): return {base['return_pct']:.1f}%, PF {base['profit_factor']:.2f}, max DD {base['max_drawdown_pct']:.1f}%, win rate {base['win_rate_pct']:.1f}%.",
        f"Trading days: {base['trading_days']}; positive days: {base['positive_days']}; median duration {base['duration_hours_p50']:.1f}h; p90 {base['duration_hours_p90']:.1f}h.",
        f"Remove the best {base['top_winners_to_zero_count']} trades ({base['top_winners_to_zero_pct_of_trades']:.1f}% of trades) to erase total profit.",
        "",
        "## Prediction controls",
        "",
        f"Real strict-forward score vs realised R: Spearman {report['model_diagnostics']['forward_spearman_realized_r']:.3f}; shuffled-train control: {report['model_diagnostics']['shuffled_train_target_forward_spearman']:.3f}.",
        "",
        "## Fixed initial-deposit risk sensitivity",
        "",
        "| Risk/trade | Return | Max DD | PF |",
        "|---:|---:|---:|---:|",
    ]
    for row in risk_table:
        lines.append(
            f"| {row['risk_pct']:.0f}% | {row['return_pct']:.1f}% | {row['max_drawdown_pct']:.1f}% | {row['profit_factor']:.2f} |"
        )
    lines += [
        "",
        "## Monthly stability",
        "",
        "| Month | Trades | Net R | PF |",
        "|---|---:|---:|---:|",
    ]
    for row in report["monthly_stability"]:
        lines.append(
            f"| {row['period']} | {row['n']} | {row['net_r']:+.1f} | {row['profit_factor']:.2f} |"
        )
    lines += [
        "",
        "## Descriptive plateaus (post-hoc, not filters)",
        "",
        "| Axis | Bucket | N | E[R] | PF |",
        "|---|---|---:|---:|---:|",
    ]
    for axis, rows in (("session", report["session_distribution"]), ("TF", report["timeframe_distribution"])):
        for row in rows:
            lines.append(
                f"| {axis} | {row['value']} | {row['n']} | {row['expectancy_r']:+.2f} | {row['profit_factor']:.2f} |"
            )
    lines += [
        "",
        "## Retracted evidence",
        "",
    ]
    lines.extend(f"- {item}" for item in report["known_invalid_evidence"])
    lines += ["", "Full machine-readable tables are in the JSON artifact."]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path} and {markdown_path}", flush=True)
    return report


if __name__ == "__main__":
    run()
