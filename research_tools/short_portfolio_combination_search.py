"""Search short strategy portfolios with daily trade-frequency constraints."""

from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.session_edge_workbench import _top_remove_pct_to_negative
from research_tools.short_enhancement_strategy_screen import (
    _filter_defs_local,
    _filter_defs_struct,
    _load_local,
    _load_structural,
    _max_drawdown,
    _proxy_cols,
)


LOCAL_GROUP_COLS = ["candidate_desc", "stop_model", "exit_model"]
STRUCT_GROUP_COLS = ["family", "stop_model", "policy"]
TARGET_MIN_TRADES_PER_DAY = 3.0
TARGET_MAX_TRADES_PER_DAY = 15.0


def _summary(df: pd.DataFrame) -> pd.Series:
    vals = pd.to_numeric(df["net_r"], errors="coerce").dropna()
    daily = df.groupby("date")["net_r"].sum()
    monthly = df.groupby("month")["net_r"].sum()
    session_sum = df.groupby("session_bucket")["net_r"].sum()
    trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
    symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(df.groupby("symbol")["net_r"].sum())
    unique_days = max(int(df["date"].nunique()), 1)
    return pd.Series(
        {
            "trades": int(len(df)),
            "symbols": int(df["symbol"].nunique()),
            "days": int(df["date"].nunique()),
            "sessions": int(df["session_bucket"].nunique()),
            "trades_per_active_day": float(len(df) / unique_days),
            "avg_r": float(vals.mean()) if len(vals) else np.nan,
            "median_r": float(vals.median()) if len(vals) else np.nan,
            "sum_r": float(vals.sum()) if len(vals) else np.nan,
            "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
            "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
            "positive_month_rate": float((monthly > 0).mean()) if len(monthly) else np.nan,
            "positive_session_rate": float((session_sum > 0).mean()) if len(session_sum) else np.nan,
            "top_trade_independence_pct": trade_pct,
            "removed_top_trades_to_negative": removed_trades,
            "winning_trades": winning_trades,
            "top_symbol_independence_pct": symbol_pct,
            "removed_top_symbols_to_negative": removed_symbols,
            "winning_symbols": winning_symbols,
            "max_drawdown_r": _max_drawdown(df.sort_values("entry_timestamp_ms")["net_r"]),
            "initial_risk_pct_median": float(pd.to_numeric(df["initial_risk_pct"], errors="coerce").median()),
        }
    )


def _score(row: pd.Series) -> float:
    avg = np.tanh(float(row["avg_r"]) / 0.20)
    med = np.tanh(float(row["median_r"]) / 0.20)
    trade_ind = min(float(row["top_trade_independence_pct"]), 0.50) / 0.50
    symbol_ind = min(float(row["top_symbol_independence_pct"]), 0.50) / 0.50
    pos_day = float(row["positive_day_rate"])
    pos_month = float(row["positive_month_rate"])
    sample = min(float(row["trades"]) / 1000.0, 1.0)
    freq = float(row["trades_per_active_day"])
    if freq < TARGET_MIN_TRADES_PER_DAY:
        freq_score = max(freq / TARGET_MIN_TRADES_PER_DAY, 0.0)
    elif freq <= TARGET_MAX_TRADES_PER_DAY:
        freq_score = 1.0
    else:
        freq_score = max(0.0, 1.0 - (freq - TARGET_MAX_TRADES_PER_DAY) / TARGET_MAX_TRADES_PER_DAY)
    dd = min(abs(float(row["max_drawdown_r"])) / 40.0, 1.0)
    return float(
        0.16 * avg
        + 0.17 * med
        + 0.17 * trade_ind
        + 0.12 * symbol_ind
        + 0.12 * pos_day
        + 0.08 * pos_month
        + 0.08 * sample
        + 0.10 * freq_score
        - 0.14 * dd
    )


def _candidate_id(source: str, filter_name: str, group_values: tuple[object, ...]) -> str:
    return source + "::" + filter_name + "::" + "::".join(str(v) for v in group_values)


