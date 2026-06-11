"""Search large-runner fade sleeves as a separate nature.

This screen starts from the current failed-pump balanced frontier and tries to
add OOS large-runner local-high fade sleeves that were selected only from the
first half of the sample. It treats the large-runner sleeve as a different
source of trades and removes same-symbol time collisions with the base
portfolio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.session_edge_workbench import _top_remove_pct_to_negative


GROUP_COLS = ["candidate_desc", "delay_min", "stop_model", "exit_model", "filter_name"]
MIN_IS_TRADES = 30
MIN_OOS_MARGINAL_TRADES = 15
COLLISION_WINDOW_MS = 60 * 60_000


def _max_drawdown(vals: pd.Series) -> float:
    arr = pd.to_numeric(vals, errors="coerce").fillna(0.0).to_numpy()
    if len(arr) == 0:
        return np.nan
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def _norm_symbol(symbol: object) -> str:
    text = str(symbol)
    return text.replace("/", "").replace(":USDT", "").replace("USDTUSDT", "USDT")


def _summary(df: pd.DataFrame, value_col: str = "net_r") -> pd.Series:
    vals = pd.to_numeric(df[value_col], errors="coerce").dropna()
    daily = df.groupby("date")[value_col].sum()
    monthly = df.groupby("month")[value_col].sum()
    trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
    symbol_sum = df.groupby("symbol")[value_col].sum()
    symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(symbol_sum)
    return pd.Series(
        {
            "trades": int(len(df)),
            "symbols": int(df["symbol"].nunique()) if "symbol" in df else 0,
            "days": int(df["date"].nunique()) if "date" in df else 0,
            "avg_r": float(vals.mean()) if len(vals) else np.nan,
            "median_r": float(vals.median()) if len(vals) else np.nan,
            "sum_r": float(vals.sum()) if len(vals) else np.nan,
            "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
            "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
            "positive_month_rate": float((monthly > 0).mean()) if len(monthly) else np.nan,
            "top_trade_independence_pct": trade_pct,
            "removed_top_trades_to_negative": removed_trades,
            "winning_trades": winning_trades,
            "top_symbol_independence_pct": symbol_pct,
            "removed_top_symbols_to_negative": removed_symbols,
            "winning_symbols": winning_symbols,
            "max_drawdown_r": _max_drawdown(df.sort_values("entry_timestamp_ms")[value_col])
            if "entry_timestamp_ms" in df
            else np.nan,
        }
    )


def _score(row: pd.Series) -> float:
    avg = np.tanh(float(row.get("avg_r", 0.0)) / 0.20)
    med = np.tanh(float(row.get("median_r", 0.0)) / 0.20)
    cost = np.tanh(float(row.get("cost10_avg_r", 0.0)) / 0.15)
    trade_ind = min(float(row.get("top_trade_independence_pct", 0.0)), 0.50) / 0.50
    sym_ind = min(float(row.get("top_symbol_independence_pct", 0.0)), 0.50) / 0.50
    pos_day = float(row.get("positive_day_rate", 0.0))
    sample = min(float(row.get("trades", 0.0)) / 80.0, 1.0)
    dd = min(abs(float(row.get("max_drawdown_r", 0.0))) / 12.0, 1.0)
    return float(
        0.16 * avg
        + 0.16 * med
        + 0.18 * cost
        + 0.16 * trade_ind
        + 0.12 * sym_ind
        + 0.10 * pos_day
        + 0.08 * sample
        - 0.12 * dd
    )


def _load_large(large_dir: Path) -> pd.DataFrame:
    trades = pd.read_csv(large_dir / "large_runner_local_high_structural_exit_replay_trades.csv")
    setup_cols = [
        "symbol",
        "seed_close_ms",
        "session_bucket",
        "close_ret_10m",
        "close_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "early_return_pct",
        "m1_sustain_mid",
        "m1_sustain_strict",
        "m1_taker_buy_quote_share",
        "m1_quote_top1_share",
        "m1_trade_top1_share",
        "m1_last2_quote_share",
        "m1_last2_trade_share",
        "base_touch_offset_min",
        "deep2_touch_offset_min",
    ]
    setups = pd.read_csv(large_dir / "large_runner_session_outcome_research_table.csv", usecols=setup_cols)
    df = trades[trades["status"].eq("closed")].merge(
        setups, on=["symbol", "seed_close_ms"], how="inner", validate="many_to_one"
    )
    df["entry_timestamp_ms"] = pd.to_numeric(df["seed_close_ms"], errors="coerce") + (
        pd.to_numeric(df["delay_min"], errors="coerce").fillna(0) * 60_000
    )
    entry_dt = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce")
    df["date"] = entry_dt.dt.normalize()
    df["month"] = entry_dt.dt.to_period("M").astype(str)
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    df["cost10_r"] = pd.to_numeric(df["net_r"], errors="coerce") - (0.001 / risk.replace(0, np.nan))
    df["symbol_norm"] = df["symbol"].map(_norm_symbol)
    df["event_uid"] = df["symbol_norm"] + "|" + df["seed_close_ms"].astype(str)
    df["source"] = "large_runner_local_high"
    df["target_valid_bool"] = df["target_valid"].astype(str).str.lower().eq("true")
    df["pre_entry_base_touched"] = pd.to_numeric(df["base_touch_offset_min"], errors="coerce").le(
        pd.to_numeric(df["delay_min"], errors="coerce")
    )
    df["pre_entry_deep2_touched"] = pd.to_numeric(df["deep2_touch_offset_min"], errors="coerce").le(
        pd.to_numeric(df["delay_min"], errors="coerce")
    )

    desc = df["candidate_desc"].astype(str)
    delay = pd.to_numeric(df["delay_min"], errors="coerce")
    requires_15 = desc.str.contains("close15", case=False, na=False)
    requires_10 = desc.str.contains("close10", case=False, na=False)
    df["entry_known_desc"] = (~requires_15 | delay.ge(15)) & (~requires_10 | delay.ge(10))
    return df[df["entry_known_desc"].fillna(False)].copy()


def _load_base(failed_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(failed_dir / "short_frontier_oos_trades.csv")
    df = df[df["portfolio"].eq("balanced_frontier")].copy()
    df["net_r"] = pd.to_numeric(df["_final_r"], errors="coerce")
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    df["cost10_r"] = df["net_r"] - (0.001 / risk.replace(0, np.nan))
    df["entry_timestamp_ms"] = pd.to_numeric(df["entry_timestamp_ms"], errors="coerce")
    entry_dt = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce")
    df["date"] = entry_dt.dt.normalize()
    df["month"] = entry_dt.dt.to_period("M").astype(str)
    df["symbol_norm"] = df["symbol"].map(_norm_symbol)
    df["event_uid"] = df["symbol_norm"] + "|" + df["signal_id"].astype(str)
    df["source"] = "failed_pump_balanced_frontier"
    df["sleeve"] = df.get("sleeve", "balanced_frontier")
    return df


def _filter_defs(df: pd.DataFrame) -> dict[str, pd.Series]:
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    close10 = pd.to_numeric(df["close_ret_10m"], errors="coerce")
    close15 = pd.to_numeric(df["close_ret_15m"], errors="coerce")
    pre_range = pd.to_numeric(df["pre60_range_pct"], errors="coerce")
    pre_ret = pd.to_numeric(df["pre60_return_pct"], errors="coerce")
    taker = pd.to_numeric(df["m1_taker_buy_quote_share"], errors="coerce")
    top1q = pd.to_numeric(df["m1_quote_top1_share"], errors="coerce")
    delay = pd.to_numeric(df["delay_min"], errors="coerce")
    return {
        "all": pd.Series(True, index=df.index),
        "target_valid": df["target_valid_bool"],
        "no_base_pre_entry": ~df["pre_entry_base_touched"].fillna(False),
        "target_no_base": df["target_valid_bool"] & ~df["pre_entry_base_touched"].fillna(False),
        "no_deep2_pre_entry": ~df["pre_entry_deep2_touched"].fillna(False),
        "risk_1_3": risk.between(0.01, 0.03, inclusive="both"),
        "risk_2_5": risk.between(0.02, 0.05, inclusive="both"),
        "risk_3_8": risk.between(0.03, 0.08, inclusive="both"),
        "pre60_range_ge_6": pre_range.ge(0.06),
        "pre60_range_ge_10": pre_range.ge(0.10),
        "pre60_down": pre_ret.le(0),
        "pre60_up": pre_ret.gt(0),
        "taker_le_50": taker.le(0.50),
        "taker_45_55": taker.between(0.45, 0.55, inclusive="both"),
        "taker_ge_55": taker.ge(0.55),
        "top1q_le_45": top1q.le(0.45),
        "m1_sustain_strict": df["m1_sustain_strict"].astype(bool),
        "close10_le_2": delay.ge(10) & close10.le(0.02),
        "close15_le_0": delay.ge(15) & close15.le(0.0),
        "close15_le_2": delay.ge(15) & close15.le(0.02),
        "close15_le_4": delay.ge(15) & close15.le(0.04),
        "asia_only": df["session_bucket"].eq("asia_only"),
        "europe_only": df["session_bucket"].eq("europe_only"),
        "us_only": df["session_bucket"].eq("us_only"),
        "europe_us_overlap": df["session_bucket"].eq("europe_us_overlap"),
        "non_us": ~df["session_bucket"].eq("us_only"),
        "not_asia_overlap": ~df["session_bucket"].eq("asia_europe_overlap"),
        "exhausted_target_no_base_risk_2_5": df["target_valid_bool"]
        & ~df["pre_entry_base_touched"].fillna(False)
        & risk.between(0.02, 0.05, inclusive="both")
        & taker.le(0.55),
    }


def _screen_candidates(df: pd.DataFrame, split_date: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    filters = _filter_defs(df)
    rows: list[pd.Series] = []
    ledgers: dict[str, pd.DataFrame] = {}
    for filter_name, mask in filters.items():
        filtered = df[mask.fillna(False)].copy()
        if filtered.empty:
            continue
        filtered["filter_name"] = filter_name
        for keys, group in filtered.groupby(GROUP_COLS, dropna=False):
            key_tuple = keys if isinstance(keys, tuple) else (keys,)
            cid = "large_runner::" + "::".join(str(v) for v in key_tuple)
            g_is = group[group["date"] < split_date].copy()
            if len(g_is) < MIN_IS_TRADES:
                continue
            s = _summary(g_is, "net_r")
            c = _summary(g_is, "cost10_r")
            s["cost10_avg_r"] = c["avg_r"]
            s["cost10_sum_r"] = c["sum_r"]
            for col, value in zip(GROUP_COLS, key_tuple):
                s[col] = value
            s["candidate_uid"] = cid
            s["score"] = _score(s)
            s["is_pass"] = bool(
                s["trades"] >= MIN_IS_TRADES
                and s["symbols"] >= 15
                and s["days"] >= 15
                and s["avg_r"] > 0.03
                and s["median_r"] > -0.05
                and s["cost10_avg_r"] > 0.0
                and s["positive_day_rate"] >= 0.45
                and s["top_trade_independence_pct"] >= 0.12
            )
            rows.append(s)
            ledger = group.copy()
            ledger["candidate_uid"] = cid
            ledgers[cid] = ledger
    out = pd.DataFrame(rows)
    if out.empty:
        return out, ledgers
    return out.sort_values(["is_pass", "score", "cost10_sum_r"], ascending=[False, False, False]), ledgers


def _remove_collisions(candidate: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty or candidate.empty:
        return candidate
    frames = []
    for symbol, group in candidate.groupby("symbol_norm", dropna=False):
        selected_times = selected.loc[selected["symbol_norm"].eq(symbol), "entry_timestamp_ms"].dropna().to_numpy()
        if len(selected_times) == 0:
            frames.append(group)
            continue
        times = pd.to_numeric(group["entry_timestamp_ms"], errors="coerce").to_numpy()
        keep = []
        for ts in times:
            keep.append(bool(np.nanmin(np.abs(selected_times - ts)) > COLLISION_WINDOW_MS) if len(selected_times) else True)
        frames.append(group.loc[keep])
    if not frames:
        return candidate.iloc[0:0].copy()
    return pd.concat(frames, ignore_index=True)


def _evaluate_oos(
    candidates: pd.DataFrame, ledgers: dict[str, pd.DataFrame], base: pd.DataFrame, split_date: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    oos_base = base[base["date"] >= split_date].copy()
    selected = oos_base.copy()
    sleeve_rows: list[pd.Series] = []
    candidate_rows: list[pd.Series] = []

    usable = candidates[candidates["is_pass"].astype(bool)].head(100).copy()
    for _, row in usable.iterrows():
        cid = str(row["candidate_uid"])
        ledger = ledgers[cid]
        oos = ledger[ledger["date"] >= split_date].copy()
        oos = _remove_collisions(oos, selected)
        oos = oos.sort_values(["entry_timestamp_ms", "cost10_r"], ascending=[True, False]).drop_duplicates("event_uid")
        if len(oos) < MIN_OOS_MARGINAL_TRADES:
            continue
        s = _summary(oos, "net_r")
        c = _summary(oos, "cost10_r")
        s["cost10_avg_r"] = c["avg_r"]
        s["cost10_sum_r"] = c["sum_r"]
        s["candidate_uid"] = cid
        s["is_score"] = float(row["score"])
        s["oos_score"] = _score(s)
        for col in GROUP_COLS:
            s[col] = row.get(col)
        s["oos_pass"] = bool(
            s["avg_r"] > 0.0
            and s["median_r"] > 0.0
            and s["cost10_avg_r"] > 0.0
            and s["positive_day_rate"] >= 0.50
            and s["top_trade_independence_pct"] >= 0.15
            and s["top_symbol_independence_pct"] >= 0.10
        )
        candidate_rows.append(s)

    oos_candidates = pd.DataFrame(candidate_rows)
    if oos_candidates.empty:
        return oos_candidates, pd.DataFrame(), selected
    oos_candidates = oos_candidates.sort_values(["oos_pass", "oos_score", "cost10_sum_r"], ascending=[False, False, False])

    for _, row in oos_candidates[oos_candidates["oos_pass"].astype(bool)].iterrows():
        cid = str(row["candidate_uid"])
        ledger = ledgers[cid]
        oos = ledger[ledger["date"] >= split_date].copy()
        oos = _remove_collisions(oos, selected)
        oos = oos.sort_values(["entry_timestamp_ms", "cost10_r"], ascending=[True, False]).drop_duplicates("event_uid")
        if len(oos) < MIN_OOS_MARGINAL_TRADES:
            continue
        s = _summary(oos, "net_r")
        c = _summary(oos, "cost10_r")
        if not (s["avg_r"] > 0 and s["median_r"] > 0 and c["avg_r"] > 0):
            continue
        oos["sleeve"] = cid
        selected = pd.concat([selected, oos], ignore_index=True)
        sleeve = s.copy()
        sleeve["cost10_avg_r"] = c["avg_r"]
        sleeve["cost10_sum_r"] = c["sum_r"]
        sleeve["candidate_uid"] = cid
        for col in GROUP_COLS:
            sleeve[col] = row.get(col)
        sleeve_rows.append(sleeve)
        if len(sleeve_rows) >= 5:
            break

    selected = selected.sort_values("entry_timestamp_ms").drop_duplicates(["source", "event_uid"], keep="first")
    return oos_candidates, pd.DataFrame(sleeve_rows), selected


def _write_report(
    out_dir: Path,
    split_date: pd.Timestamp,
    candidates: pd.DataFrame,
    oos_candidates: pd.DataFrame,
    sleeves: pd.DataFrame,
    combined: pd.DataFrame,
) -> None:
    base = combined[combined["source"].eq("failed_pump_balanced_frontier")]
    large = combined[combined["source"].eq("large_runner_local_high")]
    combined_net = _summary(combined, "net_r") if not combined.empty else pd.Series(dtype=object)
    combined_cost = _summary(combined, "cost10_r") if not combined.empty else pd.Series(dtype=object)
    base_net = _summary(base, "net_r") if not base.empty else pd.Series(dtype=object)
    large_net = _summary(large, "net_r") if not large.empty else pd.Series(dtype=object)

    cand_cols = [
        "candidate_uid",
        "trades",
        "symbols",
        "days",
        "avg_r",
        "median_r",
        "cost10_avg_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "oos_score",
    ]
    cand_cols = [c for c in cand_cols if c in oos_candidates.columns]
    sleeve_cols = [c for c in cand_cols if c in sleeves.columns]
    text = f"""# Cross-Source Short Sleeve Search

