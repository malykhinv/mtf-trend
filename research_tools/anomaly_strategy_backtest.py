"""Research backtest for early anomaly-continuation long entries."""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import numpy as np
import pandas as pd

from research_tools.anomaly_continuation_lab import (
    AnomalyLabConfig,
    build_oi_context_status,
    collect_anomaly_lab_rows,
    _emit_progress,
)

TRADE_SIGNAL_CONTEXT_COLUMNS = (
    "start_trade_count",
    "start_trade_ratio",
    "start_quote_ratio",
    "hold_count_next_n_candles",
    "hold_ratio_next_n_candles",
    "next_n_trade_decay",
    "next_n_quote_decay",
    "price_retention_next_n",
    "new_high_count_next_n",
    "start_verticality_score",
    "start_verticality_path_efficiency",
    "start_verticality_range_efficiency",
    "start_verticality_slope_pct_per_candle",
    "start_verticality_max_retrace_fraction",
    "start_verticality_green_share",
    "flow_taker_buy_status",
    "start_taker_buy_quote_share",
    "baseline_taker_buy_quote_share_median",
    "start_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_mean",
    "next_n_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_decay",
    "start_avg_trade_quote_size",
    "baseline_avg_trade_quote_size_median",
    "start_avg_trade_quote_size_ratio",
    "next_n_avg_trade_quote_size_mean",
    "next_n_avg_trade_quote_size_decay",
    "decision_return_from_start_open",
    "start_close_position_in_range",
    "start_body_to_range",
    "start_upper_wick_to_range",
    "start_lower_wick_to_range",
    "baseline_range_median",
    "baseline_range_pct_median",
    "start_range_ratio_to_baseline",
    "start_range_pct",
    "start_range_pct_ratio_to_baseline",
    "start_quote_per_abs_return",
    "start_trades_per_abs_return",
    "start_quote_ratio_per_abs_return",
    "start_trade_ratio_per_abs_return",
    "post_start_pullback_fraction_of_box",
    "oi_timeframe",
    "oi_status",
    "oi_timestamp_ms",
    "oi_timestamp_utc",
    "oi_age_ms",
    "oi_open_interest",
    "oi_change_1x5m",
    "oi_change_pct_1x5m",
    "oi_change_3x5m",
    "oi_change_pct_3x5m",
    "oi_change_6x5m",
    "oi_change_pct_6x5m",
    "oi_price_interaction_3x5m",
    "oi_change_pct_3x5m_per_decision_return",
)


@dataclass(frozen=True, slots=True)
class AnomalyBacktestConfig:
    lab_config: AnomalyLabConfig
    min_price_retention: float = 0.70
    min_verticality_score: float = 0.25
    min_hold_count: int = 0
    min_oi_change_pct_3x5m: float | None = None
    require_oi_status_ok: bool = False
    max_initial_risk_pct: float = 0.16
    entry_method: str = "market"
    pullback_box_fraction: float = 0.75
    entry_timeout_candles: int = 60
    stop_buffer_range_fraction: float = 0.05
    tp1_r: float = 1.0
    tp1_fraction: float = 0.50
    move_stop_to_breakeven_after_tp1: bool = True
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10
    max_hold_candles: int = 240
    fee_rate: float = 0.0004


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _cache_symbol_dir_name(symbol: str) -> str:
    return quote(symbol, safe="")


