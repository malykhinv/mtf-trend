"""Fast stability screen for session edge watchlist rows."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.session_edge_workbench import _add_setup_buckets, _selector_defs


KEY_COLS = ["selector", "candidate_desc", "stop_model", "exit_model"]


def _build_watchlist_rows(base_dir: Path) -> pd.DataFrame:
    setups = pd.read_csv(base_dir / "large_runner_session_outcome_research_table.csv")
    setups = _add_setup_buckets(setups)
    replay = pd.read_csv(base_dir / "large_runner_local_high_structural_exit_replay_trades.csv")
    scorecard = pd.read_csv(base_dir / "large_runner_edge_workbench_replay_scorecard.csv")

    watchlist = scorecard[
        scorecard.get("watchlist_positive_but_top_dependent", False).astype(bool)
        | scorecard.get("passes_replay_robustness", False).astype(bool)
    ][KEY_COLS].drop_duplicates()

    join_cols = [
        "symbol",
        "seed_close_ms",
        "setup_key",
        "session_bucket",
        "close_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "edge_bucket",
        "outcome_class",
    ]
    joined = replay.merge(setups[join_cols], on=["symbol", "seed_close_ms"], how="inner", validate="many_to_one")
    joined = joined[joined["status"].eq("closed")].copy()
    joined["date"] = pd.to_datetime(joined["seed_close_utc"], errors="coerce").dt.normalize()
    joined["setup_uid"] = joined["symbol"].astype(str) + "|" + joined["seed_close_ms"].astype(str)
    joined["management"] = (
        joined["candidate_desc"].astype(str)
        + " | "
        + joined["stop_model"].astype(str)
        + " | "
        + joined["exit_model"].astype(str)
    )

    frames = []
    for selector, func in _selector_defs().items():
        part = joined[func(joined).fillna(False)].copy()
        if part.empty:
            continue
        part["selector"] = selector
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    rows = pd.concat(frames, ignore_index=True)
    if watchlist.empty:
        return rows
    return rows.merge(watchlist, on=KEY_COLS, how="inner")


def _top_remove(values: pd.Series) -> tuple[float, int, int]:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    total = float(vals.sum())
    winners = vals[vals > 0].sort_values(ascending=False).to_numpy()
    if len(winners) == 0 or total <= 0:
        return 0.0, 0, int(len(winners))
    remaining = total
    for idx, winner in enumerate(winners, start=1):
        remaining -= float(winner)
        if remaining <= 0:
            return float(idx / len(winners)), int(idx), int(len(winners))
    return 1.0, int(len(winners)), int(len(winners))


def _max_drawdown(vals: pd.Series) -> float:
    arr = pd.to_numeric(vals, errors="coerce").fillna(0.0).to_numpy()
    if len(arr) == 0:
        return np.nan
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def _rolling_stats(df: pd.DataFrame, days: int) -> dict[str, float]:
    daily = df.groupby("date")["net_r"].sum().sort_index()
    if daily.empty:
        return {
            f"roll{days}_windows": 0,
            f"roll{days}_min_sum_r": np.nan,
            f"roll{days}_median_sum_r": np.nan,
            f"roll{days}_positive_rate": np.nan,
        }
    full_idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full_idx, fill_value=0.0)
    roll = daily.rolling(days, min_periods=max(5, min(days, len(daily)) // 3)).sum().dropna()
    if roll.empty:
        return {
            f"roll{days}_windows": 0,
            f"roll{days}_min_sum_r": np.nan,
            f"roll{days}_median_sum_r": np.nan,
            f"roll{days}_positive_rate": np.nan,
        }
    return {
        f"roll{days}_windows": int(len(roll)),
        f"roll{days}_min_sum_r": float(roll.min()),
        f"roll{days}_median_sum_r": float(roll.median()),
        f"roll{days}_positive_rate": float((roll > 0).mean()),
    }


def _summarize(df: pd.DataFrame) -> pd.Series:
    vals = pd.to_numeric(df["net_r"], errors="coerce").dropna()
    top_trade_pct, removed_trades, winning_trades = _top_remove(vals)
    symbol_r = df.groupby("symbol")["net_r"].sum()
    top_symbol_pct, removed_symbols, winning_symbols = _top_remove(symbol_r)
    daily = df.groupby("date")["net_r"].sum()
    monthly = df.groupby("month")["net_r"].sum()
    session_sum = df.groupby("session_bucket")["net_r"].sum()
    out = {
        "trades": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "days": int(df["date"].nunique()),
        "sessions": int(df["session_bucket"].nunique()),
        "avg_r": float(vals.mean()) if len(vals) else np.nan,
        "median_r": float(vals.median()) if len(vals) else np.nan,
        "sum_r": float(vals.sum()) if len(vals) else np.nan,
        "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
        "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
        "positive_month_rate": float((monthly > 0).mean()) if len(monthly) else np.nan,
        "positive_session_rate": float((session_sum > 0).mean()) if len(session_sum) else np.nan,
        "top_trade_independence_pct": top_trade_pct,
        "removed_top_trades_to_negative": removed_trades,
        "winning_trades": winning_trades,
        "top_symbol_independence_pct": top_symbol_pct,
        "removed_top_symbols_to_negative": removed_symbols,
        "winning_symbols": winning_symbols,
        "max_drawdown_r": _max_drawdown(df.sort_values("seed_close_ms")["net_r"]),
        "fader_rate": float(df["outcome_class"].eq("fast_base_fader").mean()),
        "runner_rate": float(df["outcome_class"].eq("runner10").mean()),
        "static_or_other_rate": float(
            df["outcome_class"].isin(["upper_static", "other_no_direction", "late_base_fader"]).mean()
        ),
        "median_initial_risk_pct": float(df["initial_risk_pct"].median()),
    }
    out.update(_rolling_stats(df, 30))
    out.update(_rolling_stats(df, 60))
    return pd.Series(out)


def _score(row: pd.Series) -> float:
    avg = np.tanh(float(row["avg_r"]) / 0.30)
    med = np.tanh(float(row["median_r"]) / 0.30)
    trade_ind = min(float(row["top_trade_independence_pct"]), 0.50) / 0.50
    sym_ind = min(float(row["top_symbol_independence_pct"]), 0.50) / 0.50
    day = float(row["positive_day_rate"])
    month = float(row["positive_month_rate"])
    roll30 = max(min(float(row["roll30_positive_rate"]), 1.0), 0.0)
    sample = min(float(row["trades"]) / 150.0, 1.0)
    dd_penalty = min(abs(float(row["max_drawdown_r"])) / 25.0, 1.0)
    return float(
        0.16 * avg
        + 0.18 * med
        + 0.18 * trade_ind
        + 0.14 * sym_ind
        + 0.10 * day
        + 0.10 * month
        + 0.08 * roll30
        + 0.06 * sample
        - 0.12 * dd_penalty
    )


def build(base_dir: Path) -> None:
    rows_path = base_dir / "large_runner_edge_workbench_selector_trade_rows.csv"
    if not rows_path.exists():
        df = _build_watchlist_rows(base_dir)
        df.to_csv(rows_path, index=False)
    else:
        df = pd.read_csv(rows_path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["month"] = pd.to_datetime(df["seed_close_utc"], errors="coerce").dt.to_period("M").astype(str)

    summaries = []
    session_rows = []
    for keys, group in df.groupby(KEY_COLS, dropna=False):
        summary = _summarize(group)
        for col, value in zip(KEY_COLS, keys):
            summary[col] = value
        summary["management"] = " | ".join(map(str, keys[1:]))
        summaries.append(summary)

        for session, session_group in group.groupby("session_bucket", dropna=False):
            s = _summarize(session_group)
            for col, value in zip(KEY_COLS, keys):
                s[col] = value
            s["session_bucket"] = session
            session_rows.append(s)

    out = pd.DataFrame(summaries)
    out["balance_score"] = out.apply(_score, axis=1)
    out["strict_candidate"] = (
        (out["trades"] >= 100)
        & (out["symbols"] >= 50)
        & (out["days"] >= 50)
        & (out["avg_r"] > 0)
        & (out["median_r"] > 0)
        & (out["top_trade_independence_pct"] >= 0.50)
        & (out["top_symbol_independence_pct"] >= 0.50)
    )
    out["balanced_watchlist"] = (
        (out["trades"] >= 50)
        & (out["symbols"] >= 40)
        & (out["days"] >= 40)
        & (out["avg_r"] > 0)
        & (out["top_trade_independence_pct"] >= 0.08)
        & (out["top_symbol_independence_pct"] >= 0.15)
        & (out["positive_day_rate"] >= 0.35)
    )
    out = out.sort_values(["strict_candidate", "balance_score", "sum_r"], ascending=[False, False, False])

    session_out = pd.DataFrame(session_rows).sort_values(["sum_r"], ascending=False)
    out_path = base_dir / "large_runner_edge_workbench_balance_screen.csv"
    session_path = base_dir / "large_runner_edge_workbench_balance_by_session.csv"
    out.to_csv(out_path, index=False)
    session_out.to_csv(session_path, index=False)

    top = out.head(15)
    report = f"""# Balance Screen