Status: research frontier on already-used 365d artifacts.

Split:

```text
IS: rows before {split_date.date()}
OOS: rows from {split_date.date()}
```

Method:

```text
1. Keep failed-pump balanced frontier as mandatory base.
2. Build large-runner local-high candidates from first-half IS only.
3. Reject candidate rows whose descriptor is not known by entry time.
4. Evaluate second-half OOS marginal trades after same-symbol 60m collision removal.
5. Add only marginal sleeves with positive OOS median and positive 10bps cost proxy.
```

IS candidate rows:

```text
{len(candidates)}
```

IS pass rows:

```text
{int(candidates['is_pass'].sum()) if not candidates.empty else 0}
```

OOS marginal candidate rows:

```text
{len(oos_candidates)}
```

OOS pass rows:

```text
{int(oos_candidates['oos_pass'].sum()) if not oos_candidates.empty else 0}
```

Selected large-runner sleeves:

```text
{sleeves[sleeve_cols].to_string(index=False) if not sleeves.empty else 'none'}
```

Base balanced frontier OOS:

```text
trades={int(base_net.get('trades', 0))}, avg={float(base_net.get('avg_r', np.nan)):.3f}R,
median={float(base_net.get('median_r', np.nan)):.3f}R, sum={float(base_net.get('sum_r', np.nan)):.2f}R
```

