"""Screen short-strategy enhancement families on existing 365d artifacts.

The script has two layers:

1. realized screens over already-simulated trades;
2. explicitly marked path-required proxy variants for BE/time-stop style ideas.

It does not claim proxy variants as executable edge. They are triage signals for
which path replay should be implemented next.
"""

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


LOCAL_MIN_ROWS = 50
STRUCT_MIN_ROWS = 25


def _max_drawdown(vals: pd.Series) -> float:
    arr = pd.to_numeric(vals, errors="coerce").fillna(0.0).to_numpy()
    if len(arr) == 0:
        return np.nan
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def _rolling_stats(df: pd.DataFrame, value_col: str, days: int) -> dict[str, float]:
    daily = df.groupby("date")[value_col].sum().sort_index()
    if daily.empty:
        return {
            f"roll{days}_min_sum": np.nan,
            f"roll{days}_median_sum": np.nan,
            f"roll{days}_positive_rate": np.nan,
        }
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(idx, fill_value=0.0)
    min_periods = max(5, min(days, len(daily)) // 3)
    roll = daily.rolling(days, min_periods=min_periods).sum().dropna()
    if roll.empty:
        return {
            f"roll{days}_min_sum": np.nan,
            f"roll{days}_median_sum": np.nan,
            f"roll{days}_positive_rate": np.nan,
        }
    return {
        f"roll{days}_min_sum": float(roll.min()),
        f"roll{days}_median_sum": float(roll.median()),
        f"roll{days}_positive_rate": float((roll > 0).mean()),
    }


def _summarize(df: pd.DataFrame, *, value_col: str, include_rolling: bool = False) -> pd.Series:
    vals = pd.to_numeric(df[value_col], errors="coerce").dropna()
    trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
    symbol_sum = df.groupby("symbol")[value_col].sum()
    symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(symbol_sum)
    daily = df.groupby("date")[value_col].sum()
    monthly = df.groupby("month")[value_col].sum()
    out = {
        "trades": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "days": int(df["date"].nunique()),
        "sessions": int(df["session_bucket"].nunique()) if "session_bucket" in df else 0,
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
        "max_drawdown_r": _max_drawdown(df.sort_values("entry_timestamp_ms")[value_col]),
        "mfe_median": float(pd.to_numeric(df.get("mfe_r", pd.Series(dtype=float)), errors="coerce").median()),
        "mae_median": float(pd.to_numeric(df.get("mae_r", pd.Series(dtype=float)), errors="coerce").median()),
        "initial_risk_pct_median": float(pd.to_numeric(df.get("initial_risk_pct", pd.Series(dtype=float)), errors="coerce").median()),
    }
    if include_rolling:
        out.update(_rolling_stats(df, value_col, 30))
        out.update(_rolling_stats(df, value_col, 60))
    else:
        out.update(
            {
                "roll30_min_sum": np.nan,
                "roll30_median_sum": np.nan,
                "roll30_positive_rate": np.nan,
                "roll60_min_sum": np.nan,
                "roll60_median_sum": np.nan,
                "roll60_positive_rate": np.nan,
            }
        )
    if "outcome_class" in df:
        out["fader_rate"] = float(df["outcome_class"].eq("fast_base_fader").mean())
        out["runner_rate"] = float(df["outcome_class"].eq("runner10").mean())
    else:
        out["fader_rate"] = np.nan
        out["runner_rate"] = np.nan
    return pd.Series(out)


def _score(row: pd.Series) -> float:
    avg = np.tanh(float(row["avg_r"]) / 0.25)
    med = np.tanh(float(row["median_r"]) / 0.25)
    trade_ind = min(float(row["top_trade_independence_pct"]), 0.50) / 0.50
    sym_ind = min(float(row["top_symbol_independence_pct"]), 0.50) / 0.50
    pos_day = float(row["positive_day_rate"])
    roll30_raw = row.get("roll30_positive_rate", 0.0)
    roll30 = 0.0 if pd.isna(roll30_raw) else float(roll30_raw)
    breadth = min(float(row["symbols"]) / 80.0, 1.0)
    sample = min(float(row["trades"]) / 150.0, 1.0)
    dd = min(abs(float(row["max_drawdown_r"])) / 20.0, 1.0)
    return float(
        0.16 * avg
        + 0.20 * med
        + 0.17 * trade_ind
        + 0.12 * sym_ind
        + 0.12 * pos_day
        + 0.08 * roll30
        + 0.08 * breadth
        + 0.07 * sample
        - 0.12 * dd
    )


def _load_local(base_dir: Path) -> pd.DataFrame:
    trades = pd.read_csv(base_dir / "large_runner_local_high_structural_exit_replay_trades.csv")
    setups = pd.read_csv(
        base_dir / "large_runner_session_outcome_research_table.csv",
        usecols=[
            "symbol",
            "seed_close_ms",
            "session_bucket",
            "close_ret_10m",
            "close_ret_15m",
            "pre60_range_pct",
            "pre60_return_pct",
            "early_return_pct",
            "m1_taker_buy_quote_share",
            "outcome_class",
            "base_touch_offset_min",
            "deep2_touch_offset_min",
            "future60_min_path_pct",
            "runner_high10_hit_offset_min",
        ],
    )
    df = trades[trades["status"].eq("closed")].merge(setups, on=["symbol", "seed_close_ms"], how="inner")
    df["entry_timestamp_ms"] = pd.to_numeric(df["seed_close_ms"], errors="coerce") + pd.to_numeric(df["delay_min"], errors="coerce").fillna(0) * 60_000
    df["date"] = pd.to_datetime(df["seed_close_utc"], errors="coerce").dt.normalize()
    df["month"] = pd.to_datetime(df["seed_close_utc"], errors="coerce").dt.to_period("M").astype(str)
    df["source"] = "large_runner_local_high"
    df["key"] = (
        df["candidate_desc"].astype(str)
        + "|"
        + df["stop_model"].astype(str)
        + "|"
        + df["exit_model"].astype(str)
    )
    df["fader_core"] = (df["close_ret_15m"] <= 0.0339) & (df["pre60_range_pct"] >= 0.10)
    df["non_us"] = ~df["session_bucket"].eq("us_only")
    df["europe_or_off"] = df["session_bucket"].isin(["europe_only", "off_session"])
    df["pre_entry_base_touched"] = pd.to_numeric(df["base_touch_offset_min"], errors="coerce").le(df["delay_min"])
    df["pre_entry_deep2_touched"] = pd.to_numeric(df["deep2_touch_offset_min"], errors="coerce").le(df["delay_min"])
    df["target_valid_bool"] = df["target_valid"].astype(str).str.lower().eq("true")
    return df


def _load_structural(base_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(base_dir / "failed_pump_structural_management_replay_trades.csv")
    df = df[df["status"].eq("closed")].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["source"] = "failed_pump_structural"
    df["key"] = df["family"].astype(str) + "|" + df["stop_model"].astype(str) + "|" + df["policy"].astype(str)
    df["break_ge_1p5"] = pd.to_numeric(df["structural_break_depth_pct"], errors="coerce").ge(0.015)
    df["break_ge_2p0"] = pd.to_numeric(df["structural_break_depth_pct"], errors="coerce").ge(0.020)
    df["retest_ge_0p8"] = pd.to_numeric(df["failed_retest_distance_pct"], errors="coerce").ge(0.008)
    df["fast_break_le_15m"] = pd.to_numeric(df["minutes_from_seed_to_break"], errors="coerce").le(15)
    df["fast_break_le_20m"] = pd.to_numeric(df["minutes_from_seed_to_break"], errors="coerce").le(20)
    df["oneprint_post"] = df["post_dist"].astype(str).str.contains("one_print", na=False)
    df["oneprint_seed"] = df["seed_dist"].astype(str).str.contains("one_print", na=False)
    return df


def _proxy_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    net = pd.to_numeric(out["net_r"], errors="coerce")
    mfe = pd.to_numeric(out["mfe_r"], errors="coerce")
    partial = out.get("partial_taken", pd.Series(False, index=out.index)).astype(str).str.lower().eq("true")
    out["net_r_realized"] = net
    # Optimistic upper-bound: order inside OHLC path is unknown. This estimates
    # whether a BE/time-stop path replay is worth implementing, not final PnL.
    out["net_r_be_after_0p5_proxy"] = np.where((mfe >= 0.50) & (net < -0.05), -0.05, net)
    out["net_r_be_after_0p75_proxy"] = np.where((mfe >= 0.75) & (net < -0.05), -0.05, net)
    out["net_r_be_after_partial_proxy"] = np.where(partial & (net < -0.05), -0.05, net)
    out["net_r_time_stop_no_mfe025_proxy"] = np.where((mfe < 0.25) & (net < -0.35), -0.35, net)
    out["net_r_time_stop_no_mfe05_proxy"] = np.where((mfe < 0.50) & (net < -0.60), -0.60, net)
    return out


def _filter_defs_local(df: pd.DataFrame) -> dict[str, pd.Series]:
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    return {
        "none": pd.Series(True, index=df.index),
        "fader_core": df["fader_core"],
        "fader_core_non_us": df["fader_core"] & df["non_us"],
        "fader_core_europe_or_off": df["fader_core"] & df["europe_or_off"],
        "fader_core_target_valid": df["fader_core"] & df["target_valid_bool"],
        "fader_core_no_base_pre_entry": df["fader_core"] & ~df["pre_entry_base_touched"].fillna(False),
        "fader_core_no_deep2_pre_entry": df["fader_core"] & ~df["pre_entry_deep2_touched"].fillna(False),
        "fader_core_target_valid_no_base": df["fader_core"] & df["target_valid_bool"] & ~df["pre_entry_base_touched"].fillna(False),
        "fader_core_risk_1_3": df["fader_core"] & risk.between(0.01, 0.03, inclusive="both"),
        "fader_core_risk_2_5": df["fader_core"] & risk.between(0.02, 0.05, inclusive="both"),
        "fader_core_risk_2_6_target": df["fader_core"] & risk.between(0.02, 0.06, inclusive="both") & df["target_valid_bool"],
        "europe_off_target_no_base_risk_1_5": df["fader_core"] & df["europe_or_off"] & df["target_valid_bool"] & ~df["pre_entry_base_touched"].fillna(False) & risk.between(0.01, 0.05, inclusive="both"),
        "asia_only_fader_core": df["fader_core"] & df["session_bucket"].eq("asia_only"),
        "us_only_fader_core": df["fader_core"] & df["session_bucket"].eq("us_only"),
    }


def _filter_defs_struct(df: pd.DataFrame) -> dict[str, pd.Series]:
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    return {
        "none": pd.Series(True, index=df.index),
        "break1p5_retest0p8": df["break_ge_1p5"] & df["retest_ge_0p8"],
        "break2p0_retest0p8": df["break_ge_2p0"] & df["retest_ge_0p8"],
        "fast_break15": df["fast_break_le_15m"],
        "fast_break20": df["fast_break_le_20m"],
        "oneprint_post": df["oneprint_post"],
        "oneprint_seed_or_post": df["oneprint_seed"] | df["oneprint_post"],
        "risk_1_3": risk.between(0.01, 0.03, inclusive="both"),
        "risk_2_5": risk.between(0.02, 0.05, inclusive="both"),
        "asia_only": df["session_bucket"].eq("asia_only"),
        "europe_or_off": df["session_bucket"].isin(["europe_only", "off_session"]),
        "fast_break20_risk_1_5": df["fast_break_le_20m"] & risk.between(0.01, 0.05, inclusive="both"),
        "oneprint_break1p5_retest0p8": (df["oneprint_seed"] | df["oneprint_post"]) & df["break_ge_1p5"] & df["retest_ge_0p8"],
    }


def _screen(df: pd.DataFrame, *, min_rows: int, filters: dict[str, pd.Series], group_cols: list[str], source: str) -> pd.DataFrame:
    value_cols = [
        ("realized", "net_r_realized", "realized"),
    ]
    rows: list[pd.Series] = []
    for filter_name, mask in filters.items():
        filtered = df[mask.fillna(False)].copy()
        if len(filtered) < min_rows:
            continue
        for keys, group in filtered.groupby(group_cols, dropna=False):
            if len(group) < min_rows:
                continue
            for strategy_name, value_col, evidence_type in value_cols:
                summary = _summarize(group, value_col=value_col, include_rolling=False)
                for col, value in zip(group_cols, keys if isinstance(keys, tuple) else (keys,)):
                    summary[col] = value
                summary["source"] = source
                summary["filter_name"] = filter_name
                summary["strategy_name"] = strategy_name
                summary["evidence_type"] = evidence_type
                summary["balance_score"] = _score(summary)
                summary["strict_pass"] = (
                    evidence_type == "realized"
                    and summary["trades"] >= 100
                    and summary["symbols"] >= 50
                    and summary["days"] >= 50
                    and summary["avg_r"] > 0
                    and summary["median_r"] > 0
                    and summary["top_trade_independence_pct"] >= 0.50
                    and summary["top_symbol_independence_pct"] >= 0.50
                )
                summary["promising_watchlist"] = (
                    summary["trades"] >= min_rows
                    and summary["symbols"] >= min(30, min_rows)
                    and summary["days"] >= min(25, min_rows)
                    and summary["avg_r"] > 0
                    and summary["median_r"] > 0
                    and summary["positive_day_rate"] >= 0.50
                    and summary["top_trade_independence_pct"] >= 0.25
                )
                rows.append(summary)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(["strict_pass", "evidence_type", "balance_score", "sum_r"], ascending=[False, True, False, False])


def _write_report(base_dir: Path, combined: pd.DataFrame) -> None:
    realized = combined[combined["evidence_type"].eq("realized")]
    proxy = combined[combined["evidence_type"].eq("path_required_proxy")]
    top_realized = realized.head(20)
    top_proxy = proxy.head(20)
    cols = [
        "source",
        "filter_name",
        "strategy_name",
        "trades",
        "symbols",
        "days",
        "avg_r",
        "median_r",
        "sum_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "roll30_min_sum",
        "roll30_positive_rate",
        "balance_score",
        "strict_pass",
        "promising_watchlist",
    ]
    text = f"""# Short Enhancement Strategy Screen

This screen compares enhancement families on existing 365d artifacts. It does
not run a new discovery scan.

Evidence types:

```text
realized: already simulated trade results
path_required_proxy: optimistic triage for BE/time-stop ideas; not executable evidence
```

Strict realized passes:

```text
{int(realized['strict_pass'].sum()) if not realized.empty else 0}
```

Promising realized watchlist rows:

```text
{int(realized['promising_watchlist'].sum()) if not realized.empty else 0}
```

Promising proxy rows:

```text
{int(proxy['promising_watchlist'].sum()) if not proxy.empty else 0}
```

Top realized rows:

```text
{top_realized[cols].to_string(index=False) if not top_realized.empty else 'none'}
```

Top proxy rows:

```text
{top_proxy[cols].to_string(index=False) if not top_proxy.empty else 'none'}
```

Interpretation:

Realized rows can reject/promote current implementations. Proxy rows only say
which management ideas deserve a true 1m path replay.
"""
    (base_dir / "short_enhancement_strategy_screen.md").write_text(text, encoding="utf-8")


def build(large_dir: Path, failed_dir: Path) -> None:
    local = _proxy_cols(_load_local(large_dir))
    structural = _proxy_cols(_load_structural(failed_dir))
    local_screen = _screen(
        local,
        min_rows=LOCAL_MIN_ROWS,
        filters=_filter_defs_local(local),
        group_cols=["candidate_desc", "stop_model", "exit_model"],
        source="large_runner_local_high",
    )
    structural_screen = _screen(
        structural,
        min_rows=STRUCT_MIN_ROWS,
        filters=_filter_defs_struct(structural),
        group_cols=["family", "stop_model", "policy"],
        source="failed_pump_structural",
    )
    combined = pd.concat([local_screen, structural_screen], ignore_index=True)
    combined = combined.sort_values(["strict_pass", "evidence_type", "balance_score", "sum_r"], ascending=[False, True, False, False])
    out_dir = large_dir
    combined.to_csv(out_dir / "short_enhancement_strategy_screen.csv", index=False)
    local_screen.to_csv(out_dir / "short_enhancement_strategy_screen_local.csv", index=False)
    structural_screen.to_csv(out_dir / "short_enhancement_strategy_screen_structural.csv", index=False)
    _write_report(out_dir, combined)
    print(f"local_screen_rows={len(local_screen)}")
    print(f"structural_screen_rows={len(structural_screen)}")
    print(f"combined_rows={len(combined)}")
    print(f"realized_strict_passes={int(combined[combined.evidence_type.eq('realized')]['strict_pass'].sum())}")
    print(f"realized_promising_watchlist={int(combined[combined.evidence_type.eq('realized')]['promising_watchlist'].sum())}")
    print(f"proxy_promising_watchlist={int(combined[combined.evidence_type.eq('path_required_proxy')]['promising_watchlist'].sum())}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--large-dir", default=".output/results/large_runner_discovery_365d")
    parser.add_argument("--failed-dir", default=".output/results/failed_pump_short_research_365d")
    args = parser.parse_args()
    build(Path(args.large_dir), Path(args.failed_dir))


if __name__ == "__main__":
    main()