def _make_candidates(df: pd.DataFrame, filters: dict[str, pd.Series], group_cols: list[str], source: str, *, min_rows: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    candidates: list[pd.Series] = []
    trade_sets: dict[str, pd.DataFrame] = {}
    for filter_name, mask in filters.items():
        filtered = df[mask.fillna(False)].copy()
        if len(filtered) < min_rows:
            continue
        for keys, group in filtered.groupby(group_cols, dropna=False):
            if len(group) < min_rows:
                continue
            key_tuple = keys if isinstance(keys, tuple) else (keys,)
            cid = _candidate_id(source, filter_name, key_tuple)
            g = group.copy()
            g["candidate_uid"] = cid
            g["candidate_rank_key"] = cid
            s = _summary(g)
            s["candidate_uid"] = cid
            s["source"] = source
            s["filter_name"] = filter_name
            for col, value in zip(group_cols, key_tuple):
                s[col] = value
            s["score"] = _score(s)
            s["is_candidate"] = (
                (s["avg_r"] > 0)
                and (s["median_r"] > -0.05)
                and (s["positive_day_rate"] >= 0.45)
                and (s["top_trade_independence_pct"] >= 0.05)
                and (s["top_symbol_independence_pct"] >= 0.03)
            )
            candidates.append(s)
            trade_sets[cid] = g
    out = pd.DataFrame(candidates)
    if out.empty:
        return out, trade_sets
    return out.sort_values(["is_candidate", "score", "sum_r"], ascending=[False, False, False]), trade_sets


def _dedupe_portfolio(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # Same market event cannot be traded multiple times by overlapping strategy
    # rows. Prefer higher candidate score/rank order already assigned.
    df["event_uid"] = df["source"].astype(str) + "|" + df.get("signal_id", df.get("setup_id", df.index)).astype(str)
    df = df.sort_values(["entry_timestamp_ms", "candidate_order"], ascending=[True, True])
    return df.drop_duplicates("event_uid", keep="first")


def _portfolio_rows(name: str, candidate_ids: list[str], trade_sets: dict[str, pd.DataFrame], candidate_table: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    frames = []
    score_lookup = candidate_table.set_index("candidate_uid")["score"].to_dict()
    for order, cid in enumerate(candidate_ids):
        g = trade_sets[cid].copy()
        g["candidate_order"] = order
        g["candidate_score"] = float(score_lookup.get(cid, 0.0))
        frames.append(g)
    port = _dedupe_portfolio(frames)
    s = _summary(port) if not port.empty else pd.Series(dtype=object)
    s["portfolio_name"] = name
    s["candidate_count"] = len(candidate_ids)
    s["candidate_uids"] = "|".join(candidate_ids)
    s["portfolio_score"] = _score(s) if not port.empty else np.nan
    s["frequency_target_pass"] = bool(TARGET_MIN_TRADES_PER_DAY <= float(s.get("trades_per_active_day", 0.0)) <= TARGET_MAX_TRADES_PER_DAY)
    s["strict_pass"] = bool(
        s["frequency_target_pass"]
        and float(s.get("avg_r", -1)) > 0
        and float(s.get("median_r", -1)) > 0
        and float(s.get("top_trade_independence_pct", 0)) >= 0.50
        and float(s.get("top_symbol_independence_pct", 0)) >= 0.50
    )
    return s, port


def _build_portfolios(candidates: pd.DataFrame, trade_sets: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    usable = candidates[candidates["is_candidate"].astype(bool)].copy()
    if usable.empty:
        return pd.DataFrame(), pd.DataFrame()

    portfolios: list[pd.Series] = []
    port_trades: list[pd.DataFrame] = []

    # 1) Greedy by score until frequency target is reached.
    greedy_ids: list[str] = []
    for cid in usable["candidate_uid"].head(120):
        greedy_ids.append(cid)
        s, p = _portfolio_rows(f"greedy_top_{len(greedy_ids)}", greedy_ids, trade_sets, candidates)
        portfolios.append(s)
        p["portfolio_name"] = s["portfolio_name"]
        port_trades.append(p)
        if float(s["trades_per_active_day"]) >= TARGET_MAX_TRADES_PER_DAY:
            break

    # 2) Source-specific and family-specific baskets.
    for source in sorted(usable["source"].dropna().unique()):
        ids = usable[usable["source"].eq(source)]["candidate_uid"].head(80).tolist()
        for n in [3, 5, 8, 12, 20, 40]:
            if len(ids) >= n:
                s, p = _portfolio_rows(f"{source}_top_{n}", ids[:n], trade_sets, candidates)
                portfolios.append(s)
                p["portfolio_name"] = s["portfolio_name"]
                port_trades.append(p)

    if "family" in usable.columns:
        for family in sorted(usable["family"].dropna().astype(str).unique()):
            ids = usable[usable["family"].astype(str).eq(family)]["candidate_uid"].head(40).tolist()
            for n in [3, 5, 10, 20]:
                if len(ids) >= n:
                    s, p = _portfolio_rows(f"family_{family}_top_{n}", ids[:n], trade_sets, candidates)
                    portfolios.append(s)
                    p["portfolio_name"] = s["portfolio_name"]
                    port_trades.append(p)

    # 3) Pair/triple combinations among top candidates to avoid greedy traps.
    top_ids = usable["candidate_uid"].head(30).tolist()
    combo_specs: list[tuple[str, list[str]]] = []
    for combo in combinations(top_ids[:20], 2):
        combo_specs.append(("pair", list(combo)))
    for combo in combinations(top_ids[:15], 3):
        combo_specs.append(("triple", list(combo)))
    for idx, (kind, ids) in enumerate(combo_specs):
        s, p = _portfolio_rows(f"{kind}_{idx}", ids, trade_sets, candidates)
        portfolios.append(s)
        p["portfolio_name"] = s["portfolio_name"]
        port_trades.append(p)

    out = pd.DataFrame(portfolios)
    out = out.sort_values(["strict_pass", "frequency_target_pass", "portfolio_score", "sum_r"], ascending=[False, False, False, False])

    if port_trades:
        keep_names = set(out.head(50)["portfolio_name"])
        trades = pd.concat([p for p in port_trades if p["portfolio_name"].iloc[0] in keep_names], ignore_index=True)
    else:
        trades = pd.DataFrame()
    return out, trades


def _write_report(out_dir: Path, candidates: pd.DataFrame, portfolios: pd.DataFrame) -> None:
    cand_cols = [
        "candidate_uid",
        "source",
        "filter_name",
        "family",
        "candidate_desc",
        "stop_model",
        "policy",
        "exit_model",
        "trades",
        "symbols",
        "days",
        "trades_per_active_day",
        "avg_r",
        "median_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "score",
    ]
    cand_cols = [c for c in cand_cols if c in candidates.columns]
    port_cols = [
        "portfolio_name",
        "candidate_count",
        "trades",
        "symbols",
        "days",
        "sessions",
        "trades_per_active_day",
        "avg_r",
        "median_r",
        "sum_r",
        "win_rate",
        "positive_day_rate",
        "positive_month_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "max_drawdown_r",
        "portfolio_score",
        "frequency_target_pass",
        "strict_pass",
    ]
    text = f"""# Short Portfolio Combination Search

Objective: find baskets of short strategies with roughly 3-15 trades per active
day, while preserving median R, top-trade/top-symbol independence, sessions and
symbol breadth.

Candidate rows:

```text
{len(candidates)}
```

Usable candidate rows:

```text
{int(candidates['is_candidate'].sum()) if not candidates.empty else 0}
```

Portfolios evaluated:

```text
{len(portfolios)}
```

Strict portfolio passes:

```text
{int(portfolios['strict_pass'].sum()) if not portfolios.empty else 0}
```

Frequency target passes:

```text
{int(portfolios['frequency_target_pass'].sum()) if not portfolios.empty else 0}
```

Top candidates:

```text
{candidates[candidates['is_candidate'].astype(bool)].head(20)[cand_cols].to_string(index=False) if not candidates.empty else 'none'}
```

Top portfolios:

```text
{portfolios.head(20)[port_cols].to_string(index=False) if not portfolios.empty else 'none'}
```
"""
    (out_dir / "short_portfolio_combination_search.md").write_text(text, encoding="utf-8")


def build(large_dir: Path, failed_dir: Path) -> None:
    local = _proxy_cols(_load_local(large_dir))
    structural = _proxy_cols(_load_structural(failed_dir))

    local_candidates, local_sets = _make_candidates(
        local,
        _filter_defs_local(local),
        LOCAL_GROUP_COLS,
        "large_runner_local_high",
        min_rows=50,
    )
    structural_candidates, structural_sets = _make_candidates(
        structural,
        _filter_defs_struct(structural),
        STRUCT_GROUP_COLS,
        "failed_pump_structural",
        min_rows=25,
    )
    candidates = pd.concat([local_candidates, structural_candidates], ignore_index=True)
    if not candidates.empty:
        candidates = candidates.sort_values(["is_candidate", "score", "sum_r"], ascending=[False, False, False])

    trade_sets = {**local_sets, **structural_sets}
    portfolios, top_trades = _build_portfolios(candidates, trade_sets)

    out_dir = large_dir
    candidates.to_csv(out_dir / "short_portfolio_candidate_library.csv", index=False)
    portfolios.to_csv(out_dir / "short_portfolio_combination_search.csv", index=False)
    top_trades.to_csv(out_dir / "short_portfolio_top_trades.csv", index=False)
    _write_report(out_dir, candidates, portfolios)

    print(f"candidate_rows={len(candidates)}")
    print(f"usable_candidates={int(candidates['is_candidate'].sum()) if not candidates.empty else 0}")
    print(f"portfolio_rows={len(portfolios)}")
    print(f"frequency_passes={int(portfolios['frequency_target_pass'].sum()) if not portfolios.empty else 0}")
    print(f"strict_passes={int(portfolios['strict_pass'].sum()) if not portfolios.empty else 0}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--large-dir", default=".output/results/large_runner_discovery_365d")
    parser.add_argument("--failed-dir", default=".output/results/failed_pump_short_research_365d")
    args = parser.parse_args()
    build(Path(args.large_dir), Path(args.failed_dir))


if __name__ == "__main__":
    main()
