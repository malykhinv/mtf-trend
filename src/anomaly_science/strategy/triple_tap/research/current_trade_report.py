"""Current IS trade report split by setup family.

This report is explicit about the distinction between deployable selection and
raw detector diagnostics.  The primary portfolio uses the frozen candidate
policy with strict weekly-forward CatBoost expected-R scores and a causal
prior-score q70 threshold.  Raw detector accounting is retained only as a
diagnostic view and is not the headline trading result.

Views:

* selected portfolio: breakout and cap compete for the same slots/symbols after
  causal ML selection;
* raw detector diagnostic: no ML selection, not deployable as the result;
* family-only diagnostics: each family simulated alone, not additive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.accounting import POLICY
from anomaly_science.strategy.triple_tap.research.audit import (
    _calendar_stats,
    _causal_quantile_selection,
    _ledger,
    _metrics,
)
from anomaly_science.strategy.triple_tap.research.economics import COST_RATE, DEPOSIT, RES
from anomaly_science.strategy.triple_tap.research.policy_ev_model import (
    CANDIDATE_POLICY,
    score_path,
    shuffled_score_path,
)

OUT_JSON = Path("research/triple_tap_current_trade_report.json")
OUT_MD = Path("research/triple_tap_current_trade_report.md")
OUT_TRADES_JSON = Path("research/triple_tap_current_trade_trades.json")
OUT_TRADES_CSV = Path("research/triple_tap_current_trade_trades.csv")
OUT_DASHBOARD_JSON = Path("research/triple_tap_current_trade_dashboard.json")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if pd.isna(value):
        return None
    return value


MAX_NOTIONAL_MULTIPLE = 1.0
SELECTION_QUANTILE = 0.70


def _candidate_frame(*, with_scores: bool) -> pd.DataFrame:
    policies = pd.read_parquet(RES / "execution_policies.parquet")
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    features = pd.read_parquet(RES / "features.parquet")
    candidates = policies[
        (policies["entry_mode"] == "close")
        & (policies["exit_policy"] == POLICY)
        & policies["r_multiple"].notna()
    ].copy()
    if candidates.duplicated(TRADE_KEY).any():
        raise ValueError("candidate policy rows are not unique by trade key")
    if setups.duplicated(TRADE_KEY).any():
        raise ValueError("setup rows are not unique by trade key")
    if features.duplicated(TRADE_KEY).any():
        raise ValueError("feature rows are not unique by trade key")

    enrich_cols = [
        "level",
        "take",
        "culmination",
        "setup_family",
        "ignition_rise",
        "sleep_range_pct",
        "sleep_high_vs_level",
        "pre_pump_atr_pct",
        "last_tap_level_ratio",
        "prior_close_above_level_count",
        "pre_brk_vol_ramp",
        "rr",
        "span_h",
    ]
    frame = candidates.merge(setups[TRADE_KEY + enrich_cols], on=TRADE_KEY, validate="one_to_one")
    feature_cols = [
        "pullback_depth",
        "form_over_pump_vol",
        "formation_vol_ratio",
        "vol_step_ratio",
        "up_down_vol_ratio",
        "trades_vs_btc",
        "trvbtc_lastleg",
        "brk_close_over_lvl",
        "n_taps",
    ]
    available_feature_cols = [col for col in feature_cols if col in features.columns and col not in frame.columns]
    if available_feature_cols:
        frame = frame.merge(features[TRADE_KEY + available_feature_cols], on=TRADE_KEY, validate="one_to_one")
    if with_scores:
        scores_file = score_path(CANDIDATE_POLICY)
        shuffled_file = shuffled_score_path(CANDIDATE_POLICY)
        if not scores_file.exists():
            raise FileNotFoundError(
                f"missing policy score artifact {scores_file}; run policy_ev_model.run_candidate_policy_controls first"
            )
        scores = pd.read_parquet(scores_file)
        if scores.duplicated(TRADE_KEY).any():
            raise ValueError("policy score rows are not unique by trade key")
        required = {"model_score_r", "model_week", "model_freeze_time_ms", "train_max_target_resolution_time_ms"}
        missing = sorted(required - set(scores.columns))
        if missing:
            raise ValueError(f"policy score artifact is missing required columns: {missing}")
        frame = frame.merge(scores, on=TRADE_KEY, validate="one_to_one")
        if not (frame["train_max_target_resolution_time_ms"] <= frame["model_freeze_time_ms"]).all():
            raise ValueError("training target resolution crosses weekly freeze")
        if not (frame["model_freeze_time_ms"] <= frame["fill_time_ms"]).all():
            raise ValueError("model was not frozen before fill time")
        if shuffled_file.exists():
            shuffled = pd.read_parquet(shuffled_file)
            if shuffled.duplicated(TRADE_KEY).any():
                raise ValueError("shuffled policy score rows are not unique by trade key")
    else:
        frame["model_score_r"] = 0.0
    return frame.sort_values(["fill_time_ms", "symbol", "tf"]).reset_index(drop=True)


def _selected_frame(frame: pd.DataFrame) -> pd.DataFrame:
    selected = _causal_quantile_selection(frame, SELECTION_QUANTILE)
    return frame.loc[selected].copy()


def _run_ledger(frame: pd.DataFrame, risk_pct: float = 0.02) -> tuple[pd.DataFrame, dict[str, int]]:
    return _ledger(
        frame,
        risk_pct=risk_pct,
        max_open=3,
        slip_pct=0.005,
        priority_by_score=True,
        max_notional_multiple=MAX_NOTIONAL_MULTIPLE,
    )


def _run_raw_ledger(frame: pd.DataFrame, risk_pct: float = 0.02) -> tuple[pd.DataFrame, dict[str, int]]:
    return _ledger(
        frame,
        risk_pct=risk_pct,
        max_open=3,
        slip_pct=0.005,
        priority_by_score=False,
        max_notional_multiple=MAX_NOTIONAL_MULTIPLE,
    )


def _family_contribution(ledger: pd.DataFrame, frame: pd.DataFrame) -> list[dict[str, Any]]:
    enriched = ledger.merge(frame[TRADE_KEY + ["setup_family"]], on=TRADE_KEY, validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for family, group in enriched.groupby("setup_family", observed=True):
        pnl = group["pnl"].to_numpy(float)
        gross_loss = -float(pnl[pnl < 0].sum())
        rows.append(
            {
                "setup_family": str(family),
                "trades": int(len(group)),
                "return_pct": float(100 * pnl.sum() / DEPOSIT),
                "win_rate_pct": float((pnl > 0).mean() * 100),
                "expectancy_r": float(group["net_r"].mean()),
                "profit_factor": float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
                "pnl": float(pnl.sum()),
            }
        )
    return rows


def _group_metrics(ledger: pd.DataFrame, frame: pd.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    extra_keys = [key for key in keys if key not in TRADE_KEY]
    enriched = ledger.merge(frame[TRADE_KEY + extra_keys], on=TRADE_KEY, validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for values, group in enriched.groupby(keys, observed=True):
        if not isinstance(values, tuple):
            values = (values,)
        pnl = group["pnl"].to_numpy(float)
        gross_loss = -float(pnl[pnl < 0].sum())
        row = {key: str(value) for key, value in zip(keys, values)}
        row.update(
            trades=int(len(group)),
            return_pct=float(100 * pnl.sum() / DEPOSIT),
            win_rate_pct=float((pnl > 0).mean() * 100),
            expectancy_r=float(group["net_r"].mean()),
            profit_factor=float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
            pnl=float(pnl.sum()),
        )
        rows.append(row)
    return rows


def _session(hour: int) -> str:
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 13:
        return "europe"
    if 13 <= hour < 21:
        return "us"
    return "late_us"


def _enriched_trades(ledger: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    enrich_cols = [
        "setup_family",
        "entry_price",
        "level",
        "stop",
        "take",
        "culmination",
        "rr",
        "span_h",
        "sleep_range_pct",
        "pump_over_sleep_vol",
        "pump_over_sleep_trades",
        "pre_brk_vol_ramp",
        "ignition_rise",
    ]
    available = [col for col in enrich_cols if col in frame.columns]
    trades = ledger.merge(frame[TRADE_KEY + available], on=TRADE_KEY, validate="one_to_one").copy()
    if trades.empty:
        return trades
    fill = pd.to_datetime(trades["fill_time_ms"], unit="ms", utc=True)
    exit_time = pd.to_datetime(trades["exit_time_ms"], unit="ms", utc=True)
    trades.insert(0, "trade_id", [f"trd_{i:05d}" for i in range(len(trades))])
    trades["fill_iso"] = fill.dt.strftime("%Y-%m-%d %H:%M:%S")
    trades["exit_iso"] = exit_time.dt.strftime("%Y-%m-%d %H:%M:%S")
    trades["day"] = fill.dt.strftime("%Y-%m-%d")
    trades["week"] = fill.dt.strftime("%G-W%V")
    trades["month"] = fill.dt.strftime("%Y-%m")
    trades["hour"] = fill.dt.hour.astype(int)
    trades["session"] = trades["hour"].map(_session)
    trades["outcome"] = np.where(trades["pnl"].to_numpy(float) > 0, "win", "loss")
    trades["duration_hours"] = (trades["exit_time_ms"] - trades["fill_time_ms"]) / 3_600_000
    trades["return_pct_of_deposit"] = trades["pnl"] / DEPOSIT * 100
    return trades


def _metric_row(group: pd.DataFrame, keys: dict[str, Any]) -> dict[str, Any]:
    if group.empty:
        return {**keys, "trades": 0}
    pnl = group["pnl"].to_numpy(float)
    gross_loss = -float(pnl[pnl < 0].sum())
    return {
        **keys,
        "trades": int(len(group)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl <= 0).sum()),
        "return_pct": float(100 * pnl.sum() / DEPOSIT),
        "win_rate_pct": float((pnl > 0).mean() * 100),
        "expectancy_r": float(group["net_r"].mean()),
        "profit_factor": float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
        "pnl": float(pnl.sum()),
    }


def _dashboard_slices(trades: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if trades.empty:
        return {key: [] for key in ("tf", "day", "week", "month", "session", "setup_family", "outcome")}
    result: dict[str, list[dict[str, Any]]] = {}
    for key in ("tf", "day", "week", "month", "session", "setup_family", "outcome"):
        rows: list[dict[str, Any]] = []
        for value, group in trades.groupby(key, observed=True):
            rows.append(_metric_row(group, {key: str(value)}))
        rows.sort(key=lambda row: (row.get(key, ""), row["trades"]))
        result[key] = rows
    return result


def _family_only(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family, group in frame.groupby("setup_family", observed=True):
        ledger, rejects = _run_ledger(group)
        months = _calendar_stats(ledger, "M") if not ledger.empty else []
        rows.append(
            {
                "setup_family": str(family),
                **_metrics(ledger),
                "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
                "months": int(len(months)),
                "rejects": rejects,
                "diagnostic_only_not_additive": True,
            }
        )
    return rows


def _risk_stress(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for risk_pct in (0.02, 0.03, 0.04, 0.05):
        ledger, rejects = _run_ledger(frame, risk_pct=risk_pct)
        risk = ledger["risk_dollars"].to_numpy(float)
        rows.append(
            {
                "risk_pct": float(100 * risk_pct),
                "max_notional_multiple": MAX_NOTIONAL_MULTIPLE,
                **_metrics(ledger),
                "effective_risk_pct_p50": float(np.median(risk / DEPOSIT * 100)),
                "effective_risk_pct_p90": float(np.quantile(risk / DEPOSIT * 100, 0.90)),
                "rejects": rejects,
            }
        )
    return rows


def run(
    out_json: Path = OUT_JSON,
    out_md: Path = OUT_MD,
    *,
    out_trades_json: Path = OUT_TRADES_JSON,
    out_trades_csv: Path = OUT_TRADES_CSV,
    out_dashboard_json: Path = OUT_DASHBOARD_JSON,
) -> dict[str, Any]:
    raw_frame = _candidate_frame(with_scores=False)
    frame = _candidate_frame(with_scores=True)
    selected_frame = _selected_frame(frame)
    ledger, rejects = _run_ledger(selected_frame)
    raw_ledger, raw_rejects = _run_raw_ledger(raw_frame)
    trades = _enriched_trades(ledger, frame)
    months = _calendar_stats(ledger, "M")
    base = _metrics(ledger)
    raw_base = _metrics(raw_ledger)
    dashboard = {
        "slices": _dashboard_slices(trades),
        "trade_count": int(len(trades)),
    }
    shuffled_spearman = None
    shuffled_file = shuffled_score_path(CANDIDATE_POLICY)
    if shuffled_file.exists():
        shuffled = pd.read_parquet(shuffled_file)[TRADE_KEY + ["model_score_r"]]
        shuffled_eval = frame[TRADE_KEY + ["r_multiple"]].merge(shuffled, on=TRADE_KEY, validate="one_to_one")
        shuffled_spearman = float(shuffled_eval["r_multiple"].corr(shuffled_eval["model_score_r"], method="spearman"))
    report = {
        "scope": "IS selected portfolio; policy-specific weekly-forward CatBoost expected-R; causal prior-score q70",
        "policy": POLICY,
        "selection": {
            "model": "CatBoost expected-R",
            "protocol": "strict weekly walk-forward; one frozen model per calendar week",
            "threshold": f"q{int(SELECTION_QUANTILE * 100)} from prior forward-scored weeks only",
            "max_notional_multiple": MAX_NOTIONAL_MULTIPLE,
            "round_trip_fee_bps": float(COST_RATE * 10_000),
            "baseline_slippage_bps": 50,
        },
        "prediction_controls": {
            "real_score_spearman": float(frame["r_multiple"].corr(frame["model_score_r"], method="spearman")),
            "shuffled_train_target_score_spearman": shuffled_spearman,
        },
        "population": {
            "raw_detector_candidates": int(len(raw_frame)),
            "forward_scored_candidates": int(len(frame)),
            "selected_before_portfolio": int(len(selected_frame)),
            "portfolio_taken": int(len(ledger)),
            "portfolio_not_opened": int(sum(rejects.values())),
        },
        "combined_portfolio": {
            **base,
            "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
            "months": int(len(months)),
            "monthly_pnl": {row["period"]: row["net_pnl"] for row in months},
            "rejects": rejects,
        },
        "raw_detector_diagnostic_not_deployable": {
            **raw_base,
            "portfolio_candidates": int(len(raw_frame)),
            "portfolio_taken": int(len(raw_ledger)),
            "portfolio_not_opened": int(sum(raw_rejects.values())),
            "rejects": raw_rejects,
            "selection": "none",
        },
        "combined_family_contribution": _family_contribution(ledger, frame),
        "combined_by_family_tf": _group_metrics(ledger, frame, ["setup_family", "tf"]),
        "family_only_diagnostics_not_additive": _family_only(selected_frame),
        "risk_stress_combined": _risk_stress(selected_frame),
        "artifacts": {
            "trades_json": str(out_trades_json),
            "trades_csv": str(out_trades_csv),
            "dashboard_json": str(out_dashboard_json),
        },
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8")
    out_trades_json.parent.mkdir(parents=True, exist_ok=True)
    out_trades_json.write_text(
        json.dumps(_jsonable(trades.replace({np.nan: None}).to_dict("records")), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    trades.to_csv(out_trades_csv, index=False)
    out_dashboard_json.write_text(json.dumps(_jsonable(dashboard), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Triple-tap current trade report",
        "",
        "Scope: IS selected portfolio, policy-specific weekly-forward CatBoost expected-R, causal prior-score q70.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Raw detector candidates | {report['population']['raw_detector_candidates']} |",
        f"| Forward-scored candidates | {report['population']['forward_scored_candidates']} |",
        f"| Selected before portfolio | {report['population']['selected_before_portfolio']} |",
        f"| Taken trades | {base['trades']} |",
        f"| Not opened | {report['population']['portfolio_not_opened']} |",
        f"| Return | {base['return_pct']:+.1f}% |",
        f"| Max DD | {base['max_drawdown_pct']:.1f}% |",
        f"| Win rate | {base['win_rate_pct']:.1f}% |",
        f"| PF | {base['profit_factor']:.2f} |",
        "",
        "## Raw detector diagnostic, not deployable",
        "",
        f"Raw no-ML accounting: {raw_base['trades']} trades, return {raw_base['return_pct']:+.1f}%, "
        f"PF {raw_base['profit_factor']:.2f}.",
        "",
        "## Combined portfolio contribution by family",
        "",
        "| Family | Trades | Return | WR | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["combined_family_contribution"]:
        lines.append(
            f"| {row['setup_family']} | {row['trades']} | {row['return_pct']:+.1f}% | "
            f"{row['win_rate_pct']:.1f}% | {row['profit_factor']:.2f} |"
        )
    lines += [
        "",
        "Family-only diagnostics are not additive; see JSON.",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote trade report -> {out_json} and {out_md}", flush=True)
    print(f"wrote trade list -> {out_trades_json} and {out_trades_csv}", flush=True)
    return report


if __name__ == "__main__":
    run()
