"""Policy-specific strict weekly-forward expected-R models."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.audit import (
    _calendar_stats,
    _causal_quantile_selection,
    _ledger,
    _metrics,
)
from anomaly_science.strategy.triple_tap.research.economics import COST_RATE, RES
from anomaly_science.strategy.triple_tap.research.ev_model import PARAMS
from anomaly_science.strategy.triple_tap.research.model import (
    CAT_FEATURES,
    FEATURES,
    ID_COLS,
    VOL_LEAKY,
)

POLICIES = (
    "structure_0.50_full",
    "structure_0.75_full",
    "structure_0.50_part_0.50_runner_initial_stop",
    "structure_0.50_part_0.50_runner_initial_stop_be_0.50_inactive_6h",
)
CANDIDATE_POLICY = "structure_0.50_part_0.50_runner_initial_stop_be_0.50_inactive_6h"
COMPARISON_OUT = Path("research/triple_tap_policy_specific_ev.json")


def score_path(policy: str) -> Path:
    return RES / f"ev_walkforward_{policy}.parquet"


def shuffled_score_path(policy: str) -> Path:
    return RES / f"ev_walkforward_{policy}_shuffled.parquet"


def run_policy_walk_forward(
    policy: str,
    *,
    min_train_weeks: int = 10,
    shuffle_train_target: bool = False,
) -> pd.DataFrame:
    if policy not in POLICIES:
        raise ValueError(f"unregistered policy {policy!r}; expected one of {POLICIES}")
    features = pd.read_parquet(FEATURES)
    labels = pd.read_parquet(RES / "execution_policies.parquet")
    labels = labels[
        (labels["entry_mode"] == "close")
        & (labels["exit_policy"] == policy)
        & labels["r_multiple"].notna()
    ][TRADE_KEY + ["r_multiple", "outcome_time_ms"]]
    frame = features.merge(labels, on=TRADE_KEY, validate="one_to_one")
    X = frame.drop(
        columns=ID_COLS + ["r_multiple", "outcome_time_ms"] + VOL_LEAKY,
        errors="ignore",
    )
    for col in CAT_FEATURES:
        X[col] = X[col].astype(str)
    cat_idx = [X.columns.get_loc(col) for col in CAT_FEATURES if col in X.columns]
    stamps = pd.to_datetime(frame["entry_time_ms"], unit="ms", utc=True)
    week_start = stamps.dt.floor("D") - pd.to_timedelta(stamps.dt.dayofweek, unit="D")
    weeks = sorted(week_start.unique())
    predictions: list[pd.DataFrame] = []
    for week_no, test_start in enumerate(weeks):
        if week_no < min_train_weeks:
            continue
        freeze_ms = int(pd.Timestamp(test_start).timestamp() * 1000)
        train = frame["outcome_time_ms"].astype("int64") <= freeze_ms
        test = week_start == test_start
        if not train.any() or not test.any():
            continue
        model = CatBoostRegressor(**PARAMS)
        train_target = frame.loc[train, "r_multiple"].to_numpy(copy=True)
        if shuffle_train_target:
            train_target = np.random.default_rng(20_000 + week_no).permutation(train_target)
        model.fit(
            Pool(X.loc[train], train_target, cat_features=cat_idx),
            verbose=False,
        )
        idx = frame.index[test]
        part = frame.loc[idx, TRADE_KEY + ["r_multiple"]].copy()
        part["model_score_r"] = model.predict(X.loc[idx])
        part["model_week"] = pd.Timestamp(test_start).strftime("%G-W%V")
        part["model_freeze_time_ms"] = freeze_ms
        part["train_n"] = int(train.sum())
        part["train_max_target_resolution_time_ms"] = int(
            frame.loc[train, "outcome_time_ms"].max()
        )
        predictions.append(part)
        print(
            f"  {policy}{' SHUFFLED' if shuffle_train_target else ''} "
            f"week {part['model_week'].iloc[0]} train={int(train.sum())} "
            f"test={int(test.sum())}",
            flush=True,
        )
    if not predictions:
        raise ValueError(f"policy walk-forward produced no supported weeks for {policy}")
    result = pd.concat(predictions, ignore_index=True)
    spearman = float(result["r_multiple"].corr(result["model_score_r"], method="spearman"))
    output_path = shuffled_score_path(policy) if shuffle_train_target else score_path(policy)
    result.drop(columns=["r_multiple"]).to_parquet(output_path, index=False)
    print(
        f"{policy}{' SHUFFLED' if shuffle_train_target else ''}: "
        f"rows={len(result)} weeks={result['model_week'].nunique()} "
        f"Spearman={spearman:.3f} -> {output_path}",
        flush=True,
    )
    return result


def run_candidate_policy_controls() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score the frozen candidate and its shuffled-target negative control only."""
    real = run_policy_walk_forward(CANDIDATE_POLICY)
    shuffled = run_policy_walk_forward(CANDIDATE_POLICY, shuffle_train_target=True)
    return real, shuffled


