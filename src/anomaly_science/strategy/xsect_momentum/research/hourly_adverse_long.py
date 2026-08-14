"""Causal 1h adverse-long hazard audit for active cross-sectional shorts.

This module adapts the broad-anomaly *anatomy* to completed 1h bars.  It does
not reuse the 1m detector with incorrect time units, and it does not execute a
trade.  Its output is a prediction/timing artifact that must pass registered
gates before an exit policy may consume it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


HOUR = pd.Timedelta(hours=1)
PRIMARY_HORIZON_HOURS = 12
REGISTERED_HORIZONS = (6, 12, 24)
REGISTERED_VARIANTS = (
    "price_only",
    "core_z2",
    "core_z3",
    "core_z4",
    "core_z3_buyflow",
)


@dataclass(frozen=True, slots=True)
class AdverseLongSpec:
    detector_version: str = "xsect_adverse_long_1h_v1"
    baseline_bars: int = 60
    min_one_hour_return: float = 0.015
    fast_window_bars: int = 3
    min_fast_return: float = 0.025
    grind_window_bars: int = 12
    min_grind_return: float = 0.05
    min_grind_positive_bars: int = 8
    breakout_lookback_bars: int = 24
    min_breakout_return: float = 0.002
    min_close_location: float = 0.55
    primary_activity_z: float = 3.0
    min_buyflow_share: float = 0.52

    def __post_init__(self) -> None:
        if self.baseline_bars < 2:
            raise ValueError("baseline_bars must be at least two")
        if self.fast_window_bars <= 0 or self.grind_window_bars <= 0:
            raise ValueError("registered windows must be positive")
        if self.min_grind_positive_bars > self.grind_window_bars:
            raise ValueError("min_grind_positive_bars exceeds grind_window_bars")
        if not 0.0 <= self.min_close_location <= 1.0:
            raise ValueError("min_close_location must be in [0, 1]")
        if not 0.0 <= self.min_buyflow_share <= 1.0:
            raise ValueError("min_buyflow_share must be in [0, 1]")
        if not self.detector_version:
            raise ValueError("detector_version is required")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _rolling_zscore(value: pd.Series, bars: int) -> pd.Series:
    """Current value versus the preceding ``bars`` observations."""
    baseline = value.shift(1).rolling(bars, min_periods=bars)
    mean = baseline.mean()
    std = baseline.std(ddof=0)
    return ((value - mean) / std.where(std > 0.0)).replace([np.inf, -np.inf], np.nan)


def _future_max(series: pd.Series, horizon: int) -> pd.Series:
    future = pd.concat([series.shift(-offset) for offset in range(1, horizon + 1)], axis=1)
    return future.max(axis=1, skipna=False)


def _future_peak_offset(high: pd.Series, horizon: int) -> pd.Series:
    future = pd.concat([high.shift(-offset) for offset in range(1, horizon + 1)], axis=1)
    values = future.to_numpy(dtype=float)
    valid = np.isfinite(values).all(axis=1)
    offsets = np.full(len(future), np.nan)
    offsets[valid] = np.argmax(values[valid], axis=1) + 1
    return pd.Series(offsets, index=high.index)


def regularize_symbol_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_quote_volume",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"hourly source is missing columns: {missing}")
    if frame.empty:
        return frame.copy()
    work = frame.loc[:, sorted(required)].copy()
    work["open_time"] = pd.to_datetime(work.pop("timestamp"), unit="ms", utc=True)
    work = work.sort_values("open_time", kind="stable")
    if work["open_time"].duplicated().any():
        raise ValueError("hourly source contains duplicate timestamps")
    calendar = pd.date_range(work["open_time"].iloc[0], work["open_time"].iloc[-1], freq="h", tz="UTC")
    return work.set_index("open_time").reindex(calendar).rename_axis("open_time")


def build_symbol_hazard_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    spec: AdverseLongSpec = AdverseLongSpec(),
    horizons: Iterable[int] = REGISTERED_HORIZONS,
    btc_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Build causal features and future labels for one regularized symbol."""
    work = regularize_symbol_bars(frame)
    if work.empty:
        return work.reset_index()
    horizons = tuple(sorted(set(int(value) for value in horizons)))
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("horizons must contain positive hours")

    open_ = work["open"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    close = work["close"].astype(float)
    quote_volume = work["quote_volume"].astype(float)
    trade_count = work["trade_count"].astype(float)
    taker_buy_quote = work["taker_buy_quote_volume"].astype(float)
    candle_range = high / low - 1.0
    close_range = high - low

    out = pd.DataFrame(index=work.index)
    out["symbol"] = symbol
    out["feature_cutoff_time"] = out.index + HOUR
    out["snapshot_time"] = out["feature_cutoff_time"]
    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["close"] = close
    out["volume"] = work["volume"].astype(float)
    out["quote_volume"] = quote_volume
    out["trade_count"] = trade_count
    out["taker_buy_quote_volume"] = taker_buy_quote
    out["move_1h"] = close / open_ - 1.0
    out["return_3h"] = close / close.shift(spec.fast_window_bars) - 1.0
    out["return_12h"] = close / close.shift(spec.grind_window_bars) - 1.0
    positive = (close > open_).where(close.notna() & open_.notna()).astype(float)
    out["positive_bar_count_12h"] = positive.rolling(
        spec.grind_window_bars,
        min_periods=spec.grind_window_bars,
    ).sum()
    out["close_location"] = ((close - low) / close_range.where(close_range > 0.0)).clip(0.0, 1.0)
    out["quote_volume_z60"] = _rolling_zscore(quote_volume, spec.baseline_bars)
    out["trade_count_z60"] = _rolling_zscore(trade_count, spec.baseline_bars)
    out["range_z60"] = _rolling_zscore(candle_range, spec.baseline_bars)
    prior_high = high.shift(1).rolling(
        spec.breakout_lookback_bars,
        min_periods=spec.breakout_lookback_bars,
    ).max()
    out["breakout_24h"] = close / prior_high - 1.0
    qv3 = quote_volume.rolling(spec.fast_window_bars, min_periods=spec.fast_window_bars).sum()
    buy3 = taker_buy_quote.rolling(spec.fast_window_bars, min_periods=spec.fast_window_bars).sum()
    out["taker_buy_share_3h"] = (buy3 / qv3.where(qv3 > 0.0)).clip(0.0, 1.0)
    path = close.pct_change(fill_method=None).abs().rolling(
        spec.fast_window_bars,
        min_periods=spec.fast_window_bars,
    ).sum()
    out["upward_efficiency_3h"] = (out["return_3h"].clip(lower=0.0) / path.where(path > 0.0)).clip(0.0, 1.0)

    if btc_close is not None:
        btc = btc_close.reindex(out.index)
        out["relative_to_btc_3h"] = out["return_3h"] - (btc / btc.shift(spec.fast_window_bars) - 1.0)

    one_shot = out["move_1h"] >= spec.min_one_hour_return
    fast_burst = out["return_3h"] >= spec.min_fast_return
    grind = (
        (out["return_12h"] >= spec.min_grind_return)
        & (out["positive_bar_count_12h"] >= spec.min_grind_positive_bars)
    )
    breakout = (
        (out["breakout_24h"] >= spec.min_breakout_return)
        & (out["return_3h"] > 0.0)
    )
    out["component_one_shot"] = one_shot
    out["component_fast_burst"] = fast_burst
    out["component_grind"] = grind
    out["component_breakout"] = breakout
    out["positive_price_evidence"] = one_shot | fast_burst | grind | breakout
    out["price_only"] = out["positive_price_evidence"] & (out["close_location"] >= spec.min_close_location)
    for threshold in (2, 3, 4):
        activity = (
            (out["quote_volume_z60"] >= float(threshold))
            | (out["trade_count_z60"] >= float(threshold))
            | (out["range_z60"] >= float(threshold))
        )
        out[f"activity_z{threshold}"] = activity
        out[f"core_z{threshold}"] = out["price_only"] & activity
    out["core_z3_buyflow"] = out["core_z3"] & (out["taker_buy_share_3h"] >= spec.min_buyflow_share)

    out["future_start_time"] = out["snapshot_time"] + HOUR
    for horizon in horizons:
        future_high = _future_max(high, horizon)
        out[f"future_mae_{horizon}h"] = future_high / close - 1.0
        future_low = pd.concat(
            [low.shift(-offset) for offset in range(1, horizon + 1)],
            axis=1,
        ).min(axis=1, skipna=False)
        out[f"future_mfe_{horizon}h"] = future_low / close - 1.0
        out[f"future_close_return_{horizon}h"] = close.shift(-horizon) / close - 1.0
        out[f"future_peak_offset_{horizon}h"] = _future_peak_offset(high, horizon)
        out[f"label_end_time_{horizon}h"] = out["snapshot_time"] + pd.Timedelta(hours=horizon)

    valid = out["close"].notna()
    out = out.loc[valid].reset_index(names="open_time")
    if not bool((out["feature_cutoff_time"] <= out["snapshot_time"]).all()):
        raise AssertionError("feature cutoff exceeds snapshot time")
    if not bool((out["future_start_time"] > out["snapshot_time"]).all()):
        raise AssertionError("future label does not start after snapshot time")
    return out


def attach_active_short_trades(features: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Select feature rows observed while each registered short is active."""
    required = {"symbol", "entry_time", "exit_time", "entry_price", "weight"}
    missing = sorted(required.difference(trades.columns))
    if missing:
        raise ValueError(f"trade ledger is missing columns: {missing}")
    shorts = trades.loc[trades["weight"] < 0.0].copy()
    pieces: list[pd.DataFrame] = []
    for ledger_index, trade in shorts.iterrows():
        active = features.loc[
            (features["snapshot_time"] > trade["entry_time"])
            & (features["snapshot_time"] < trade["exit_time"])
        ].copy()
        if active.empty:
            continue
        active["trade_id"] = f"{trade['symbol']}|{pd.Timestamp(trade['entry_time']).isoformat()}|{ledger_index}"
        active["entry_time"] = trade["entry_time"]
        active["scheduled_exit_time"] = trade["exit_time"]
        active["entry_price"] = float(trade["entry_price"])
        active["entry_weight"] = float(trade["weight"])
        active["trade_seg_ret"] = float(trade.get("seg_ret", np.nan))
        remaining = (active["scheduled_exit_time"] - active["snapshot_time"]) / HOUR
        active["remaining_holding_hours"] = remaining.astype(float)
        for horizon in REGISTERED_HORIZONS:
            active[f"eligible_{horizon}h"] = (
                (active["remaining_holding_hours"] >= horizon)
                & active[f"future_mae_{horizon}h"].notna()
                & active[f"future_close_return_{horizon}h"].notna()
            )
        pieces.append(active)
    if not pieces:
        return pd.DataFrame()
    result = pd.concat(pieces, ignore_index=True)
    if not bool((result["symbol"] == result["trade_id"].str.split("|").str[0]).all()):
        raise AssertionError("symbol/trade identity mismatch")
    return result.sort_values(["snapshot_time", "symbol", "trade_id"], kind="stable").reset_index(drop=True)


def build_active_short_snapshot_table(
    hourly_root: Path,
    trades: pd.DataFrame,
    *,
    spec: AdverseLongSpec = AdverseLongSpec(),
) -> pd.DataFrame:
    """Load only symbols used by the short ledger and build the audit table."""
    btc_path = hourly_root / "BTCUSDT.parquet"
    if not btc_path.exists():
        raise FileNotFoundError(btc_path)
    btc = regularize_symbol_bars(pd.read_parquet(btc_path))
    btc_close = btc["close"].astype(float)

    pieces: list[pd.DataFrame] = []
    shorts = trades.loc[trades["weight"] < 0.0]
    for symbol in sorted(shorts["symbol"].unique()):
        path = hourly_root / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        source = pd.read_parquet(path)
        features = build_symbol_hazard_frame(
            source,
            symbol=str(symbol),
            spec=spec,
            btc_close=btc_close,
        )
        active = attach_active_short_trades(features, shorts.loc[shorts["symbol"] == symbol])
        if not active.empty:
            pieces.append(active)
    if not pieces:
        raise ValueError("no active short snapshots were constructed")
    result = pd.concat(pieces, ignore_index=True)
    return result.sort_values(["snapshot_time", "symbol", "trade_id"], kind="stable").reset_index(drop=True)


def feature_ic_table(snapshots: pd.DataFrame) -> pd.DataFrame:
    features = (
        "move_1h",
        "return_3h",
        "return_12h",
        "close_location",
        "quote_volume_z60",
        "trade_count_z60",
        "range_z60",
        "breakout_24h",
        "taker_buy_share_3h",
        "upward_efficiency_3h",
        "relative_to_btc_3h",
    )
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        year_rows = snapshots.loc[snapshots["snapshot_time"].dt.year == year]
        for horizon in REGISTERED_HORIZONS:
            eligible = year_rows.loc[year_rows[f"eligible_{horizon}h"]]
            label = f"future_mae_{horizon}h"
            for feature in features:
                if feature not in eligible:
                    continue
                pair = eligible[[feature, label]].dropna()
                rows.append({
                    "year": year,
                    "horizon_hours": horizon,
                    "feature": feature,
                    "n": len(pair),
                    "spearman_ic": float(pair[feature].corr(pair[label], method="spearman")) if len(pair) > 2 else np.nan,
                })
    return pd.DataFrame(rows)


def snapshot_metric_table(snapshots: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        year_rows = snapshots.loc[snapshots["snapshot_time"].dt.year == year]
        for horizon in REGISTERED_HORIZONS:
            eligible = year_rows.loc[year_rows[f"eligible_{horizon}h"]].copy()
            label = f"future_mae_{horizon}h"
            if eligible.empty:
                continue
            adverse_threshold = float(eligible[label].quantile(0.90))
            unconditional = float(eligible[label].mean())
            top_adverse = eligible[label] >= adverse_threshold
            for variant in REGISTERED_VARIANTS:
                trigger = eligible[variant].fillna(False).astype(bool)
                triggered = eligible.loc[trigger]
                mean_triggered = float(triggered[label].mean()) if len(triggered) else np.nan
                rows.append({
                    "year": year,
                    "horizon_hours": horizon,
                    "variant": variant,
                    "eligible_snapshots": len(eligible),
                    "triggered_snapshots": int(trigger.sum()),
                    "trigger_rate": float(trigger.mean()),
                    "mean_future_mae": unconditional,
                    "mean_triggered_future_mae": mean_triggered,
                    "mae_lift": mean_triggered / unconditional if unconditional > 0.0 else np.nan,
                    "top_adverse_threshold": adverse_threshold,
                    "top_adverse_count": int(top_adverse.sum()),
                    "top_adverse_recall": float((trigger & top_adverse).sum() / top_adverse.sum()) if top_adverse.any() else np.nan,
                    "mean_triggered_future_close_return": float(triggered[f"future_close_return_{horizon}h"].mean()) if len(triggered) else np.nan,
                })
    return pd.DataFrame(rows)


def trade_catastrophe_table(snapshots: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for trade_id, group in snapshots.groupby("trade_id", sort=False):
        ordered = group.sort_values("snapshot_time", kind="stable")
        peak_i = ordered["high"].astype(float).idxmax()
        peak = ordered.loc[peak_i]
        row: dict[str, object] = {
            "trade_id": trade_id,
            "symbol": str(peak["symbol"]),
            "entry_time": peak["entry_time"],
            "scheduled_exit_time": peak["scheduled_exit_time"],
            "entry_year": int(pd.Timestamp(peak["entry_time"]).year),
            "entry_price": float(peak["entry_price"]),
            "trade_seg_ret": float(peak["trade_seg_ret"]),
            "trade_max_adverse_excursion": float(peak["high"] / peak["entry_price"] - 1.0),
            "adverse_peak_open_time": peak["open_time"],
            "adverse_peak_available_time": peak["snapshot_time"],
        }
        for variant in REGISTERED_VARIANTS:
            warnings = ordered.loc[
                ordered[variant].fillna(False).astype(bool)
                & (ordered["snapshot_time"] <= peak["open_time"])
            ]
            first_warning = warnings["snapshot_time"].min() if len(warnings) else pd.NaT
            row[f"{variant}_covered_before_peak"] = bool(len(warnings))
            row[f"{variant}_first_warning_time"] = first_warning
            row[f"{variant}_lead_hours"] = (
                float((peak["open_time"] - first_warning) / HOUR)
                if pd.notna(first_warning)
                else np.nan
            )
        rows.append(row)
    result = pd.DataFrame(rows)
    result["catastrophic"] = False
    result["catastrophe_threshold"] = np.nan
    for year, indices in result.groupby("entry_year").groups.items():
        threshold = float(result.loc[indices, "trade_max_adverse_excursion"].quantile(0.95))
        result.loc[indices, "catastrophe_threshold"] = threshold
        result.loc[indices, "catastrophic"] = result.loc[indices, "trade_max_adverse_excursion"] >= threshold
    return result.sort_values(["entry_time", "symbol"], kind="stable").reset_index(drop=True)


def trade_metric_table(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        year_rows = trades.loc[trades["entry_year"] == year]
        catastrophic = year_rows.loc[year_rows["catastrophic"]]
        for variant in REGISTERED_VARIANTS:
            covered = catastrophic[f"{variant}_covered_before_peak"].astype(bool)
            leads = catastrophic.loc[covered, f"{variant}_lead_hours"].dropna()
            rows.append({
                "year": year,
                "variant": variant,
                "trade_count": len(year_rows),
                "catastrophic_trade_count": len(catastrophic),
                "catastrophic_trade_recall": float(covered.mean()) if len(catastrophic) else np.nan,
                "median_warning_lead_hours": float(leads.median()) if len(leads) else np.nan,
            })
    return pd.DataFrame(rows)


def circular_shift_null(
    snapshots: pd.DataFrame,
    *,
    simulations: int = 500,
    seed: int = 20_260_805,
) -> pd.DataFrame:
    """Within-symbol/year circular-shift null for the primary 12h detector."""
    eligible = snapshots.loc[snapshots["eligible_12h"]].copy()
    eligible["year"] = eligible["snapshot_time"].dt.year
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        year_rows = eligible.loc[eligible["year"] == year]
        groups: list[tuple[np.ndarray, np.ndarray]] = []
        for _, group in year_rows.groupby("symbol", sort=True):
            ordered = group.sort_values("snapshot_time", kind="stable")
            groups.append((
                ordered["core_z3"].fillna(False).to_numpy(dtype=bool),
                ordered["future_mae_12h"].to_numpy(dtype=float),
            ))
        unconditional = float(year_rows["future_mae_12h"].mean())
        for simulation in range(simulations):
            triggered_sum = 0.0
            triggered_n = 0
            for trigger, label in groups:
                if len(trigger) < 2 or not trigger.any():
                    continue
                shift = int(rng.integers(1, len(trigger)))
                shifted = np.roll(trigger, shift)
                triggered_sum += float(label[shifted].sum())
                triggered_n += int(shifted.sum())
            mean_triggered = triggered_sum / triggered_n if triggered_n else np.nan
            rows.append({
                "year": year,
                "simulation": simulation,
                "mean_triggered_future_mae_12h": mean_triggered,
                "mae_lift": mean_triggered / unconditional if unconditional > 0.0 else np.nan,
            })
    return pd.DataFrame(rows)


def acceptance_gate_table(
    snapshots: pd.DataFrame,
    snapshot_metrics: pd.DataFrame,
    trade_metrics: pd.DataFrame,
    null: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        null_q95 = float(null.loc[null["year"] == year, "mae_lift"].quantile(0.95))
        snap = snapshot_metrics.loc[
            (snapshot_metrics["year"] == year)
            & (snapshot_metrics["horizon_hours"] == PRIMARY_HORIZON_HOURS)
            & (snapshot_metrics["variant"] == "core_z3")
        ].iloc[0]
        trade = trade_metrics.loc[
            (trade_metrics["year"] == year)
            & (trade_metrics["variant"] == "core_z3")
        ].iloc[0]
        year_primary = snapshots.loc[
            snapshots["eligible_12h"]
            & (snapshots["snapshot_time"].dt.year == year)
        ]
        real_mean = float(year_primary.loc[year_primary["core_z3"], "future_mae_12h"].mean())
        unconditional = float(year_primary["future_mae_12h"].mean())
        real_lift = real_mean / unconditional if unconditional > 0.0 else np.nan
        values = {
            "trigger_rate_lte_10pct": float(snap["trigger_rate"]) <= 0.10,
            "mae_lift_gte_1_5": float(snap["mae_lift"]) >= 1.5,
            "top_decile_recall_gte_35pct": float(snap["top_adverse_recall"]) >= 0.35,
            "catastrophic_trade_recall_gte_50pct": float(trade["catastrophic_trade_recall"]) >= 0.50,
            "median_lead_gte_2h": float(trade["median_warning_lead_hours"]) >= 2.0,
            "real_lift_gt_shift_q95": real_lift > null_q95,
        }
        rows.append({
            "year": year,
            **values,
            "real_mae_lift": real_lift,
            "shift_null_q95_mae_lift": null_q95,
            "all_prediction_gates_pass": all(values.values()),
        })
    return pd.DataFrame(rows)
