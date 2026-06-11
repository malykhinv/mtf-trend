"""Replay structural short exits that do not take profit before 0.75R.

This is a postprocess over existing failed-pump short artifacts and 1m cache.
It does not launch a new discovery run.  The goal is to test whether the
promising failed-retest / lower-high shorts remain useful when early 0.25R/0.5R
profit taking is disallowed, and to compare first-minute path/flow features for
winning versus losing trades.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe
from research_tools.session_edge_workbench import _top_remove_pct_to_negative
from research_tools.short_enhancement_strategy_screen import _max_drawdown


MINUTE_MS = 60_000
MAX_HOLD_MINUTES = 60
FEE_RATE = 0.0004
EXIT_SLIPPAGE_PCT = 0.0005
TRAIL_LOOKBACK = 5
STRUCTURAL_STOP_BUFFER_PCT = 0.0005

POLICIES = (
    "tp075_full",
    "tp075_half_trail",
    "tp075_half_be_trail",
    "tp1_full",
    "tp1_be075",
    "tp1_half075_be_trail",
    "tp075_full_time10_no_mfe025",
    "tp075_full_time15_no_mfe05",
)

BASE_POLICIES_FOR_UNIQUE_ROWS = ("full075", "full1", "half075_trail", "half1_trail")


def _short_leg_r(entry_price: float, exit_price: float, fraction: float, risk_abs: float) -> float:
    pnl = fraction * (entry_price - exit_price)
    fees = fraction * FEE_RATE * (entry_price + exit_price)
    return (pnl - fees) / risk_abs


def _num(value: object, default: float = np.nan) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def _top1_share(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    total = float(vals.sum()) if not vals.empty else 0.0
    if total <= 0:
        return np.nan
    return float(vals.max() / total)


def _window_features(path: pd.DataFrame, *, entry_price: float, risk_abs: float, minutes: int) -> dict[str, float]:
    win = path.iloc[: max(0, int(minutes))]
    prefix = f"m{minutes}"
    if win.empty or risk_abs <= 0:
        return {
            f"{prefix}_mfe_r": np.nan,
            f"{prefix}_mae_r": np.nan,
            f"{prefix}_close_r": np.nan,
            f"{prefix}_red_close_share": np.nan,
            f"{prefix}_quote_sum": np.nan,
            f"{prefix}_trade_sum": np.nan,
            f"{prefix}_taker_buy_share": np.nan,
            f"{prefix}_quote_top1_share": np.nan,
            f"{prefix}_trade_top1_share": np.nan,
            f"{prefix}_close_near_low_share": np.nan,
        }
    high = pd.to_numeric(win["high"], errors="coerce")
    low = pd.to_numeric(win["low"], errors="coerce")
    close = pd.to_numeric(win["close"], errors="coerce")
    open_ = pd.to_numeric(win["open"], errors="coerce")
    quote = pd.to_numeric(win.get("quote_volume", pd.Series(index=win.index)), errors="coerce").fillna(0.0)
    trades = pd.to_numeric(win.get("number_of_trades", pd.Series(index=win.index)), errors="coerce").fillna(0.0)
    taker_quote = pd.to_numeric(win.get("taker_buy_quote_volume", pd.Series(index=win.index)), errors="coerce").fillna(0.0)
    candle_range = (high - low).replace(0.0, np.nan)
    close_pos = ((close - low) / candle_range).replace([np.inf, -np.inf], np.nan)
    return {
        f"{prefix}_mfe_r": float(((entry_price - low.min()) / risk_abs)) if low.notna().any() else np.nan,
        f"{prefix}_mae_r": float(((high.max() - entry_price) / risk_abs)) if high.notna().any() else np.nan,
        f"{prefix}_close_r": float((entry_price - close.iloc[-1]) / risk_abs) if close.notna().any() else np.nan,
        f"{prefix}_red_close_share": float((close < open_).mean()) if close.notna().any() and open_.notna().any() else np.nan,
        f"{prefix}_quote_sum": float(quote.sum()),
        f"{prefix}_trade_sum": float(trades.sum()),
        f"{prefix}_taker_buy_share": float(taker_quote.sum() / quote.sum()) if quote.sum() > 0 else np.nan,
        f"{prefix}_quote_top1_share": _top1_share(quote),
        f"{prefix}_trade_top1_share": _top1_share(trades),
        f"{prefix}_close_near_low_share": float((close_pos <= 0.33).mean()) if close_pos.notna().any() else np.nan,
    }


def _first_candle_features(path: pd.DataFrame, *, entry_price: float, risk_abs: float, stop: float) -> dict[str, float]:
    if path.empty:
        return {}
    c = path.iloc[0]
    o = _num(c.get("open"))
    h = _num(c.get("high"))
    l = _num(c.get("low"))
    close = _num(c.get("close"))
    quote = _num(c.get("quote_volume"), 0.0)
    taker = _num(c.get("taker_buy_quote_volume"), 0.0)
    trades = _num(c.get("number_of_trades"), 0.0)
    rng = h - l if math.isfinite(h) and math.isfinite(l) else np.nan
    return {
        "m1_0_red_close": float(close < o) if math.isfinite(close) and math.isfinite(o) else np.nan,
        "m1_0_close_r": (entry_price - close) / risk_abs if risk_abs > 0 and math.isfinite(close) else np.nan,
        "m1_0_mfe_r": (entry_price - l) / risk_abs if risk_abs > 0 and math.isfinite(l) else np.nan,
        "m1_0_mae_r": (h - entry_price) / risk_abs if risk_abs > 0 and math.isfinite(h) else np.nan,
        "m1_0_close_position": (close - l) / rng if math.isfinite(rng) and rng > 0 and math.isfinite(close) else np.nan,
        "m1_0_high_to_stop_r": (stop - h) / risk_abs if risk_abs > 0 and math.isfinite(h) else np.nan,
        "m1_0_taker_buy_share": taker / quote if quote > 0 else np.nan,
        "m1_0_quote_volume": quote,
        "m1_0_number_of_trades": trades,
    }


def _simulate(row: pd.Series, path: pd.DataFrame, policy: str) -> dict[str, object]:
    entry_ts = int(row["entry_timestamp_ms"])
    entry_price = _num(row["entry_price"])
    initial_stop = _num(row["initial_stop"])
    risk_abs = initial_stop - entry_price
    if path.empty or not math.isfinite(entry_price) or not math.isfinite(initial_stop) or risk_abs <= 0:
        return {
            "status": "skipped",
            "skip_reason": "invalid_path_or_risk",
            "policy": policy,
            "net_r": np.nan,
        }

    target075 = entry_price - 0.75 * risk_abs
    target1 = entry_price - 1.00 * risk_abs
    target = target1 if policy in {"tp1_full", "tp1_be075", "tp1_half075_be_trail"} else target075
    stop = initial_stop
    open_fraction = 1.0
    realized_r = 0.0
    partial_taken = False
    be_armed = False
    trail_updates = 0
    mfe_r = 0.0
    mae_r = 0.0
    time_to_075 = np.nan
    time_to_1 = np.nan
    exit_ts = int(path.iloc[-1]["timestamp"])
    exit_price = _num(path.iloc[-1].get("close")) * (1.0 + EXIT_SLIPPAGE_PCT)
    exit_reason = "horizon"
    stop_first_conservative = False

    for i, (_, candle) in enumerate(path.iterrows()):
        candle_ts = int(candle["timestamp"])
        h = _num(candle.get("high"))
        l = _num(candle.get("low"))
        c = _num(candle.get("close"))
        if not all(math.isfinite(v) and v > 0 for v in (h, l, c)):
            continue
        mfe_r = max(mfe_r, (entry_price - l) / risk_abs)
        mae_r = max(mae_r, (h - entry_price) / risk_abs)
        if math.isnan(time_to_075) and l <= target075:
            time_to_075 = (candle_ts - entry_ts) / MINUTE_MS
        if math.isnan(time_to_1) and l <= target1:
            time_to_1 = (candle_ts - entry_ts) / MINUTE_MS

        if h >= stop:
            stop_first_conservative = bool(l <= target and not partial_taken)
            fill = stop * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, open_fraction, risk_abs)
            exit_ts = candle_ts
            exit_price = fill
            exit_reason = "stop" if trail_updates <= 0 and not be_armed else ("be_stop" if be_armed else "trail_stop")
            open_fraction = 0.0
            break

        if policy in {"tp1_be075", "tp1_half075_be_trail"} and not be_armed and l <= target075:
            stop = min(stop, entry_price)
            be_armed = True

        if policy in {"tp075_half_trail", "tp075_half_be_trail", "tp1_half075_be_trail"} and not partial_taken and l <= target075:
            fill = target075 * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, 0.50, risk_abs)
            open_fraction = 0.50
            partial_taken = True
            if policy in {"tp075_half_be_trail", "tp1_half075_be_trail"}:
                stop = min(stop, entry_price)
                be_armed = True
        elif policy not in {"tp075_half_trail", "tp075_half_be_trail", "tp1_half075_be_trail"} and l <= target:
            fill = target * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, open_fraction, risk_abs)
            exit_ts = candle_ts
            exit_price = fill
            exit_reason = "target_full"
            open_fraction = 0.0
            break

        if policy == "tp075_full_time10_no_mfe025" and i + 1 >= 10 and mfe_r < 0.25:
            fill = c * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, open_fraction, risk_abs)
            exit_ts = candle_ts
            exit_price = fill
            exit_reason = "time_exit_no_mfe025_10m"
            open_fraction = 0.0
            break
        if policy == "tp075_full_time15_no_mfe05" and i + 1 >= 15 and mfe_r < 0.50:
            fill = c * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, open_fraction, risk_abs)
            exit_ts = candle_ts
            exit_price = fill
            exit_reason = "time_exit_no_mfe05_15m"
            open_fraction = 0.0
            break

        if open_fraction > 0 and i >= TRAIL_LOOKBACK - 1 and policy in {"tp075_half_trail", "tp075_half_be_trail", "tp1_half075_be_trail"}:
            recent = path.iloc[max(0, i - TRAIL_LOOKBACK + 1) : i + 1]
            candidate = pd.to_numeric(recent["high"], errors="coerce").max() * (1.0 + STRUCTURAL_STOP_BUFFER_PCT)
            if math.isfinite(candidate) and candidate > c and candidate < stop:
                stop = candidate
                trail_updates += 1

    if open_fraction > 0:
        final_close = _num(path.iloc[-1].get("close"))
        if math.isfinite(final_close) and final_close > 0:
            fill = final_close * (1.0 + EXIT_SLIPPAGE_PCT)
            realized_r += _short_leg_r(entry_price, fill, open_fraction, risk_abs)
            exit_ts = int(path.iloc[-1]["timestamp"])
            exit_price = fill
            exit_reason = "horizon_after_partial" if partial_taken else "horizon"

    out = {
        "status": "closed",
        "policy": policy,
        "symbol": row["symbol"],
        "signal_id": row["signal_id"],
        "setup_id": row["setup_id"],
        "family": row["family"],
        "stop_model": row["stop_model"],
        "entry_timestamp_ms": entry_ts,
        "entry_time_utc": pd.to_datetime(entry_ts, unit="ms", utc=True).isoformat(),
        "date": pd.to_datetime(entry_ts, unit="ms", utc=True).normalize().date().isoformat(),
        "month": pd.to_datetime(entry_ts, unit="ms", utc=True).strftime("%Y-%m"),
        "session_bucket": row.get("session_bucket", ""),
        "signal_variant": row.get("signal_variant", ""),
        "pump_tier": row.get("pump_tier", ""),
        "entry_price": entry_price,
        "initial_stop": initial_stop,
        "initial_risk_pct": risk_abs / entry_price,
        "net_r": realized_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "time_to_075r_min": time_to_075,
        "time_to_1r_min": time_to_1,
        "exit_timestamp_ms": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "hold_minutes": max(0.0, (exit_ts - entry_ts) / MINUTE_MS),
        "partial_taken": partial_taken,
        "be_armed": be_armed,
        "trail_updates": trail_updates,
        "stop_first_conservative": stop_first_conservative,
        "structural_break_depth_pct": row.get("structural_break_depth_pct", np.nan),
        "failed_retest_distance_pct": row.get("failed_retest_distance_pct", np.nan),
        "minutes_from_seed_to_break": row.get("minutes_from_seed_to_break", np.nan),
        "post_dist": row.get("post_dist", ""),
        "seed_dist": row.get("seed_dist", ""),
    }
    out.update(_first_candle_features(path, entry_price=entry_price, risk_abs=risk_abs, stop=initial_stop))
    for minutes in (3, 5, 10, 15):
        out.update(_window_features(path, entry_price=entry_price, risk_abs=risk_abs, minutes=minutes))
    return out


def _summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        vals = pd.to_numeric(group["net_r"], errors="coerce").dropna()
        if vals.empty:
            continue
        daily = group.groupby("date")["net_r"].sum()
        monthly = group.groupby("month")["net_r"].sum()
        trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
        symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(group.groupby("symbol")["net_r"].sum())
        row = {
            "trades": int(len(group)),
            "symbols": int(group["symbol"].nunique()),
            "days": int(group["date"].nunique()),
            "sessions": int(group["session_bucket"].nunique()),
            "avg_r": float(vals.mean()),
            "median_r": float(vals.median()),
            "sum_r": float(vals.sum()),
            "win_rate": float((vals > 0).mean()),
            "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
            "positive_month_rate": float((monthly > 0).mean()) if len(monthly) else np.nan,
            "top_trade_independence_pct": trade_pct,
            "removed_top_trades_to_negative": removed_trades,
            "winning_trades": winning_trades,
            "top_symbol_independence_pct": symbol_pct,
            "removed_top_symbols_to_negative": removed_symbols,
            "winning_symbols": winning_symbols,
            "max_drawdown_r": _max_drawdown(group.sort_values("entry_timestamp_ms")["net_r"]),
            "median_mfe_r": float(pd.to_numeric(group["mfe_r"], errors="coerce").median()),
            "median_mae_r": float(pd.to_numeric(group["mae_r"], errors="coerce").median()),
            "median_time_to_075r_min": float(pd.to_numeric(group["time_to_075r_min"], errors="coerce").median()),
        }
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        row.update(dict(zip(group_cols, key_tuple)))
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["avg_r", "median_r", "trades"], ascending=[False, False, False])


def _micro_diff(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    feature_cols = [
        "m1_0_close_r",
        "m1_0_mfe_r",
        "m1_0_mae_r",
        "m1_0_close_position",
        "m1_0_high_to_stop_r",
        "m1_0_taker_buy_share",
        "m3_mfe_r",
        "m3_mae_r",
        "m3_close_r",
        "m3_red_close_share",
        "m3_taker_buy_share",
        "m3_quote_top1_share",
        "m3_trade_top1_share",
        "m5_mfe_r",
        "m5_mae_r",
        "m5_close_r",
        "m5_red_close_share",
        "m5_taker_buy_share",
        "m10_mfe_r",
        "m10_mae_r",
        "m10_close_r",
        "m10_red_close_share",
        "m10_taker_buy_share",
        "m15_mfe_r",
        "m15_mae_r",
        "m15_close_r",
        "m15_red_close_share",
        "m15_taker_buy_share",
        "structural_break_depth_pct",
        "failed_retest_distance_pct",
        "minutes_from_seed_to_break",
        "initial_risk_pct",
    ]
    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        wins = group[pd.to_numeric(group["net_r"], errors="coerce") > 0]
        losses = group[pd.to_numeric(group["net_r"], errors="coerce") <= 0]
        if len(wins) < 10 or len(losses) < 10:
            continue
        for feature in feature_cols:
            w = pd.to_numeric(wins[feature], errors="coerce").dropna()
            l = pd.to_numeric(losses[feature], errors="coerce").dropna()
            if len(w) < 10 or len(l) < 10:
                continue
            pooled = pd.to_numeric(group[feature], errors="coerce").dropna()
            spread = float(pooled.quantile(0.75) - pooled.quantile(0.25)) if len(pooled) else np.nan
            delta = float(w.median() - l.median())
            row = {
                "feature": feature,
                "wins": int(len(w)),
                "losses": int(len(l)),
                "win_median": float(w.median()),
                "loss_median": float(l.median()),
                "median_delta_win_minus_loss": delta,
                "iqr_scaled_delta": delta / spread if math.isfinite(spread) and spread > 0 else np.nan,
            }
            key_tuple = keys if isinstance(keys, tuple) else (keys,)
            row.update(dict(zip(group_cols, key_tuple)))
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("iqr_scaled_delta", key=lambda s: s.abs(), ascending=False)


DEFAULT_FAMILIES = (
    "A_post_oneprint_break1p5_retest0p8",
    "fast_deep_break_taker_above",
    "deep_break_retest0p8",
    "deep_break_retest0p4",
)


def _read_base_rows(path: Path, *, families: tuple[str, ...]) -> pd.DataFrame:
    cols = [
        "family",
        "stop_model",
        "policy",
        "symbol",
        "signal_id",
        "setup_id",
        "entry_timestamp_ms",
        "date",
        "signal_variant",
        "pump_tier",
        "session_bucket",
        "structural_break_depth_pct",
        "failed_retest_distance_pct",
        "minutes_from_seed_to_break",
        "post_dist",
        "seed_dist",
        "status",
        "entry_price",
        "initial_stop",
    ]
    df = pd.read_csv(path, usecols=cols)
    df = df[df["status"].eq("closed") & df["policy"].isin(BASE_POLICIES_FOR_UNIQUE_ROWS)].copy()
    if families:
        df = df[df["family"].isin(families)].copy()
    df["entry_timestamp_ms"] = pd.to_numeric(df["entry_timestamp_ms"], errors="coerce")
    df = df.dropna(subset=["entry_timestamp_ms", "entry_price", "initial_stop"])
    df["entry_timestamp_ms"] = df["entry_timestamp_ms"].astype("int64")
    # One row per signal/family/stop. Existing policy rows duplicate the same
    # entry and stop; replay policies are generated below.
    return (
        df.sort_values(["entry_timestamp_ms", "family", "stop_model", "policy"])
        .drop_duplicates(["family", "stop_model", "signal_id"], keep="first")
        .reset_index(drop=True)
    )


def run(*, failed_dir: Path, cache_dir: Path, families: tuple[str, ...]) -> dict[str, object]:
    trades_path = failed_dir / "failed_pump_structural_management_replay_trades.csv"
    base = _read_base_rows(trades_path, families=families)
    storage = ParquetStorage(cache_dir)
    rows: list[dict[str, object]] = []
    for symbol, group in base.groupby("symbol", sort=False):
        start = int(group["entry_timestamp_ms"].min())
        end = int(group["entry_timestamp_ms"].max()) + MAX_HOLD_MINUTES * MINUTE_MS
        loaded = storage.load_window_result(str(symbol), Timeframe("1m"), start, end)
        if not loaded.ok or loaded.frame.empty:
            for _, row in group.iterrows():
                for policy in POLICIES:
                    rows.append(
                        {
                            "status": "skipped",
                            "skip_reason": loaded.reason,
                            "policy": policy,
                            "symbol": symbol,
                            "signal_id": row["signal_id"],
                            "family": row["family"],
                            "stop_model": row["stop_model"],
                            "entry_timestamp_ms": row["entry_timestamp_ms"],
                            "net_r": np.nan,
                        }
                    )
            continue
        frame = loaded.frame.sort_values("timestamp").reset_index(drop=True)
        ts = pd.to_numeric(frame["timestamp"], errors="coerce")
        for _, row in group.iterrows():
            entry_ts = int(row["entry_timestamp_ms"])
            path = frame.loc[(ts >= entry_ts) & (ts <= entry_ts + MAX_HOLD_MINUTES * MINUTE_MS)].copy()
            for policy in POLICIES:
                rows.append(_simulate(row, path, policy))

    out = pd.DataFrame(rows)
    out_dir = failed_dir
    trades_out = out_dir / "short_structural_075_path_replay_trades.csv"
    summary_out = out_dir / "short_structural_075_path_replay_summary.csv"
    micro_out = out_dir / "short_structural_075_path_micro_win_loss.csv"
    report_out = out_dir / "short_structural_075_path_replay.md"
    out.to_csv(trades_out, index=False)
    closed = out[out["status"].eq("closed")].copy()
    summary = _summarize(closed, ["family", "stop_model", "policy"])
    summary.to_csv(summary_out, index=False)
    micro = _micro_diff(
        closed[closed["family"].isin(["A_post_oneprint_break1p5_retest0p8", "fast_deep_break_taker_above", "deep_break_retest0p8", "deep_break_retest0p4"])],
        ["family", "policy"],
    )
    micro.to_csv(micro_out, index=False)

    top = summary.head(20)
    policy_summary = _summarize(closed, ["policy"])
    micro_top = micro.head(30)
    report = [
        "# Structural 0.75R Path Replay",
        "",
        "Profit exits below 0.75R are intentionally not tested here.",
        "Early exits are allowed only as loss/no-continuation protection.",
        "",
        "Rows:",
        "",
        "```text",
        f"base unique signal/family/stop rows: {len(base)}",
        f"replayed rows: {len(out)}",
        f"closed rows: {len(closed)}",
        "```",
        "",
        "Policy summary:",
        "",
        "```text",
        policy_summary.to_string(index=False) if not policy_summary.empty else "EMPTY",
        "```",
        "",
        "Top family/stop/policy rows:",
        "",
        "```text",
        top.to_string(index=False) if not top.empty else "EMPTY",
        "```",
        "",
        "Top win/loss 1m feature differences:",
        "",
        "```text",
        micro_top.to_string(index=False) if not micro_top.empty else "EMPTY",
        "```",
    ]
    report_out.write_text("\n".join(report) + "\n", encoding="utf-8")
    return {
        "base_rows": len(base),
        "replayed_rows": len(out),
        "closed_rows": len(closed),
        "summary_rows": len(summary),
        "micro_rows": len(micro),
        "trades_out": str(trades_out),
        "summary_out": str(summary_out),
        "micro_out": str(micro_out),
        "report_out": str(report_out),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".output/cache"))
    parser.add_argument(
        "--families",
        nargs="*",
        default=list(DEFAULT_FAMILIES),
        help="Structural families to replay. Use --families with no values to run all families.",
    )
    args = parser.parse_args()
    result = run(failed_dir=args.failed_dir, cache_dir=args.cache_dir, families=tuple(args.families or ()))
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