def _read_symbol_frame(cache_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    path = cache_dir / _cache_symbol_dir_name(symbol) / timeframe / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path)
    frame.sort_values("timestamp", inplace=True)
    frame.drop_duplicates("timestamp", keep="last", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def build_anomaly_signals(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    required = {
        "price_retention_next_n",
        "start_verticality_score",
        "hold_count_next_n_candles",
        "decision_close",
        "decision_box_low",
        "decision_box_high",
        "decision_box_range",
        "timestamp_ms",
        "decision_timestamp_ms",
    }
    if config.min_oi_change_pct_3x5m is not None:
        required.add("oi_change_pct_3x5m")
    if config.require_oi_status_ok:
        required.add("oi_status")
    missing = required.difference(candidates.columns)
    if missing:
        raise ValueError(f"candidates missing required columns: {sorted(missing)}")

    signals = candidates.copy()
    signals["entry_price"] = signals["decision_close"].astype(float)
    box_low = signals["decision_box_low"].astype(float)
    box_range = signals["decision_box_range"].astype(float).clip(lower=0.0)
    signals["initial_stop_at_decision"] = box_low - config.stop_buffer_range_fraction * box_range
    signals["initial_risk_pct_at_decision"] = (
        (signals["entry_price"] - signals["initial_stop_at_decision"]) / signals["entry_price"]
    )
    mask = (
        signals["price_retention_next_n"].astype(float).ge(config.min_price_retention)
        & signals["start_verticality_score"].astype(float).ge(config.min_verticality_score)
        & signals["hold_count_next_n_candles"].astype(float).ge(config.min_hold_count)
        & signals["initial_risk_pct_at_decision"].gt(0.0)
        & signals["initial_risk_pct_at_decision"].le(config.max_initial_risk_pct)
    )
    if config.min_oi_change_pct_3x5m is not None:
        mask &= signals["oi_change_pct_3x5m"].astype(float).gt(config.min_oi_change_pct_3x5m)
    if config.require_oi_status_ok:
        mask &= signals["oi_status"].eq("ok")
    signals = signals.loc[mask].copy()
    if signals.empty:
        return signals
    signals.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    signals.reset_index(drop=True, inplace=True)
    return signals


def _resolve_signal_stop(
    frame: pd.DataFrame,
    *,
    anomaly_timestamp_ms: int,
    decision_timestamp_ms: int,
    entry_price: float,
    config: AnomalyBacktestConfig,
) -> tuple[float, float, float]:
    box = frame.loc[
        (frame["timestamp"] >= anomaly_timestamp_ms)
        & (frame["timestamp"] <= decision_timestamp_ms)
    ]
    if box.empty:
        return float("nan"), float("nan"), float("nan")
    box_low = float(box["low"].min())
    box_high = float(box["high"].max())
    box_range = max(box_high - box_low, 0.0)
    initial_stop = box_low - config.stop_buffer_range_fraction * box_range
    initial_risk = entry_price - initial_stop
    return initial_stop, initial_risk, box_range


def _resolve_signal_entry(
    frame: pd.DataFrame,
    *,
    anomaly_timestamp_ms: int,
    decision_timestamp_ms: int,
    decision_close: float,
    config: AnomalyBacktestConfig,
) -> tuple[int, float, float, float, float, float]:
    box = frame.loc[
        (frame["timestamp"] >= anomaly_timestamp_ms)
        & (frame["timestamp"] <= decision_timestamp_ms)
    ]
    if box.empty:
        return decision_timestamp_ms, float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
    box_low = float(box["low"].min())
    box_high = float(box["high"].max())
    box_range = max(box_high - box_low, 0.0)
    initial_stop = box_low - config.stop_buffer_range_fraction * box_range

    if config.entry_method == "market":
        entry_ts = decision_timestamp_ms
        entry_price = decision_close
    else:
        future = frame.loc[frame["timestamp"] > decision_timestamp_ms].head(config.entry_timeout_candles)
        if future.empty:
            return decision_timestamp_ms, float("nan"), initial_stop, float("nan"), box_range, box_high
        if config.entry_method == "break_box_high":
            trigger = box_high
            hit = future.loc[future["high"].astype(float).ge(trigger)]
            if hit.empty:
                return decision_timestamp_ms, float("nan"), initial_stop, float("nan"), box_range, box_high
            entry_ts = int(hit["timestamp"].iloc[0])
            entry_price = trigger
        elif config.entry_method == "pullback_box_fraction":
            trigger = box_low + config.pullback_box_fraction * box_range
            entry_ts = decision_timestamp_ms
            entry_price = float("nan")
            for _, row in future.iterrows():
                low = float(row["low"])
                high = float(row["high"])
                if low <= initial_stop:
                    return decision_timestamp_ms, float("nan"), initial_stop, float("nan"), box_range, box_high
                if low <= trigger <= high:
                    entry_ts = int(row["timestamp"])
                    entry_price = trigger
                    break
            if not np.isfinite(entry_price):
                return decision_timestamp_ms, float("nan"), initial_stop, float("nan"), box_range, box_high
        else:
            raise ValueError(f"unsupported entry_method: {config.entry_method}")

    initial_risk = entry_price - initial_stop
    return entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high


def simulate_long_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    symbol = str(signal["symbol"])
    anomaly_ts = int(signal["timestamp_ms"])
    decision_ts = int(signal["decision_timestamp_ms"])
    decision_close = float(signal["decision_close"])
    entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high = _resolve_signal_entry(
        frame,
        anomaly_timestamp_ms=anomaly_ts,
        decision_timestamp_ms=decision_ts,
        decision_close=decision_close,
        config=config,
    )
    if not np.isfinite(entry_price):
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "entry_trigger_not_reached",
            "entry_method": config.entry_method,
        }
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "invalid_initial_risk",
            "entry_method": config.entry_method,
        }
    initial_risk_pct = initial_risk / entry_price
    if initial_risk_pct > config.max_initial_risk_pct:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "initial_risk_too_wide",
            "initial_risk_pct": initial_risk_pct,
            "entry_method": config.entry_method,
        }

    future = frame.loc[frame["timestamp"] > entry_ts].head(config.max_hold_candles).copy()
    if future.empty:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "no_future_candles",
            "entry_method": config.entry_method,
        }

    tp1_price = entry_price + config.tp1_r * initial_risk
    active_stop = initial_stop
    tp1_hit = False
    remaining_fraction = 1.0
    realized_r = 0.0
    max_high = entry_price
    min_low = entry_price
    exit_reason = "time_exit"
    exit_ts = int(future["timestamp"].iloc[-1])
    exit_price = float(future["close"].iloc[-1])
    trail_stop = float("nan")

    for idx, row in future.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        if low <= active_stop:
            exit_reason = "stop_loss" if not tp1_hit else "trailing_stop"
            exit_ts = candle_ts
            exit_price = active_stop
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            break

        if not tp1_hit and high >= tp1_price:
            tp1_hit = True
            realized_r += config.tp1_fraction * config.tp1_r
            remaining_fraction = 1.0 - config.tp1_fraction
            if config.move_stop_to_breakeven_after_tp1:
                active_stop = max(active_stop, entry_price)

        if tp1_hit:
            prior = frame.loc[
                (frame["timestamp"] < candle_ts)
                & (frame["timestamp"] >= entry_ts)
            ].tail(config.trail_lookback_candles)
            if not prior.empty:
                structural_stop = float(prior["low"].min()) - config.trail_buffer_r * initial_risk
                if structural_stop > active_stop and structural_stop < close:
                    active_stop = structural_stop
                    trail_stop = active_stop

    if remaining_fraction > 0.0:
        realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)

    gross_return = realized_r * initial_risk / entry_price
    round_trip_fee = config.fee_rate * 2.0
    net_return = gross_return - round_trip_fee
    result = {
        "symbol": symbol,
        "status": "closed",
        "anomaly_timestamp_ms": anomaly_ts,
        "anomaly_timestamp_utc": _timestamp_to_utc(anomaly_ts),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "entry_timestamp_ms": entry_ts,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "entry_method": config.entry_method,
        "pullback_box_fraction": config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan,
        "entry_delay_candles": int((entry_ts - decision_ts) / 60_000),
        "entry_price": entry_price,
        "initial_stop": initial_stop,
        "initial_risk": initial_risk,
        "initial_risk_pct": initial_risk_pct,
        "box_range": box_range,
        "box_high": box_high,
        "tp1_price": tp1_price,
        "tp1_hit": tp1_hit,
        "tp1_fraction": config.tp1_fraction,
        "trail_stop_final": trail_stop,
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "holding_candles": int((future["timestamp"] <= exit_ts).sum()),
        "mfe_pct": (max_high - entry_price) / entry_price,
        "mae_pct": (min_low - entry_price) / entry_price,
        "gross_r": realized_r,
        "gross_return": gross_return,
        "fee_rate": config.fee_rate,
        "net_return": net_return,
        "outcome_label": signal.get("outcome_label", ""),
    }
    for column in TRADE_SIGNAL_CONTEXT_COLUMNS:
        result[column] = signal.get(column, "")
    return result