def _variant(
    frame: pd.DataFrame,
    selected: pd.Series,
    *,
    selection: str,
    max_notional_multiple: float | None = None,
) -> dict:
    ledger, rejects = _ledger(
        frame.loc[selected], risk_pct=0.02, max_open=3, slip_pct=0.005,
        max_notional_multiple=max_notional_multiple,
    )
    metrics = _metrics(ledger)
    months = _calendar_stats(ledger, "M") if not ledger.empty else []
    positive_months = sum(row["net_pnl"] > 0 for row in months)
    return {
        "selection": selection,
        "max_notional_multiple": max_notional_multiple,
        **metrics,
        "positive_day_pct": (
            100 * metrics["positive_days"] / metrics["trading_days"]
            if metrics.get("trading_days")
            else 0.0
        ),
        "positive_months": positive_months,
        "months": len(months),
        "all_months_positive": bool(len(months) == 5 and positive_months == 5),
        "monthly_net_r": {row["period"]: row["net_r"] for row in months},
        "monthly_pnl": {row["period"]: row["net_pnl"] for row in months},
        "monthly_net_r_min": min((row["net_r"] for row in months), default=None),
        "monthly_pnl_min": min((row["net_pnl"] for row in months), default=None),
        "rejects": rejects,
    }


def evaluate_policy_models(out_path: Path = COMPARISON_OUT) -> dict:
    policy_labels = pd.read_parquet(RES / "execution_policies.parquet")
    results: dict[str, dict] = {}
    for policy in POLICIES:
        labels = policy_labels[
            (policy_labels["entry_mode"] == "close")
            & (policy_labels["exit_policy"] == policy)
            & policy_labels["r_multiple"].notna()
        ]
        scores = pd.read_parquet(score_path(policy))
        if scores.duplicated(TRADE_KEY).any():
            raise ValueError(f"duplicate policy score identity for {policy}")
        if not (
            scores["train_max_target_resolution_time_ms"]
            <= scores["model_freeze_time_ms"]
        ).all():
            raise ValueError(f"training target crosses weekly freeze for {policy}")
        if not (scores["model_freeze_time_ms"] <= scores["entry_time_ms"]).all():
            raise ValueError(f"model was not frozen before signal for {policy}")
        frame = labels.merge(scores, on=TRADE_KEY, validate="one_to_one")
        frame["predicted_net_r"] = frame["model_score_r"] - (
            COST_RATE + 0.005
        ) / frame["dist_stop"]
        variants = []
        for margin in (0.0, 0.25, 0.50, 0.75):
            variants.append(
                _variant(
                    frame,
                    frame["predicted_net_r"] > margin,
                    selection=f"predicted_net_r>{margin:.2f}",
                )
            )
        for quantile in (0.65, 0.70, 0.75, 0.80, 0.85):
            selected = _causal_quantile_selection(frame, quantile)
            for max_notional in (None, 0.25, 0.50, 1.0, 2.0, 3.0):
                variants.append(
                    _variant(
                        frame,
                        selected,
                        selection=f"prior_score_quantile={quantile:.2f}",
                        max_notional_multiple=max_notional,
                    )
                )
        variants.sort(
            key=lambda row: (
                row["positive_months"],
                row["monthly_pnl_min"] if row["monthly_pnl_min"] is not None else -1e9,
                row.get("top_winners_to_zero_pct_of_trades", 0),
                row.get("profit_factor") or 0,
            ),
            reverse=True,
        )
        merged = frame[["r_multiple", "model_score_r"]]
        shuffled_spearman = None
        if shuffled_score_path(policy).exists():
            shuffled_scores = pd.read_parquet(shuffled_score_path(policy))
            shuffled_eval = labels[TRADE_KEY + ["r_multiple"]].merge(
                shuffled_scores[TRADE_KEY + ["model_score_r"]],
                on=TRADE_KEY,
                validate="one_to_one",
            )
            shuffled_spearman = float(
                shuffled_eval["r_multiple"].corr(
                    shuffled_eval["model_score_r"], method="spearman"
                )
            )
        results[policy] = {
            "time_checks": {
                "unique_trade_key": True,
                "training_targets_resolved_by_freeze": True,
                "model_frozen_before_signal": True,
            },
            "forward_spearman": float(
                merged["r_multiple"].corr(merged["model_score_r"], method="spearman")
            ),
            "shuffled_forward_spearman": shuffled_spearman,
            "variants": variants,
        }
    candidate_policy = CANDIDATE_POLICY
    candidate_labels = policy_labels[
        (policy_labels["entry_mode"] == "close")
        & (policy_labels["exit_policy"] == candidate_policy)
        & policy_labels["r_multiple"].notna()
    ]
    candidate_scores = pd.read_parquet(score_path(candidate_policy))
    candidate_frame = candidate_labels.merge(
        candidate_scores, on=TRADE_KEY, validate="one_to_one"
    )
    candidate_selected = _causal_quantile_selection(candidate_frame, 0.70)
    cost_stress = []
    for slip_bps in (25, 50, 75, 100, 150):
        ledger, rejects = _ledger(
            candidate_frame.loc[candidate_selected],
            risk_pct=0.02,
            max_open=3,
            slip_pct=slip_bps / 10_000,
            max_notional_multiple=0.50,
        )
        metrics = _metrics(ledger)
        months = _calendar_stats(ledger, "M")
        cost_stress.append(
            {
                "slippage_bps": slip_bps,
                **metrics,
                "positive_day_pct": 100 * metrics["positive_days"] / metrics["trading_days"],
                "positive_months": sum(row["net_pnl"] > 0 for row in months),
                "monthly_pnl": {row["period"]: row["net_pnl"] for row in months},
                "rejects": rejects,
            }
        )
    risk_stress = []
    for risk_pct in (0.01, 0.02, 0.03):
        ledger, rejects = _ledger(
            candidate_frame.loc[candidate_selected],
            risk_pct=risk_pct,
            max_open=3,
            slip_pct=0.005,
            max_notional_multiple=0.50,
        )
        risk_stress.append(
            {"risk_pct": 100 * risk_pct, **_metrics(ledger), "rejects": rejects}
        )

    report = {
        "scope": "IS; policy-specific strict weekly-forward; policy family chosen post-hoc",
        "policies": results,
        "candidate": {
            "policy": candidate_policy,
            "entry": "confirmed_close",
            "selection": "causal prior-forward-score q70",
            "max_open": 3,
            "one_position_per_symbol": True,
            "max_notional_multiple": 0.50,
            "cost_stress": cost_stress,
            "risk_stress": risk_stress,
        },
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote policy-specific comparison -> {out_path}", flush=True)
    return report


if __name__ == "__main__":
    for registered_policy in POLICIES:
        run_policy_walk_forward(registered_policy)
    evaluate_policy_models()