This is a fast stability screen over the current positive full-period watchlist.
It is not a final non-leaky rolling selector, but it identifies which pockets
have the best balance between PnL, sample, sessions, rolling stability and
top-dependence.

Rows screened:

```text
{len(out)}
```

Strict candidates:

```text
{int(out['strict_candidate'].sum())}
```

Balanced watchlist rows:

```text
{int(out['balanced_watchlist'].sum())}
```

Top rows:

```text
{top[[
    'selector',
    'management',
    'trades',
    'symbols',
    'days',
    'sessions',
    'avg_r',
    'median_r',
    'sum_r',
    'win_rate',
    'positive_day_rate',
    'positive_month_rate',
    'top_trade_independence_pct',
    'top_symbol_independence_pct',
    'roll30_min_sum_r',
    'roll30_positive_rate',
    'balance_score',
    'strict_candidate',
    'balanced_watchlist',
]].to_string(index=False)}
```
"""
    (base_dir / "large_runner_edge_workbench_balance_screen.md").write_text(report, encoding="utf-8")

    print(f"balance_rows={len(out)}")
    print(f"strict_candidates={int(out['strict_candidate'].sum())}")
    print(f"balanced_watchlist={int(out['balanced_watchlist'].sum())}")
    print(f"wrote={out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".output/results/large_runner_discovery_365d")
    args = parser.parse_args()
    build(Path(args.base_dir))


if __name__ == "__main__":
    main()