def simulate_anomaly_trades(
    signals: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    progress_label: str | None = None,
    frame_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    last_exit_by_symbol: dict[str, int] = {}
    if frame_cache is None:
        frame_cache = {}
    ordered_signals = signals.sort_values(["decision_timestamp_ms", "symbol"])
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    total_signals = len(ordered_signals)
    for processed_count, (_, signal) in enumerate(ordered_signals.iterrows(), start=1):
        symbol = str(signal["symbol"])
        entry_ts = int(signal["decision_timestamp_ms"])
        if entry_ts <= last_exit_by_symbol.get(symbol, -1):
            rows.append(
                {
                    "symbol": symbol,
                    "entry_timestamp_ms": entry_ts,
                    "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
                    "status": "skipped",
                    "skip_reason": "overlapping_signal",
                }
            )
            continue
        frame = frame_cache.get(symbol)
        if frame is None:
            frame = _read_symbol_frame(config.lab_config.cache_dir, symbol, config.lab_config.timeframe)
            frame_cache[symbol] = frame
        result = simulate_long_signal(frame, signal, config=config)
        rows.append(result)
        if result.get("status") == "closed" and "exit_timestamp_ms" in result:
            last_exit_by_symbol[symbol] = int(result["exit_timestamp_ms"])
        if progress_label is not None:
            current_pct = int(100 * processed_count / total_signals)
            if current_pct >= next_progress_pct or processed_count == total_signals:
                _emit_progress(
                    label=progress_label,
                    done=processed_count,
                    total=total_signals,
                    started_at=progress_started_at,
                )
                next_progress_pct = current_pct + 5
    return pd.DataFrame(rows)


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}])
    net = closed["net_return"].astype(float)
    gross_r = closed["gross_r"].astype(float)
    wins = net > 0
    rows = [
        {"metric": "closed_trades", "value": int(len(closed))},
        {"metric": "symbols", "value": int(closed["symbol"].nunique())},
        {"metric": "win_rate", "value": float(wins.mean())},
        {"metric": "avg_net_return", "value": float(net.mean())},
        {"metric": "median_net_return", "value": float(net.median())},
        {"metric": "sum_net_return", "value": float(net.sum())},
        {"metric": "avg_gross_r", "value": float(gross_r.mean())},
        {"metric": "median_gross_r", "value": float(gross_r.median())},
        {"metric": "tp1_hit_rate", "value": float(closed["tp1_hit"].astype(bool).mean())},
        {"metric": "avg_mfe_pct", "value": float(closed["mfe_pct"].astype(float).mean())},
        {"metric": "avg_mae_pct", "value": float(closed["mae_pct"].astype(float).mean())},
    ]
    by_reason = closed.groupby("exit_reason").size().reset_index(name="count")
    for _, row in by_reason.iterrows():
        rows.append({"metric": f"exit_reason:{row['exit_reason']}", "value": int(row["count"])})
    return pd.DataFrame(rows)


