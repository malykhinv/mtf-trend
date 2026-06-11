"""Session-aware timing deep dive for short fade research.

This is a postprocess over existing large-runner and failed-pump artifacts. It
does not run discovery. Future fade timing is used only as an evaluation label,
not as an entry filter.
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


def _max_drawdown(vals: pd.Series) -> float:
    arr = pd.to_numeric(vals, errors="coerce").fillna(0.0).to_numpy()
    if len(arr) == 0:
        return np.nan
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def _summary(df: pd.DataFrame, value_col: str = "net_r") -> pd.Series:
    vals = pd.to_numeric(df[value_col], errors="coerce").dropna()
    daily = df.groupby("date")[value_col].sum() if "date" in df else pd.Series(dtype=float)
    trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
    symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(df.groupby("symbol")[value_col].sum())
    return pd.Series(
        {
            "trades": int(len(df)),
            "symbols": int(df["symbol"].nunique()),
            "days": int(df["date"].nunique()) if "date" in df else 0,
            "avg_r": float(vals.mean()) if len(vals) else np.nan,
            "median_r": float(vals.median()) if len(vals) else np.nan,
            "sum_r": float(vals.sum()) if len(vals) else np.nan,
            "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
            "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
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


def _load_large(large_dir: Path) -> pd.DataFrame:
    trades = pd.read_csv(large_dir / "large_runner_local_high_structural_exit_replay_trades.csv")
    setup_cols = [
        "symbol",
        "seed_close_ms",
        "seed_close_utc",
        "session_bucket",
        "outcome_class",
        "future60_low_break_offset_min",
        "runner_high10_hit_offset_min",
        "base_touch_offset_min",
        "deep2_touch_offset_min",
        "close_ret_10m",
        "close_ret_15m",
        "high_ret_15m",
        "wick_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "early_return_pct",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "early_taker_buy_quote_share",
        "m1_taker_buy_quote_share",
        "m1_quote_top1_share",
        "m1_trade_top1_share",
        "m1_last2_quote_share",
        "m1_last2_trade_share",
        "m1_quote_accel_last2_vs_first2",
        "m1_trade_accel_last2_vs_first2",
        "m1_sustain_strict",
        "close15_to_high15_ratio",
    ]
    setups = pd.read_csv(large_dir / "large_runner_session_outcome_research_table.csv", usecols=setup_cols)
    df = trades[trades["status"].eq("closed")].merge(
        setups, on=["symbol", "seed_close_ms", "seed_close_utc"], how="inner", validate="many_to_one"
    )
    df["entry_timestamp_ms"] = pd.to_numeric(df["seed_close_ms"], errors="coerce") + (
        pd.to_numeric(df["delay_min"], errors="coerce").fillna(0) * 60_000
    )
    entry_dt = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce")
    df["date"] = entry_dt.dt.normalize()
    df["month"] = entry_dt.dt.to_period("M").astype(str)
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    df["cost10_r"] = pd.to_numeric(df["net_r"], errors="coerce") - (0.001 / risk.replace(0, np.nan))

    low_offset = pd.to_numeric(df["future60_low_break_offset_min"], errors="coerce")
    delay = pd.to_numeric(df["delay_min"], errors="coerce")
    rel = low_offset - delay
    timing = pd.Series("no_low_break", index=df.index)
    timing[low_offset.notna() & rel.lt(0)] = "already_faded_before_entry"
    timing[low_offset.notna() & rel.ge(0) & rel.le(5)] = "fade_0_5_after_entry"
    timing[low_offset.notna() & rel.gt(5) & rel.le(10)] = "fade_5_10_after_entry"
    timing[low_offset.notna() & rel.gt(10) & rel.le(20)] = "fade_10_20_after_entry"
    timing[low_offset.notna() & rel.gt(20)] = "fade_late_20plus"
    df["timing_bucket"] = timing
    df["fresh_fade_0_10"] = timing.isin(["fade_0_5_after_entry", "fade_5_10_after_entry"])
    df["late_or_no_fade"] = timing.isin(["fade_10_20_after_entry", "fade_late_20plus", "no_low_break"])
    df["already_faded"] = timing.eq("already_faded_before_entry")
    df["base_touched_before_entry"] = pd.to_numeric(df["base_touch_offset_min"], errors="coerce").le(delay)
    df["deep2_touched_before_entry"] = pd.to_numeric(df["deep2_touch_offset_min"], errors="coerce").le(delay)
    df["fader_classifier_15"] = (
        delay.ge(15)
        & pd.to_numeric(df["close_ret_15m"], errors="coerce").le(0.0339)
        & pd.to_numeric(df["pre60_range_pct"], errors="coerce").ge(0.10)
    )
    df["entry_known_desc"] = _entry_known_desc(df)
    return df[df["entry_known_desc"]].copy()


def _entry_known_desc(df: pd.DataFrame) -> pd.Series:
    desc = df["candidate_desc"].astype(str)
    delay = pd.to_numeric(df["delay_min"], errors="coerce")
    requires_15 = desc.str.contains("close15", case=False, na=False)
    requires_10 = desc.str.contains("close10", case=False, na=False)
    return (~requires_15 | delay.ge(15)) & (~requires_10 | delay.ge(10))


def _session_timing(df: pd.DataFrame, split_date: pd.Timestamp) -> pd.DataFrame:
    rows = []
    oos = df[df["date"] >= split_date].copy()
    for keys, group in oos.groupby(["session_bucket", "fader_classifier_15", "timing_bucket"], dropna=False):
        session, classifier, timing = keys
        s = _summary(group, "net_r")
        c = _summary(group, "cost10_r")
        s["session_bucket"] = session
        s["fader_classifier_15"] = bool(classifier)
        s["timing_bucket"] = timing
        s["cost10_avg_r"] = c["avg_r"]
        s["fader_rate"] = float(group["outcome_class"].eq("fast_base_fader").mean())
        s["runner_rate"] = float(group["outcome_class"].eq("runner10").mean())
        s["base_touched_before_entry_rate"] = float(group["base_touched_before_entry"].fillna(False).mean())
        rows.append(s)
    return pd.DataFrame(rows).sort_values(["session_bucket", "fader_classifier_15", "trades"], ascending=[True, False, False])


def _setup_session_classifier(df: pd.DataFrame, split_date: pd.Timestamp) -> pd.DataFrame:
    setup = (
        df.drop_duplicates(["symbol", "seed_close_ms"])
        .copy()
        .assign(setup_date=lambda x: pd.to_datetime(x["seed_close_utc"], utc=True, errors="coerce").dt.normalize())
    )
    setup = setup[setup["setup_date"] >= split_date].copy()
    setup["close15_band"] = pd.cut(
        pd.to_numeric(setup["close_ret_15m"], errors="coerce"),
        bins=[-np.inf, 0.0, 0.0339, 0.0563, 0.081, 0.12, np.inf],
        labels=["<=0", "0-3.39", "3.39-5.63", "5.63-8.10", "8.10-12", ">=12"],
    )
    rows = []
    for keys, group in setup.groupby(["session_bucket", "close15_band"], observed=False, dropna=False):
        session, band = keys
        if len(group) < 15:
            continue
        rows.append(
            {
                "session_bucket": session,
                "close15_band": str(band),
                "setups": int(len(group)),
                "symbols": int(group["symbol"].nunique()),
                "days": int(group["setup_date"].nunique()),
                "fader_rate": float(group["outcome_class"].eq("fast_base_fader").mean()),
                "runner_rate": float(group["outcome_class"].eq("runner10").mean()),
                "static_or_other_rate": float(
                    group["outcome_class"].isin(["upper_static", "other_no_direction", "late_base_fader"]).mean()
                ),
                "base_touch_median_min": float(
                    pd.to_numeric(group["base_touch_offset_min"], errors="coerce").median()
                ),
                "pre60_range_median": float(pd.to_numeric(group["pre60_range_pct"], errors="coerce").median()),
            }
        )
    return pd.DataFrame(rows).sort_values(["session_bucket", "close15_band"])


def _entry_known_filter_defs(df: pd.DataFrame) -> dict[str, pd.Series]:
    delay = pd.to_numeric(df["delay_min"], errors="coerce")
    close15 = pd.to_numeric(df["close_ret_15m"], errors="coerce")
    high15 = pd.to_numeric(df["high_ret_15m"], errors="coerce")
    wick15 = pd.to_numeric(df["wick_ret_15m"], errors="coerce")
    pre60_range = pd.to_numeric(df["pre60_range_pct"], errors="coerce")
    pre60_ret = pd.to_numeric(df["pre60_return_pct"], errors="coerce")
    taker = pd.to_numeric(df["m1_taker_buy_quote_share"], errors="coerce")
    early_taker = pd.to_numeric(df["early_taker_buy_quote_share"], errors="coerce")
    last2_trade = pd.to_numeric(df["m1_last2_trade_share"], errors="coerce")
    top1_trade = pd.to_numeric(df["m1_trade_top1_share"], errors="coerce")
    ratio = pd.to_numeric(df["close15_to_high15_ratio"], errors="coerce")
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    base = delay.ge(15) & close15.le(0.0339) & pre60_range.ge(0.10)
    return {
        "classifier": base,
        "classifier_no_base_touch": base & ~df["base_touched_before_entry"].fillna(False),
        "classifier_no_deep2_touch": base & ~df["deep2_touched_before_entry"].fillna(False),
        "classifier_taker_le50": base & taker.le(0.50),
        "classifier_taker_45_55": base & taker.between(0.45, 0.55, inclusive="both"),
        "classifier_early_taker_le50": base & early_taker.le(0.50),
        "classifier_pre60_down": base & pre60_ret.le(0),
        "classifier_pre60_up": base & pre60_ret.gt(0),
        "classifier_wick15_ge2": base & wick15.ge(0.02),
        "classifier_close15_to_high_le45": base & ratio.le(0.45),
        "classifier_high15_4_8": base & high15.between(0.04, 0.08, inclusive="both"),
        "classifier_risk_2_5": base & risk.between(0.02, 0.05, inclusive="both"),
        "classifier_last2_trade_le45": base & last2_trade.le(0.45),
        "classifier_top1_trade_35_55": base & top1_trade.between(0.35, 0.55, inclusive="both"),
    }


def _entry_known_oos_filters(df: pd.DataFrame, split_date: pd.Timestamp) -> pd.DataFrame:
    rows = []
    oos = df[df["date"] >= split_date].copy()
    filters = _entry_known_filter_defs(oos)
    group_cols = ["session_bucket", "candidate_desc", "stop_model", "exit_model"]
    for filter_name, mask in filters.items():
        filtered = oos[mask.fillna(False)].copy()
        if len(filtered) < 30:
            continue
        for keys, group in filtered.groupby(group_cols, dropna=False):
            if len(group) < 30:
                continue
            s = _summary(group, "net_r")
            c = _summary(group, "cost10_r")
            s["cost10_avg_r"] = c["avg_r"]
            s["filter_name"] = filter_name
            for col, value in zip(group_cols, keys if isinstance(keys, tuple) else (keys,)):
                s[col] = value
            s["fresh_fade_0_10_rate"] = float(group["fresh_fade_0_10"].mean())
            s["already_faded_rate"] = float(group["already_faded"].mean())
            s["late_or_no_fade_rate"] = float(group["late_or_no_fade"].mean())
            s["fader_rate"] = float(group["outcome_class"].eq("fast_base_fader").mean())
            s["runner_rate"] = float(group["outcome_class"].eq("runner10").mean())
            s["promotion_candidate"] = bool(
                s["trades"] >= 50
                and s["avg_r"] > 0
                and s["median_r"] > 0
                and s["cost10_avg_r"] > 0
                and s["top_trade_independence_pct"] >= 0.25
                and s["positive_day_rate"] >= 0.50
            )
            rows.append(s)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["promotion_candidate", "cost10_avg_r", "median_r", "trades"],
        ascending=[False, False, False, False],
    )


def _failed_frontier_by_session(failed_dir: Path) -> pd.DataFrame:
    path = failed_dir / "short_frontier_oos_trades.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["portfolio"].eq("balanced_frontier")].copy()
    if df.empty:
        return pd.DataFrame()
    df["net_r"] = pd.to_numeric(df["_final_r"], errors="coerce")
    risk = pd.to_numeric(df["initial_risk_pct"], errors="coerce")
    df["cost10_r"] = df["net_r"] - (0.001 / risk.replace(0, np.nan))
    df["entry_timestamp_ms"] = pd.to_numeric(df["entry_timestamp_ms"], errors="coerce")
    df["date"] = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce").dt.normalize()
    rows = []
    for keys, group in df.groupby(["session_bucket", "sleeve"], dropna=False):
        session, sleeve = keys
        s = _summary(group, "net_r")
        c = _summary(group, "cost10_r")
        s["session_bucket"] = session
        s["sleeve"] = sleeve
        s["cost10_avg_r"] = c["avg_r"]
        rows.append(s)
    out = pd.DataFrame(rows)
    return out.sort_values(["sum_r"], ascending=False)


def _write_report(
    out_dir: Path,
    session_timing: pd.DataFrame,
    setup_bands: pd.DataFrame,
    filter_rows: pd.DataFrame,
    failed_sessions: pd.DataFrame,
) -> None:
    timing_cols = [
        "session_bucket",
        "fader_classifier_15",
        "timing_bucket",
        "trades",
        "avg_r",
        "median_r",
        "win_rate",
        "cost10_avg_r",
        "fader_rate",
        "base_touched_before_entry_rate",
    ]
    band_cols = [
        "session_bucket",
        "close15_band",
        "setups",
        "fader_rate",
        "runner_rate",
        "static_or_other_rate",
        "base_touch_median_min",
    ]
    filter_cols = [
        "filter_name",
        "session_bucket",
        "candidate_desc",
        "stop_model",
        "exit_model",
        "trades",
        "avg_r",
        "median_r",
        "cost10_avg_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "fresh_fade_0_10_rate",
        "already_faded_rate",
        "late_or_no_fade_rate",
        "promotion_candidate",
    ]
    failed_cols = [
        "session_bucket",
        "sleeve",
        "trades",
        "avg_r",
        "median_r",
        "sum_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "cost10_avg_r",
    ]
    text = f"""# Session Timing Deep Dive

