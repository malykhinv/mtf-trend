"""Strategy-neutral portfolio exposure and EV diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ArmSummary:
    arm: str
    stop_variant: str
    trades: int
    ev_net_return: float
    ev_ci_lower_95: float
    ev_ci_upper_95: float
    median_net_return: float
    mean_net_r: float
    ev_without_top_1pct: float
    ev_without_top_5pct: float
    positive_week_share: float
    positive_month_share: float
    max_drawdown_return_sum: float
    max_drawdown_weeks: float
    ev_at_fee_10bps_rt: float
    ev_at_fee_30bps_rt: float


def apply_exposure_rule(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Keep at most one concurrent position per recurrence chain and variant."""
    kept_masks: list[pd.Index] = []
    for _, variant_frame in outcomes.groupby("stop_variant", sort=False):
        ordered = variant_frame.sort_values(["entry_time_ms", "event_id"])
        open_until: dict[str, int] = {}
        kept_index = []
        for row in ordered.itertuples():
            chain = str(row.recurrence_chain_id)
            entry_ms = int(row.entry_time_ms)
            if open_until.get(chain, -1) > entry_ms:
                continue
            open_until[chain] = int(row.exit_time_ms)
            kept_index.append(row.Index)
        kept_masks.append(pd.Index(kept_index))
    if not kept_masks:
        return outcomes.iloc[0:0]
    keep = kept_masks[0]
    for other in kept_masks[1:]:
        keep = keep.union(other)
    return outcomes.loc[keep]


def summarize_arm(
    trades: pd.DataFrame,
    *,
    arm: str,
    stop_variant: str,
    bootstrap_iterations: int = 1000,
    random_seed: int = 20260702,
) -> ArmSummary:
    net = trades["net_return"].to_numpy(dtype=float)
    order = np.argsort(trades["exit_time_ms"].to_numpy(dtype=np.int64))
    equity = np.cumsum(net[order])
    running_peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))[1:]
    drawdown = running_peak - equity
    weeks = pd.to_datetime(trades["entry_time_ms"], unit="ms", utc=True).dt.strftime("%G-W%V")
    months = pd.to_datetime(trades["entry_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    weekly = trades.assign(week=weeks.values).groupby("week")["net_return"].sum()
    monthly = trades.assign(month=months.values).groupby("month")["net_return"].sum()
    ci_lower, ci_upper = _week_cluster_bootstrap_ci(
        net_return=net,
        weeks=weeks.to_numpy(),
        iterations=bootstrap_iterations,
        random_seed=random_seed,
    )
    fee_stress = {
        bps: float(np.mean(_net_at_round_trip_fee(trades, round_trip_bps=bps)))
        for bps in (10.0, 30.0)
    }
    return ArmSummary(
        arm=arm,
        stop_variant=stop_variant,
        trades=len(trades),
        ev_net_return=float(np.mean(net)),
        ev_ci_lower_95=ci_lower,
        ev_ci_upper_95=ci_upper,
        median_net_return=float(np.median(net)),
        mean_net_r=float(np.mean(trades["net_r"].to_numpy(dtype=float))),
        ev_without_top_1pct=_ev_without_top(net, 0.01),
        ev_without_top_5pct=_ev_without_top(net, 0.05),
        positive_week_share=float((weekly > 0).mean()),
        positive_month_share=float((monthly > 0).mean()),
        max_drawdown_return_sum=float(np.max(drawdown)) if len(drawdown) else 0.0,
        max_drawdown_weeks=_max_drawdown_duration_weeks(equity, trades, order),
        ev_at_fee_10bps_rt=fee_stress[10.0],
        ev_at_fee_30bps_rt=fee_stress[30.0],
    )


def _ev_without_top(net: np.ndarray, fraction: float) -> float:
    if len(net) == 0:
        return float("nan")
    remove = max(1, int(np.ceil(len(net) * fraction)))
    trimmed = np.sort(net)[:-remove]
    return float(np.mean(trimmed)) if len(trimmed) else float("nan")


def _net_at_round_trip_fee(trades: pd.DataFrame, *, round_trip_bps: float) -> np.ndarray:
    fee_rate = round_trip_bps / 2.0 / 10_000.0
    entry = trades["entry_price"].to_numpy(dtype=float)
    exit_ = trades["exit_price"].to_numpy(dtype=float)
    gross = trades["gross_return"].to_numpy(dtype=float)
    return gross - (entry + exit_) * fee_rate / entry


def _week_cluster_bootstrap_ci(
    *, net_return: np.ndarray, weeks: np.ndarray, iterations: int, random_seed: int
) -> tuple[float, float]:
    if len(net_return) == 0:
        return float("nan"), float("nan")
    unique = np.unique(weeks)
    by_week = {week: net_return[weeks == week] for week in unique}
    rng = np.random.default_rng(random_seed)
    samples = np.empty(iterations)
    for index in range(iterations):
        selected = rng.choice(unique, size=len(unique), replace=True)
        samples[index] = np.mean(np.concatenate([by_week[item] for item in selected]))
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def _max_drawdown_duration_weeks(equity: np.ndarray, trades: pd.DataFrame, order: np.ndarray) -> float:
    if not len(equity):
        return 0.0
    longest_ms = 0
    times = trades["exit_time_ms"].to_numpy(dtype=np.int64)[order]
    running_peak = float("-inf")
    peak_time = int(times[0])
    for index, value in enumerate(equity):
        if value >= running_peak:
            running_peak = float(value)
            peak_time = int(times[index])
        else:
            longest_ms = max(longest_ms, int(times[index]) - peak_time)
    return longest_ms / (7 * 24 * 60 * 60 * 1000)


__all__ = ["ArmSummary", "apply_exposure_rule", "summarize_arm"]