def summarize_trades_by_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame()
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    grouped = (
        closed.assign(win=closed["net_return"].astype(float) > 0)
        .groupby("symbol")
        .agg(
            closed_trades=("symbol", "size"),
            win_rate=("win", "mean"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            sum_net_return=("net_return", "sum"),
            avg_gross_r=("gross_r", "mean"),
            tp1_hit_rate=("tp1_hit", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
    )
    grouped.sort_values("sum_net_return", ascending=False, inplace=True)
    return grouped


def _parse_grid_values(raw: str, *, cast: type[float] | type[int]) -> list[float] | list[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return [cast(item) for item in values]


def summarize_entry_grid_variant(
    trades: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    signal_count: int,
) -> dict[str, object]:
    closed = trades.loc[trades.get("status", pd.Series(dtype=str)).eq("closed")].copy()
    row: dict[str, object] = {
        "entry_method": config.entry_method,
        "pullback_box_fraction": (
            config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan
        ),
        "min_hold_count": config.min_hold_count,
        "min_oi_change_pct_3x5m": config.min_oi_change_pct_3x5m,
        "require_oi_status_ok": config.require_oi_status_ok,
        "signals": int(signal_count),
        "closed_trades": int(len(closed)),
        "skipped_trades": int(len(trades) - len(closed)),
    }
    if closed.empty:
        return row
    net = closed["net_return"].astype(float)
    row.update(
        {
            "symbols": int(closed["symbol"].nunique()),
            "days": int(closed["entry_timestamp_utc"].astype(str).str[:10].nunique()),
            "win_rate": float((net > 0).mean()),
            "avg_net_return": float(net.mean()),
            "median_net_return": float(net.median()),
            "sum_net_return": float(net.sum()),
            "tp1_hit_rate": float(closed["tp1_hit"].astype(bool).mean()),
            "avg_mfe_pct": float(closed["mfe_pct"].astype(float).mean()),
            "avg_mae_pct": float(closed["mae_pct"].astype(float).mean()),
        }
    )
    return row


def run_anomaly_entry_grid(
    candidates: pd.DataFrame,
    base_config: AnomalyBacktestConfig,
    *,
    oi3_values: Iterable[float],
    hold_values: Iterable[int],
    pullback_fractions: Iterable[float],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    variants: list[AnomalyBacktestConfig] = []
    for oi3 in oi3_values:
        for hold in hold_values:
            variants.append(
                replace(
                    base_config,
                    entry_method="market",
                    min_hold_count=int(hold),
                    min_oi_change_pct_3x5m=float(oi3),
                    require_oi_status_ok=True,
                )
            )
            variants.append(
                replace(
                    base_config,
                    entry_method="break_box_high",
                    min_hold_count=int(hold),
                    min_oi_change_pct_3x5m=float(oi3),
                    require_oi_status_ok=True,
                )
            )
            for fraction in pullback_fractions:
                variants.append(
                    replace(
                        base_config,
                        entry_method="pullback_box_fraction",
                        pullback_box_fraction=float(fraction),
                        min_hold_count=int(hold),
                        min_oi_change_pct_3x5m=float(oi3),
                        require_oi_status_ok=True,
                    )
                )

    started_at = time.monotonic()
    frame_cache: dict[str, pd.DataFrame] = {}
    for idx, variant in enumerate(variants, start=1):
        _emit_progress(label="anomaly entry grid", done=idx, total=len(variants), started_at=started_at)
        signals = build_anomaly_signals(candidates, config=variant)
        trades = simulate_anomaly_trades(signals, config=variant, frame_cache=frame_cache)
        rows.append(summarize_entry_grid_variant(trades, config=variant, signal_count=len(signals)))
    result = pd.DataFrame(rows)
    if not result.empty and "avg_net_return" in result.columns:
        result.sort_values(["avg_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return result


def run_anomaly_strategy_backtest(
    config: AnomalyBacktestConfig,
    *,
    symbols: Iterable[str] | None = None,
    run_entry_grid: bool = False,
    grid_oi3_values: Iterable[float] = (0.01, 0.02, 0.03),
    grid_hold_values: Iterable[int] = (1, 2),
    grid_pullback_fractions: Iterable[float] = (0.65, 0.75, 0.85),
) -> Path:
    output_dir = config.lab_config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = collect_anomaly_lab_rows(config.lab_config, symbols=symbols, progress_label="anomaly candidates")
    candidates.to_csv(output_dir / "anomaly_candidates.csv", index=False)
    build_oi_context_status(candidates).to_csv(output_dir / "oi_context_status.csv", index=False)
    print("anomaly signals: filtering", flush=True)
    signals = build_anomaly_signals(candidates, config=config)
    signals.to_csv(output_dir / "anomaly_signals.csv", index=False)
    trades = simulate_anomaly_trades(signals, config=config, progress_label="anomaly trades")
    trades.to_csv(output_dir / "anomaly_trades.csv", index=False)
    summary = summarize_trades(trades)
    summary.to_csv(output_dir / "anomaly_profitability_summary.csv", index=False)
    summarize_trades_by_symbol(trades).to_csv(output_dir / "anomaly_profitability_by_symbol.csv", index=False)
    if run_entry_grid:
        grid = run_anomaly_entry_grid(
            candidates,
            config,
            oi3_values=grid_oi3_values,
            hold_values=grid_hold_values,
            pullback_fractions=grid_pullback_fractions,
        )
        grid.to_csv(output_dir / "anomaly_entry_grid_summary.csv", index=False)
    run_config = {
        **asdict(config),
        "lab_config": asdict(config.lab_config),
    }
    pd.DataFrame([run_config]).to_csv(output_dir / "run_config.csv", index=False)
    return output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".output/cache"))
    parser.add_argument("--output-dir", type=Path, default=Path(".output/results/anomaly_lab"))
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--baseline-candles", type=int, default=60)
    parser.add_argument("--confirmation-candles", type=int, default=4)
    parser.add_argument("--forward-high-candles", type=int, default=240)
    parser.add_argument("--forward-low-candles", type=int, default=60)
    parser.add_argument("--min-quote-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-trade-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-price-retention", type=float, default=0.70)
    parser.add_argument("--min-verticality-score", type=float, default=0.25)
    parser.add_argument("--min-hold-count", type=int, default=0)
    parser.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    parser.add_argument("--require-oi-status-ok", action="store_true")
    parser.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    parser.add_argument("--entry-method", choices=["market", "break_box_high", "pullback_box_fraction"], default="market")
    parser.add_argument("--pullback-box-fraction", type=float, default=0.75)
    parser.add_argument("--entry-timeout-candles", type=int, default=60)
    parser.add_argument("--tp1-r", type=float, default=1.0)
    parser.add_argument("--tp1-fraction", type=float, default=0.50)
    parser.add_argument("--trail-lookback-candles", type=int, default=5)
    parser.add_argument("--trail-buffer-r", type=float, default=0.10)
    parser.add_argument("--max-hold-candles", type=int, default=240)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--run-entry-grid", action="store_true")
    parser.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    parser.add_argument("--grid-hold-values", default="1,2")
    parser.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    return parser


def config_from_args(args: argparse.Namespace) -> AnomalyBacktestConfig:
    lab_config = AnomalyLabConfig(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        timeframe=args.timeframe,
        days=args.days,
        end_timestamp_ms=args.end_timestamp_ms,
        baseline_candles=args.baseline_candles,
        confirmation_candles=args.confirmation_candles,
        forward_high_candles=args.forward_high_candles,
        forward_low_candles=args.forward_low_candles,
        min_quote_ratio_start=args.min_quote_ratio_start,
        min_trade_ratio_start=args.min_trade_ratio_start,
    )
    return AnomalyBacktestConfig(
        lab_config=lab_config,
        min_price_retention=args.min_price_retention,
        min_verticality_score=args.min_verticality_score,
        min_hold_count=args.min_hold_count,
        min_oi_change_pct_3x5m=args.min_oi_change_pct_3x5m,
        require_oi_status_ok=bool(args.require_oi_status_ok),
        max_initial_risk_pct=args.max_initial_risk_pct,
        entry_method=args.entry_method,
        pullback_box_fraction=args.pullback_box_fraction,
        entry_timeout_candles=args.entry_timeout_candles,
        tp1_r=args.tp1_r,
        tp1_fraction=args.tp1_fraction,
        trail_lookback_candles=args.trail_lookback_candles,
        trail_buffer_r=args.trail_buffer_r,
        max_hold_candles=args.max_hold_candles,
        fee_rate=args.fee_rate,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = run_anomaly_strategy_backtest(
        config_from_args(args),
        symbols=args.symbols,
        run_entry_grid=bool(args.run_entry_grid),
        grid_oi3_values=_parse_grid_values(args.grid_oi3_values, cast=float),
        grid_hold_values=_parse_grid_values(args.grid_hold_values, cast=int),
        grid_pullback_fractions=_parse_grid_values(args.grid_pullback_fractions, cast=float),
    )
    print(f"wrote anomaly strategy artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