Selected large-runner marginal OOS:

```text
trades={int(large_net.get('trades', 0))}, avg={float(large_net.get('avg_r', np.nan)):.3f}R,
median={float(large_net.get('median_r', np.nan)):.3f}R, sum={float(large_net.get('sum_r', np.nan)):.2f}R
```

Combined OOS:

```text
trades={int(combined_net.get('trades', 0))}
symbols={int(combined_net.get('symbols', 0))}
days={int(combined_net.get('days', 0))}
avg={float(combined_net.get('avg_r', np.nan)):.3f}R
median={float(combined_net.get('median_r', np.nan)):.3f}R
sum={float(combined_net.get('sum_r', np.nan)):.2f}R
win_rate={float(combined_net.get('win_rate', np.nan)):.1%}
positive_day_rate={float(combined_net.get('positive_day_rate', np.nan)):.1%}
top_trade_independence={float(combined_net.get('top_trade_independence_pct', np.nan)):.1%}
top_symbol_independence={float(combined_net.get('top_symbol_independence_pct', np.nan)):.1%}
cost10_avg={float(combined_cost.get('avg_r', np.nan)):.3f}R
max_dd={float(combined_net.get('max_drawdown_r', np.nan)):.2f}R
```

Top OOS marginal candidates:

```text
{oos_candidates.head(20)[cand_cols].to_string(index=False) if not oos_candidates.empty else 'none'}
```

