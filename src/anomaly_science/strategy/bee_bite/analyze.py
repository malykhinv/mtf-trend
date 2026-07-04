"""Cheap in-sample analysis of the build-once bee-bite outcome artifact."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_long.research.metrics import summarize
from anomaly_science.strategy.pump_long.research.metrics import dissect_wins_losses
from anomaly_science.strategy.bee_bite.spring import CAUSAL_METRICS

DEFAULT_OUTCOMES = Path(".output/results/bee_bite_v1/spring_outcomes.parquet")
NET_COL = "net_wick_perehai"
HOLDING_COL = "holding_min_wick_perehai"


def equal_weight_symbol_ev(frame: pd.DataFrame, net_col: str = NET_COL) -> float:
    """Mean of per-symbol mean returns, giving every symbol equal weight."""
    if frame.empty:
        return 0.0
    return float(frame.groupby("symbol", observed=True)[net_col].mean().mean())


def top_symbol_concentration(
    frame: pd.DataFrame, net_col: str = NET_COL, *, count: int = 5
) -> float:
    """Top-symbol PnL divided by total PnL; values above 1 imply a losing rest."""
    if frame.empty:
        return np.nan
    pnl = frame.groupby("symbol", observed=True)[net_col].sum().sort_values(ascending=False)
    total = float(pnl.sum())
    return float(pnl.head(count).sum() / total) if total > 0 else np.nan


def describe_slice(label: str, frame: pd.DataFrame, net_col: str = NET_COL) -> str:
    valid = frame.loc[frame[net_col].notna()]
    summary = summarize(
        valid[net_col].to_numpy(),
        months=valid["mo"].to_numpy(),
        weeks=valid["week"].to_numpy(),
    )
    concentration = top_symbol_concentration(valid, net_col)
    concentration_text = f"{concentration * 100:.0f}%" if np.isfinite(concentration) else "n/a"
    monthly = valid.groupby("mo", observed=True)[net_col].mean()
    positive_months = int((monthly > 0).sum())
    return (
        summary.line(label)
        + f" eqSym={equal_weight_symbol_ev(valid, net_col) * 100:+.2f}%"
        + f" top5sym={concentration_text}"
        + f" posM={positive_months}/{len(monthly)}"
    )


def trader_metrics(frame: pd.DataFrame, net_col: str = NET_COL) -> dict[str, object]:
    """Metrics required for evaluating whether an IS edge is tradable."""
    valid = frame.loc[frame[net_col].notna()].copy()
    if valid.empty:
        return {}
    monthly_counts = valid.groupby("mo", observed=True).size()
    daily = valid.groupby("entry_date_utc", observed=True)[net_col].agg(["size", "sum"])
    wins = valid.loc[valid[net_col] > 0, HOLDING_COL]
    losses = valid.loc[valid[net_col] <= 0, HOLDING_COL]
    win_returns = valid.loc[valid[net_col] > 0, net_col]
    loss_returns = valid.loc[valid[net_col] <= 0, net_col]
    worst = valid.loc[valid[net_col].idxmin()]
    best = valid.loc[valid[net_col].idxmax()]
    ordered_wins = (valid.sort_values("entry_time_ms")[net_col] > 0).to_numpy()
    max_win_streak = 0
    max_loss_streak = 0
    win_streak = 0
    loss_streak = 0
    for won in ordered_wins:
        win_streak = win_streak + 1 if won else 0
        loss_streak = 0 if won else loss_streak + 1
        max_win_streak = max(max_win_streak, win_streak)
        max_loss_streak = max(max_loss_streak, loss_streak)
    return {
        "n": len(valid),
        "monthly_min": int(monthly_counts.min()),
        "monthly_max": int(monthly_counts.max()),
        "worst_return": float(worst[net_col]),
        "worst_symbol": str(worst["symbol"]),
        "worst_entry": str(worst["entry_date_utc"]),
        "best_return": float(best[net_col]),
        "best_symbol": str(best["symbol"]),
        "best_entry": str(best["entry_date_utc"]),
        "win_holding_median": float(wins.median()),
        "win_holding_p90": float(wins.quantile(0.90)),
        "loss_holding_median": float(losses.median()),
        "loss_holding_p90": float(losses.quantile(0.90)),
        "positive_days": int((daily["sum"] > 0).sum()),
        "trading_days": len(daily),
        "trades_per_day_mean": float(daily["size"].mean()),
        "trades_per_day_max": int(daily["size"].max()),
        "average_win": float(win_returns.mean()),
        "average_loss": float(loss_returns.mean()),
        "payoff_ratio": float(win_returns.mean() / -loss_returns.mean()),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "worst_day": float(daily["sum"].min()),
        "best_day": float(daily["sum"].max()),
    }


def print_trader_report(label: str, frame: pd.DataFrame) -> None:
    print(describe_slice(label, frame), flush=True)
    metrics = trader_metrics(frame)
    if not metrics:
        return
    print(
        "  trades/month="
        f"{metrics['monthly_min']}..{metrics['monthly_max']} "
        f"worst={metrics['worst_return'] * 100:+.2f}% "
        f"({metrics['worst_symbol']} {metrics['worst_entry']}) "
        f"best={metrics['best_return'] * 100:+.2f}% "
        f"({metrics['best_symbol']} {metrics['best_entry']})",
        flush=True,
    )
    print(
        f"  avg win/loss={metrics['average_win'] * 100:+.2f}%/"
        f"{metrics['average_loss'] * 100:+.2f}% "
        f"payoff={metrics['payoff_ratio']:.2f} "
        f"max win/loss streak={metrics['max_win_streak']}/{metrics['max_loss_streak']} "
        f"best/worst day={metrics['best_day'] * 100:+.2f}%/"
        f"{metrics['worst_day'] * 100:+.2f}%",
        flush=True,
    )
    print(
        "  holding win median/p90="
        f"{metrics['win_holding_median']:.0f}/{metrics['win_holding_p90']:.0f}m "
        "loss median/p90="
        f"{metrics['loss_holding_median']:.0f}/{metrics['loss_holding_p90']:.0f}m "
        f"positive days={metrics['positive_days']}/{metrics['trading_days']} "
        f"trades/day mean/max={metrics['trades_per_day_mean']:.2f}/{metrics['trades_per_day_max']}",
        flush=True,
    )
def _quantile_slices(frame: pd.DataFrame, feature: str) -> list[tuple[str, pd.DataFrame]]:
    finite = frame.loc[np.isfinite(frame[feature])].copy()
    if finite.empty or finite[feature].nunique() < 4:
        return []
    finite["_bin"] = pd.qcut(finite[feature], 4, duplicates="drop")
    return [
        (f"{feature} Q{i}", group.drop(columns="_bin"))
        for i, (_, group) in enumerate(finite.groupby("_bin", observed=True), start=1)
    ]


def report_is(frame: pd.DataFrame) -> None:
    """Print descriptive IS slices only; no split, fitting, or OOS access."""
    print("=== BEE-BITE IS ONLY: structural wick stop -> perehai ===", flush=True)
    print_trader_report("all", frame)

    big = frame.loc[(frame["pump_size"] >= 0.20) & (frame["held_ratio"] >= 0.70)]
    print_trader_report("pump>=20 held>=.70", big)

    print("\n=== registered geometry profile ===", flush=True)
    regime = (frame["pump_size"] >= 0.15) & (frame["held_ratio"] >= 0.70)
    registered = (
        ("pump>=15 held>=.70", regime),
        ("+ rising lows", regime & (frame["lows_slope"] > 0)),
        ("+ descending corridor", regime & (frame["lows_slope"] < 0) & (frame["highs_slope"] < 0)),
        ("+ compression<1", regime & (frame["range_compression"] < 1)),
        ("+ support tests<=2", regime & (frame["support_test_count"] <= 2)),
    )
    for label, mask in registered:
        print_trader_report(label, frame.loc[mask])

    daily_frequency = frame.groupby("entry_date_utc", observed=True).size().mean()
    if daily_frequency > 5:
        print("\n=== causal win/loss differences: frequency >5 trades/day ===", flush=True)
        dissect_wins_losses(frame, NET_COL, CAUSAL_METRICS)

    print("\n=== UTC entry sessions ===", flush=True)
    sessions = {
        "UTC 00-07": frame["entry_hour_utc"].between(0, 7),
        "UTC 08-15": frame["entry_hour_utc"].between(8, 15),
        "UTC 16-23": frame["entry_hour_utc"].between(16, 23),
    }
    for label, mask in sessions.items():
        print(describe_slice(label, frame.loc[mask]), flush=True)

    print("\n=== candidate features: quartiles low -> high ===", flush=True)
    features = (
        "avg_trade_size_sweep_ratio",
        "avg_trade_size_reclaim_ratio",
        "range_compression",
        "support_test_count",
        "last_pivot_low_delta",
        "reclaim_range_ratio",
        "upper_test_count",
        "lower_test_count",
        "upper_pivot_dispersion",
        "lower_pivot_dispersion",
        "swing_interval_mean",
        "swing_interval_cv",
        "swing_alternation_ratio",
        "slope_convergence",
        "range_mid_crossings",
        "range_close_dispersion",
        "pump_duration",
        "pump_path_efficiency",
        "pump_green_fraction",
        "pump_max_drawdown_frac",
        "pump_back_half_return_share",
        "pump_back_half_volume_ratio",
        "pump_largest_bar_share",
        "pump_wick_fraction",
        "pullback_wick_frac",
        "pullback_close_frac",
        "range_low_location",
    )
    for feature in features:
        print(f"\n-- {feature} --", flush=True)
        for label, group in _quantile_slices(frame, feature):
            print(describe_slice(label, group), flush=True)

    print("\n=== range geometry ===", flush=True)
    descending = (frame["highs_slope"] < 0) & (frame["lows_slope"] < 0)
    ascending = (frame["highs_slope"] > 0) & (frame["lows_slope"] > 0)
    converging = (frame["highs_slope"] < 0) & (frame["lows_slope"] > 0)
    broadening = (frame["highs_slope"] > 0) & (frame["lows_slope"] < 0)
    for label, mask in (
        ("descending", descending),
        ("ascending", ascending),
        ("converging triangle", converging),
        ("broadening", broadening),
        ("flat/zero slope", ~(descending | ascending | converging | broadening)),
    ):
        print(describe_slice(label, frame.loc[mask]), flush=True)

    print("\n=== structural perehai label: causal feature differences ===", flush=True)
    dissect_wins_losses(frame, "new_high", CAUSAL_METRICS)


def main() -> None:
    report_is(pd.read_parquet(DEFAULT_OUTCOMES))


if __name__ == "__main__":
    main()