Status: exploratory postprocess on existing artifacts.

Important limitation:

```text
future60_low_break_offset_min is used only as an evaluation label.
It is not an entry feature.
```

## Setup-Level Close15 Bands By Session

```text
{setup_bands[band_cols].to_string(index=False) if not setup_bands.empty else 'none'}
```

## Large-Runner Replay Timing By Session

```text
{session_timing[timing_cols].to_string(index=False) if not session_timing.empty else 'none'}
```

## Best Entry-Known OOS Slices

```text
{filter_rows.head(30)[filter_cols].to_string(index=False) if not filter_rows.empty else 'none'}
```

## Failed-Pump Balanced Frontier By Session

```text
{failed_sessions[failed_cols].to_string(index=False) if not failed_sessions.empty else 'none'}
```

## Interpretation

The session effect is mostly a timing effect:

```text
Fader classifier -> useful context.
Already faded before entry -> bad short despite high fader rate.
Fresh fade after entry -> good, but current entry-known proxies do not isolate
it robustly enough.
```

Therefore, session filters should not be treated as magic alpha. They should
change the required trigger:

```text
Europe/off-session weak close15: require fresh failed retest and no pre-entry
base touch.

US session: strong close15 is a hard no-short/runner veto; weak close15 still
needs structure because US squeezes can re-accelerate.

Asia: weak close15 can fade, but samples are more tail-sensitive, so require
tighter risk and cleaner lower-high structure.
```
"""
    (out_dir / "short_session_timing_deep_dive.md").write_text(text, encoding="utf-8")


def build(large_dir: Path, failed_dir: Path, split_date_arg: str) -> None:
    split_date = pd.Timestamp(split_date_arg, tz="UTC")
    large = _load_large(large_dir)
    session_timing = _session_timing(large, split_date)
    setup_bands = _setup_session_classifier(large, split_date)
    filter_rows = _entry_known_oos_filters(large, split_date)
    failed_sessions = _failed_frontier_by_session(failed_dir)

    session_timing.to_csv(large_dir / "short_session_timing_by_session.csv", index=False)
    setup_bands.to_csv(large_dir / "short_session_close15_bands.csv", index=False)
    filter_rows.to_csv(large_dir / "short_session_entry_known_oos_slices.csv", index=False)
    failed_sessions.to_csv(large_dir / "short_failed_frontier_by_session.csv", index=False)
    _write_report(large_dir, session_timing, setup_bands, filter_rows, failed_sessions)

    print(f"session_timing_rows={len(session_timing)}")
    print(f"setup_band_rows={len(setup_bands)}")
    print(f"entry_known_slice_rows={len(filter_rows)}")
    print(f"promotion_candidates={int(filter_rows['promotion_candidate'].sum()) if not filter_rows.empty else 0}")
    print(f"failed_session_rows={len(failed_sessions)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--large-dir", default=".output/results/large_runner_discovery_365d")
    parser.add_argument("--failed-dir", default=".output/results/failed_pump_short_research_365d")
    parser.add_argument("--split-date", default="2025-12-03")
    args = parser.parse_args()
    build(Path(args.large_dir), Path(args.failed_dir), args.split_date)


if __name__ == "__main__":
    main()