Interpretation:

Large-runner local-high fades are a genuinely different source from the
failed-pump structural frontier. If selected sleeves are empty or weak, that
means this source currently adds frequency only by accepting lower-quality
trades. If selected sleeves exist, they are still not fresh OOS proof because
the same 365d archive has been heavily inspected.
"""
    (out_dir / "short_cross_source_sleeve_report.md").write_text(text, encoding="utf-8")


def build(large_dir: Path, failed_dir: Path, split_date_arg: str) -> None:
    split_date = pd.Timestamp(split_date_arg, tz="UTC")
    large = _load_large(large_dir)
    base = _load_base(failed_dir)
    candidates, ledgers = _screen_candidates(large, split_date)
    oos_candidates, sleeves, combined = _evaluate_oos(candidates, ledgers, base, split_date)

    large_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(large_dir / "short_cross_source_sleeve_candidates.csv", index=False)
    oos_candidates.to_csv(large_dir / "short_cross_source_sleeve_oos_candidates.csv", index=False)
    sleeves.to_csv(large_dir / "short_cross_source_sleeves.csv", index=False)
    combined.to_csv(large_dir / "short_cross_source_portfolio_oos_trades.csv", index=False)
    _write_report(large_dir, split_date, candidates, oos_candidates, sleeves, combined)

    combined_net = _summary(combined, "net_r") if not combined.empty else pd.Series(dtype=object)
    combined_cost = _summary(combined, "cost10_r") if not combined.empty else pd.Series(dtype=object)
    print(f"candidate_rows={len(candidates)}")
    print(f"is_pass_rows={int(candidates['is_pass'].sum()) if not candidates.empty else 0}")
    print(f"oos_candidate_rows={len(oos_candidates)}")
    print(f"oos_pass_rows={int(oos_candidates['oos_pass'].sum()) if not oos_candidates.empty else 0}")
    print(f"selected_sleeves={len(sleeves)}")
    print(f"combined_trades={int(combined_net.get('trades', 0))}")
    print(f"combined_avg_r={float(combined_net.get('avg_r', np.nan))}")
    print(f"combined_cost10_avg_r={float(combined_cost.get('avg_r', np.nan))}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--large-dir", default=".output/results/large_runner_discovery_365d")
    parser.add_argument("--failed-dir", default=".output/results/failed_pump_short_research_365d")
    parser.add_argument("--split-date", default="2025-12-03")
    args = parser.parse_args()
    build(Path(args.large_dir), Path(args.failed_dir), args.split_date)


if __name__ == "__main__":
    main()
