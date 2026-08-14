"""Acceptance gates (§6) and inference helpers (§5).

Thresholds mirror the preregistration. Every gate returns both the measured
value and a pass/fail, so the report is auditable rather than a single verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.backtest import RunResult


# preregistered thresholds (§6) — flexible but logged
GATES = {
    "G1_top40_drop_positive": True,
    "G2_positive_day_share_min": 0.70,
    "G3_best_day_share_max": 0.15,
    "G4_best_week_share_max": 0.25,
    "G5_best_month_share_max": 0.40,  # low power at 7 months
    "G6_win_rate_min": 0.50,
    "G7_max_drawdown_max": 0.15,
    "G8_worst_day_max": 0.08,  # black-swan: no single day worse than -8%
}


def tail_metrics(daily_ret: pd.Series) -> dict:
    """Black-swan / tail diagnostics on the daily return series."""
    r = daily_ret.dropna()
    if r.empty:
        return {"worst_day": np.nan, "worst_week": np.nan, "cvar_5": np.nan}
    worst_day = float(r.min())
    weekly = r.rolling(5).sum().dropna()
    worst_week = float(weekly.min()) if len(weekly) else np.nan
    q05 = r.quantile(0.05)
    cvar_5 = float(r[r <= q05].mean()) if (r <= q05).any() else float(r.min())
    return {"worst_day": worst_day, "worst_week": worst_week, "cvar_5": cvar_5}


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(-dd.min())


def sharpe(daily_ret: pd.Series, ann: int = 365) -> float:
    r = daily_ret.dropna()
    if r.std() == 0 or len(r) < 5:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(ann))


def _best_period_share(trades: pd.DataFrame, freq: str) -> float:
    if trades.empty:
        return np.nan
    rb = trades["rebalance_date"].dt.tz_localize(None)
    net = trades.assign(period=rb.dt.to_period(freq)).groupby("period")["pnl_net"].sum()
    total = net.sum()
    if total <= 0:
        return np.nan  # undefined when the strategy is not net-positive
    return float(net.max() / total)


def _best_day_share(daily_ret: pd.Series, equity: pd.Series) -> float:
    # PnL per day in currency terms
    if equity.empty:
        return np.nan
    prev = equity.shift(1).fillna(equity.iloc[0] / (1 + daily_ret.iloc[0]) if len(daily_ret) else 1.0)
    pnl = equity - prev
    total = pnl.sum()
    if total <= 0:
        return np.nan
    return float(pnl.max() / total)


def top_drop_positive(trades: pd.DataFrame, drop_frac: float = 0.40) -> tuple[bool, float]:
    """G1: remove the top `drop_frac` trades by net PnL; remaining sum > 0?"""
    if trades.empty:
        return False, 0.0
    s = trades["pnl_net"].sort_values(ascending=False)
    n_drop = int(np.ceil(len(s) * drop_frac))
    remaining = s.iloc[n_drop:].sum()
    return bool(remaining > 0), float(remaining)


def block_bootstrap_sharpe(daily_ret: pd.Series, block: int = 7, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """Median and 5th-percentile Sharpe from contiguous-block resampling (§5)."""
    r = daily_ret.dropna().to_numpy()
    if len(r) < block * 3:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(len(r) / block))
    starts_max = len(r) - block
    out = np.empty(n)
    for i in range(n):
        starts = rng.integers(0, starts_max + 1, size=n_blocks)
        sample = np.concatenate([r[s:s + block] for s in starts])[: len(r)]
        sd = sample.std()
        out[i] = sample.mean() / sd * np.sqrt(365) if sd > 0 else 0.0
    return float(np.median(out)), float(np.quantile(out, 0.05))


@dataclass
class GateReport:
    values: dict
    passes: dict
    all_pass: bool


def evaluate_gates(res: RunResult) -> GateReport:
    eq, dr, td = res.equity, res.daily_ret, res.trades
    # G2 counts only in-position (non-flat) days; flat cash/regime-off days are
    # not "trading days" and must not inflate the positive-day share.
    in_market = dr[dr != 0.0]
    pos_day_share = float((in_market >= 0).mean()) if len(in_market) else 0.0
    g1_pass, g1_rem = top_drop_positive(td, 0.40)
    best_day = _best_day_share(dr, eq)
    best_week = _best_period_share(td, "W")
    best_month = _best_period_share(td, "M")
    win_rate = float((td["pnl_net"] > 0).mean()) if len(td) else 0.0
    mdd = max_drawdown(eq)
    tails = tail_metrics(dr)

    start_equity = float(res.meta.get("start_equity", eq.iloc[0] / (1.0 + dr.iloc[0]))) if len(eq) else np.nan
    values = {
        "final_equity": float(eq.iloc[-1]) if len(eq) else np.nan,
        "total_return": float(eq.iloc[-1] / start_equity - 1.0) if len(eq) else np.nan,
        "sharpe": sharpe(dr),
        "n_trades": int(len(td)),
        "g1_remaining_after_top40_drop": g1_rem,
        "g2_positive_day_share": pos_day_share,
        "g3_best_day_share": best_day,
        "g4_best_week_share": best_week,
        "g5_best_month_share": best_month,
        "g6_win_rate": win_rate,
        "g7_max_drawdown": mdd,
        "g8_worst_day": tails["worst_day"],
        "worst_week": tails["worst_week"],
        "cvar_5": tails["cvar_5"],
    }
    passes = {
        "G1": g1_pass,
        "G2": pos_day_share >= GATES["G2_positive_day_share_min"],
        "G3": (best_day <= GATES["G3_best_day_share_max"]) if np.isfinite(best_day) else False,
        "G4": (best_week <= GATES["G4_best_week_share_max"]) if np.isfinite(best_week) else False,
        "G5": (best_month <= GATES["G5_best_month_share_max"]) if np.isfinite(best_month) else False,
        "G6": win_rate > GATES["G6_win_rate_min"],
        "G7": mdd <= GATES["G7_max_drawdown_max"],
        "G8": (tails["worst_day"] >= -GATES["G8_worst_day_max"]) if np.isfinite(tails["worst_day"]) else False,
    }
    return GateReport(values=values, passes=passes, all_pass=all(passes.values()))
