"""Robustness-first trade-set metrics (single implementation).

These replace the copy-pasted ``toptoneg`` / ``EV_exBest`` / PF / equity
snippets that were duplicated across every ``tmp`` script. All operate on a
1-D array of per-trade NET returns (fraction), optionally with matching
per-trade R-multiples and calendar month/week labels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def top_removed_to_negative(net: np.ndarray) -> float:
    """Min %% of the top trades to delete before the mean EV turns <= 0.

    The core tail-dependence guard: a strategy whose positive EV survives only
    because of a handful of monster trades scores near 0 here and is fragile.
    """

    if len(net) == 0:
        return 0.0
    ordered = np.sort(net)[::-1]
    for k in range(len(ordered)):
        if ordered[k:].mean() <= 0:
            return k / len(ordered) * 100.0
    return 100.0


def profit_factor(r_multiples: np.ndarray) -> float:
    """Gross win R over gross loss R."""

    r = np.asarray(r_multiples, dtype=float)
    gains = r[r > 0].sum()
    losses = -r[r <= 0].sum()
    return float(gains / (losses + 1e-9))


def ev_excluding_best_month(net: np.ndarray, months: np.ndarray) -> float:
    """Mean EV after dropping the single strongest calendar month.

    Guards against a result carried by one explosive regime (e.g. W41/October).
    """

    if len(net) == 0:
        return 0.0
    net = np.asarray(net, dtype=float)
    months = np.asarray(months)
    by_month = pd.Series(net).groupby(pd.Series(months)).mean()
    best = by_month.idxmax()
    kept = net[months != best]
    return float(kept.mean()) if len(kept) else 0.0


def equity_curve(r_multiples: np.ndarray, *, start: float = 1000.0, risk: float = 0.03) -> np.ndarray:
    """Compounded equity risking ``risk`` of capital per trade at its R-multiple."""

    eq = start
    curve = np.empty(len(r_multiples))
    for i, rm in enumerate(np.asarray(r_multiples, dtype=float)):
        eq *= 1.0 + rm * risk
        curve[i] = eq
    return curve


def max_drawdown(curve: np.ndarray) -> float:
    """Deepest peak-to-trough fraction of an equity curve (0..1)."""

    if len(curve) == 0:
        return 0.0
    running_peak = np.maximum.accumulate(curve)
    return float((1.0 - curve / running_peak).max())


def positive_period_fraction(net: np.ndarray, period_labels: np.ndarray) -> float:
    """Fraction of calendar periods (week/month labels) that were net-positive."""

    if len(net) == 0:
        return 0.0
    by_period = pd.Series(np.asarray(net)).groupby(pd.Series(period_labels)).mean()
    return float((by_period > 0).mean())


@dataclass(frozen=True, slots=True)
class TradeSetSummary:
    n: int
    ev: float
    winrate: float
    median: float
    profit_factor: float
    top_removed_to_negative: float
    ev_excluding_best_month: float
    avg_r: float
    max_drawdown: float
    positive_weeks: float
    equity_end: float

    def line(self, label: str = "") -> str:
        return (
            f"{label:<26} n={self.n:4d} EV={self.ev*100:6.2f}% wr={self.winrate:.2f} "
            f"med={self.median*100:6.2f}% PF={self.profit_factor:5.2f} "
            f"top2neg={self.top_removed_to_negative:5.1f}% exBest={self.ev_excluding_best_month*100:6.2f}% "
            f"avgR={self.avg_r:5.2f} maxDD={self.max_drawdown*100:4.0f}% "
            f"posW={self.positive_weeks:.2f} $1k->{self.equity_end:.0f}"
        )


def summarize(
    net: np.ndarray,
    *,
    r_multiples: np.ndarray | None = None,
    months: np.ndarray | None = None,
    weeks: np.ndarray | None = None,
    risk: float = 0.03,
) -> TradeSetSummary:
    """One human-readable robustness summary of a trade set."""

    net = np.asarray(net, dtype=float)
    if len(net) == 0:
        return TradeSetSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0)
    r = np.asarray(r_multiples, dtype=float) if r_multiples is not None else net.copy()
    mo = np.asarray(months) if months is not None else np.zeros(len(net))
    wk = np.asarray(weeks) if weeks is not None else mo
    curve = equity_curve(r, risk=risk)
    return TradeSetSummary(
        n=len(net),
        ev=float(net.mean()),
        winrate=float((net > 0).mean()),
        median=float(np.median(net)),
        profit_factor=profit_factor(r),
        top_removed_to_negative=top_removed_to_negative(net),
        ev_excluding_best_month=ev_excluding_best_month(net, mo),
        avg_r=float(r.mean()),
        max_drawdown=max_drawdown(curve),
        positive_weeks=positive_period_fraction(net, wk),
        equity_end=float(curve[-1]),
    )


__all__ = [
    "TradeSetSummary",
    "equity_curve",
    "ev_excluding_best_month",
    "max_drawdown",
    "positive_period_fraction",
    "profit_factor",
    "summarize",
    "top_removed_to_negative",
]
