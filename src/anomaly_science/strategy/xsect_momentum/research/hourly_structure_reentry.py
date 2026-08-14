"""Causal visible-structure and post-anomaly short re-entry research.

The adaptive directional-change threshold confirms exact market swing prices;
it is never used as a physical stop offset.  Re-entry candidates are prediction
rows only and do not mutate portfolio accounting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_adverse_long import (
    AdverseLongSpec,
    HOUR,
    attach_active_short_trades,
    build_symbol_hazard_frame,
    regularize_symbol_bars,
)


REENTRY_VARIANTS = (
    "midpoint_only",
    "midpoint_flow",
    "midpoint_structure",
    "combined",
)
REENTRY_HORIZONS = (12, 24)


@dataclass(frozen=True, slots=True)
class VisibleStructureSpec:
    structure_version: str = "adaptive_directional_change_1h_v1"
    range_lookback_bars: int = 24
    reversal_range_multiple: float = 2.0

    def __post_init__(self) -> None:
        if self.range_lookback_bars < 2:
            raise ValueError("range_lookback_bars must be at least two")
        if self.reversal_range_multiple <= 0.0:
            raise ValueError("reversal_range_multiple must be positive")
        if not self.structure_version:
            raise ValueError("structure_version is required")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReentrySpec:
    reentry_version: str = "xsect_structure_reentry_1h_v1"
    warning_variant: str = "core_z2"
    minimum_delay_hours: int = 2
    maximum_taker_buy_share_3h: float = 0.50
    maximum_activity_z: float = 2.0

    def __post_init__(self) -> None:
        if self.warning_variant != "core_z2":
            raise ValueError("v1 warning_variant is frozen to core_z2")
        if self.minimum_delay_hours < 1:
            raise ValueError("minimum_delay_hours must be positive")
        if not 0.0 <= self.maximum_taker_buy_share_3h <= 1.0:
            raise ValueError("maximum_taker_buy_share_3h must be in [0, 1]")
        if not self.reentry_version:
            raise ValueError("reentry_version is required")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def attach_visible_structure(
    frame: pd.DataFrame,
    *,
    spec: VisibleStructureSpec = VisibleStructureSpec(),
) -> pd.DataFrame:
    """Attach causal adaptive directional-change structure to a symbol frame."""
    required = {"open_time", "snapshot_time", "high", "low", "close"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"hazard frame is missing structure columns: {missing}")
    out = frame.sort_values("open_time", kind="stable").reset_index(drop=True).copy()
    high = out["high"].to_numpy(dtype=float)
    low = out["low"].to_numpy(dtype=float)
    close = out["close"].to_numpy(dtype=float)
    log_range = np.log(high / low)
    threshold = (
        pd.Series(log_range)
        .shift(1)
        .rolling(spec.range_lookback_bars, min_periods=spec.range_lookback_bars)
        .median()
        .to_numpy(dtype=float)
        * spec.reversal_range_multiple
    )

    n = len(out)
    direction_values = np.zeros(n, dtype=np.int8)
    last_high_price = np.full(n, np.nan)
    last_low_price = np.full(n, np.nan)
    lower_high_price = np.full(n, np.nan)
    bearish = np.zeros(n, dtype=bool)
    new_lower_high = np.zeros(n, dtype=bool)
    last_high_pivot: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n
    last_low_pivot: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n
    last_high_confirmed: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n
    last_low_confirmed: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n
    lower_high_pivot: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n
    lower_high_confirmed: list[pd.Timestamp | pd.NaT] = [pd.NaT] * n

    direction = 0
    reference_close = np.nan
    high_extreme = np.nan
    low_extreme = np.nan
    high_extreme_i = -1
    low_extreme_i = -1
    confirmed_highs: list[tuple[float, int, pd.Timestamp]] = []
    confirmed_lows: list[tuple[float, int, pd.Timestamp]] = []
    protected_lower_high: tuple[float, int, pd.Timestamp] | None = None

    for i in range(n):
        if not (np.isfinite(high[i]) and np.isfinite(low[i]) and np.isfinite(close[i])):
            direction = 0
            reference_close = np.nan
            high_extreme = low_extreme = np.nan
            high_extreme_i = low_extreme_i = -1
            confirmed_highs.clear()
            confirmed_lows.clear()
            protected_lower_high = None
            continue
        if not np.isfinite(reference_close):
            reference_close = close[i]
            high_extreme, low_extreme = high[i], low[i]
            high_extreme_i = low_extreme_i = i
        if high[i] >= high_extreme:
            high_extreme, high_extreme_i = high[i], i
        if low[i] <= low_extreme:
            low_extreme, low_extreme_i = low[i], i

        reversal = threshold[i]
        confirmation_time = pd.Timestamp(out.at[i, "snapshot_time"])
        if np.isfinite(reversal):
            if direction == 0:
                if np.log(close[i] / reference_close) >= reversal:
                    confirmed_lows.append((float(low_extreme), low_extreme_i, confirmation_time))
                    direction = 1
                    high_extreme, high_extreme_i = high[i], i
                elif np.log(reference_close / close[i]) >= reversal:
                    confirmed_highs.append((float(high_extreme), high_extreme_i, confirmation_time))
                    direction = -1
                    low_extreme, low_extreme_i = low[i], i
            elif direction > 0 and np.log(high_extreme / close[i]) >= reversal:
                confirmed_highs.append((float(high_extreme), high_extreme_i, confirmation_time))
                if len(confirmed_highs) >= 2 and confirmed_highs[-1][0] < confirmed_highs[-2][0]:
                    protected_lower_high = confirmed_highs[-1]
                    new_lower_high[i] = True
                direction = -1
                low_extreme, low_extreme_i = low[i], i
            elif direction < 0 and np.log(close[i] / low_extreme) >= reversal:
                confirmed_lows.append((float(low_extreme), low_extreme_i, confirmation_time))
                direction = 1
                high_extreme, high_extreme_i = high[i], i

        direction_values[i] = direction
        if confirmed_highs:
            price, pivot_i, confirmed_at = confirmed_highs[-1]
            last_high_price[i] = price
            last_high_pivot[i] = pd.Timestamp(out.at[pivot_i, "open_time"])
            last_high_confirmed[i] = confirmed_at
        if confirmed_lows:
            price, pivot_i, confirmed_at = confirmed_lows[-1]
            last_low_price[i] = price
            last_low_pivot[i] = pd.Timestamp(out.at[pivot_i, "open_time"])
            last_low_confirmed[i] = confirmed_at
        if protected_lower_high is not None:
            price, pivot_i, confirmed_at = protected_lower_high
            lower_high_price[i] = price
            lower_high_pivot[i] = pd.Timestamp(out.at[pivot_i, "open_time"])
            lower_high_confirmed[i] = confirmed_at
        highs_lower = len(confirmed_highs) >= 2 and confirmed_highs[-1][0] < confirmed_highs[-2][0]
        lows_lower = len(confirmed_lows) >= 2 and confirmed_lows[-1][0] < confirmed_lows[-2][0]
        bearish[i] = bool(highs_lower and lows_lower)

    out["structure_reversal_log_threshold"] = threshold
    out["structure_direction"] = direction_values
    out["last_swing_high_price"] = last_high_price
    out["last_swing_high_pivot_time"] = pd.to_datetime(last_high_pivot, utc=True)
    out["last_swing_high_confirmed_time"] = pd.to_datetime(last_high_confirmed, utc=True)
    out["last_swing_low_price"] = last_low_price
    out["last_swing_low_pivot_time"] = pd.to_datetime(last_low_pivot, utc=True)
    out["last_swing_low_confirmed_time"] = pd.to_datetime(last_low_confirmed, utc=True)
    out["protected_lower_high_price"] = lower_high_price
    out["protected_lower_high_pivot_time"] = pd.to_datetime(lower_high_pivot, utc=True)
    out["protected_lower_high_confirmed_time"] = pd.to_datetime(lower_high_confirmed, utc=True)
    out["new_lower_high_confirmed"] = new_lower_high
    out["bearish_structure"] = bearish
    confirmed_columns = [
        "last_swing_high_confirmed_time",
        "last_swing_low_confirmed_time",
        "protected_lower_high_confirmed_time",
    ]
    for column in confirmed_columns:
        valid = out[column].notna()
        if not bool((out.loc[valid, column] <= out.loc[valid, "snapshot_time"]).all()):
            raise AssertionError(f"{column} exceeds snapshot time")
    return out


def build_structure_snapshot_table(
    hourly_root: Path,
    trades: pd.DataFrame,
    *,
    hazard_spec: AdverseLongSpec = AdverseLongSpec(),
    structure_spec: VisibleStructureSpec = VisibleStructureSpec(),
) -> pd.DataFrame:
    btc_path = hourly_root / "BTCUSDT.parquet"
    if not btc_path.exists():
        raise FileNotFoundError(btc_path)
    btc = regularize_symbol_bars(pd.read_parquet(btc_path))
    btc_close = btc["close"].astype(float)
    shorts = trades.loc[trades["weight"] < 0.0]
    pieces: list[pd.DataFrame] = []
    for symbol in sorted(shorts["symbol"].unique()):
        path = hourly_root / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        features = build_symbol_hazard_frame(
            pd.read_parquet(path),
            symbol=str(symbol),
            spec=hazard_spec,
            btc_close=btc_close,
        )
        structured = attach_visible_structure(features, spec=structure_spec)
        active = attach_active_short_trades(structured, shorts.loc[shorts["symbol"] == symbol])
        if not active.empty:
            pieces.append(active)
    if not pieces:
        raise ValueError("no structure-enriched active short snapshots were constructed")
    return pd.concat(pieces, ignore_index=True).sort_values(
        ["snapshot_time", "symbol", "trade_id"], kind="stable"
    ).reset_index(drop=True)


def build_reentry_ledgers(
    snapshots: pd.DataFrame,
    *,
    spec: ReentrySpec = ReentrySpec(),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one warning episode per trade and first signal per variant."""
    episodes: list[dict[str, object]] = []
    signals: list[dict[str, object]] = []
    for trade_id, group in snapshots.groupby("trade_id", sort=False):
        ordered = group.sort_values("snapshot_time", kind="stable").reset_index(drop=True)
        warnings = ordered.loc[ordered[spec.warning_variant].fillna(False).astype(bool)]
        if warnings.empty:
            continue
        warning_i = int(warnings.index[0])
        warning = ordered.loc[warning_i]
        origin = float(warning["last_swing_low_price"])
        origin_valid = (
            np.isfinite(origin)
            and pd.notna(warning["last_swing_low_confirmed_time"])
            and warning["last_swing_low_confirmed_time"] <= warning["snapshot_time"]
            and origin < float(warning["high"])
        )
        eligible_warning = bool(origin_valid and warning["remaining_holding_hours"] >= 24)
        episode: dict[str, object] = {
            "trade_id": trade_id,
            "symbol": warning["symbol"],
            "entry_time": warning["entry_time"],
            "scheduled_exit_time": warning["scheduled_exit_time"],
            "warning_open_time": warning["open_time"],
            "warning_snapshot_time": warning["snapshot_time"],
            "impulse_origin_price": origin if origin_valid else np.nan,
            "impulse_origin_pivot_time": warning["last_swing_low_pivot_time"] if origin_valid else pd.NaT,
            "impulse_origin_confirmed_time": warning["last_swing_low_confirmed_time"] if origin_valid else pd.NaT,
            "origin_valid": origin_valid,
            "eligible_warning_24h": eligible_warning,
        }
        found = {variant: False for variant in REENTRY_VARIANTS}
        event_high = -np.inf
        event_high_time = pd.NaT
        turnover = 0.0
        value_sum = 0.0
        ever_midpoint = False
        ever_flow = False
        ever_structure = False
        if origin_valid:
            for i in range(warning_i, len(ordered)):
                row = ordered.loc[i]
                if float(row["high"]) >= event_high:
                    event_high = float(row["high"])
                    event_high_time = row["open_time"]
                qv = float(row["quote_volume"])
                typical = float((row["high"] + row["low"] + row["close"]) / 3.0)
                if np.isfinite(qv) and qv > 0.0:
                    turnover += qv
                    value_sum += typical * qv
                event_vwap = value_sum / turnover if turnover > 0.0 else np.nan
                log_midpoint = float(np.sqrt(origin * event_high))
                zone_lower = min(log_midpoint, event_vwap) if np.isfinite(event_vwap) else np.nan
                zone_upper = max(log_midpoint, event_vwap) if np.isfinite(event_vwap) else np.nan
                delay = float((row["snapshot_time"] - warning["snapshot_time"]) / HOUR)
                midpoint = bool(
                    delay >= spec.minimum_delay_hours
                    and np.isfinite(zone_lower)
                    and float(row["close"]) < zone_lower
                )
                flow = bool(
                    float(row["taker_buy_share_3h"]) <= spec.maximum_taker_buy_share_3h
                    and float(row["quote_volume_z60"]) < spec.maximum_activity_z
                    and float(row["trade_count_z60"]) < spec.maximum_activity_z
                )
                stop_anchor = float(row["protected_lower_high_price"])
                structure = bool(
                    np.isfinite(stop_anchor)
                    and pd.notna(row["protected_lower_high_pivot_time"])
                    and pd.notna(row["protected_lower_high_confirmed_time"])
                    and row["protected_lower_high_pivot_time"] >= warning["open_time"]
                    and row["protected_lower_high_confirmed_time"] <= row["snapshot_time"]
                    and bool(row["bearish_structure"])
                    and float(row["close"]) < stop_anchor < event_high
                )
                ever_midpoint = ever_midpoint or midpoint
                ever_flow = ever_flow or flow
                ever_structure = ever_structure or structure
                conditions = {
                    "midpoint_only": midpoint,
                    "midpoint_flow": midpoint and flow,
                    "midpoint_structure": midpoint and structure,
                    "combined": midpoint and flow and structure,
                }
                for variant, accepted in conditions.items():
                    if not accepted or found[variant]:
                        continue
                    if float(row["remaining_holding_hours"]) < 24 or not bool(row["eligible_24h"]):
                        continue
                    future_high = float(row["close"] * (1.0 + row["future_mae_24h"]))
                    signals.append({
                        "trade_id": trade_id,
                        "symbol": row["symbol"],
                        "entry_year": int(pd.Timestamp(row["entry_time"]).year),
                        "variant": variant,
                        "warning_snapshot_time": warning["snapshot_time"],
                        "signal_open_time": row["open_time"],
                        "signal_snapshot_time": row["snapshot_time"],
                        "signal_delay_hours": delay,
                        "remaining_holding_hours": float(row["remaining_holding_hours"]),
                        "signal_close": float(row["close"]),
                        "impulse_origin_price": origin,
                        "event_high_price": event_high,
                        "event_high_time": event_high_time,
                        "event_vwap": event_vwap,
                        "log_midpoint": log_midpoint,
                        "zone_lower": zone_lower,
                        "zone_upper": zone_upper,
                        "taker_buy_share_3h": float(row["taker_buy_share_3h"]),
                        "quote_volume_z60": float(row["quote_volume_z60"]),
                        "trade_count_z60": float(row["trade_count_z60"]),
                        "structural_stop_anchor": stop_anchor if structure else np.nan,
                        "structural_stop_pivot_time": row["protected_lower_high_pivot_time"] if structure else pd.NaT,
                        "structural_stop_confirmed_time": row["protected_lower_high_confirmed_time"] if structure else pd.NaT,
                        **{
                            f"future_close_return_{horizon}h": float(row[f"future_close_return_{horizon}h"])
                            for horizon in REENTRY_HORIZONS
                        },
                        **{
                            f"future_mae_{horizon}h": float(row[f"future_mae_{horizon}h"])
                            for horizon in REENTRY_HORIZONS
                        },
                        **{
                            f"future_mfe_{horizon}h": float(row[f"future_mfe_{horizon}h"])
                            for horizon in REENTRY_HORIZONS
                        },
                        "structural_stop_breached_24h": bool(
                            structure and np.isfinite(stop_anchor) and future_high >= stop_anchor
                        ) if structure else np.nan,
                    })
                    found[variant] = True
        episode.update({
            "ever_midpoint_condition": ever_midpoint,
            "ever_flow_decay_condition": ever_flow,
            "ever_bearish_structure_condition": ever_structure,
            **{f"{variant}_signal_found": found[variant] for variant in REENTRY_VARIANTS},
        })
        episodes.append(episode)
    return pd.DataFrame(episodes), pd.DataFrame(signals)


def reentry_metric_table(episodes: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    valid_episodes = episodes.loc[episodes["eligible_warning_24h"]]
    for year in (2023, 2024, 2025):
        year_episodes = valid_episodes.loc[pd.to_datetime(valid_episodes["entry_time"], utc=True).dt.year == year]
        for variant in REENTRY_VARIANTS:
            variant_signals = signals.loc[
                (signals["entry_year"] == year) & (signals["variant"] == variant)
            ]
            for horizon in REENTRY_HORIZONS:
                returns = variant_signals[f"future_close_return_{horizon}h"].dropna()
                stop_breach = variant_signals["structural_stop_breached_24h"].dropna()
                rows.append({
                    "year": year,
                    "variant": variant,
                    "horizon_hours": horizon,
                    "valid_warning_episodes": len(year_episodes),
                    "reentry_events": len(returns),
                    "reentry_coverage": len(returns) / len(year_episodes) if len(year_episodes) else np.nan,
                    "negative_close_return_share": float((returns < 0.0).mean()) if len(returns) else np.nan,
                    "mean_future_close_return": float(returns.mean()) if len(returns) else np.nan,
                    "median_future_close_return": float(returns.median()) if len(returns) else np.nan,
                    "mean_future_mae": float(variant_signals[f"future_mae_{horizon}h"].mean()) if len(returns) else np.nan,
                    "mean_future_mfe": float(variant_signals[f"future_mfe_{horizon}h"].mean()) if len(returns) else np.nan,
                    "structural_stop_breach_share_24h": float(stop_breach.mean()) if len(stop_breach) else np.nan,
                    "median_signal_delay_hours": float(variant_signals["signal_delay_hours"].median()) if len(returns) else np.nan,
                })
    return pd.DataFrame(rows)


def bootstrap_mean_bounds(
    signals: pd.DataFrame,
    *,
    simulations: int = 2_000,
    seed: int = 20_260_805,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        for variant in REENTRY_VARIANTS:
            values = signals.loc[
                (signals["entry_year"] == year) & (signals["variant"] == variant),
                "future_close_return_24h",
            ].dropna().to_numpy(dtype=float)
            if len(values):
                draws = rng.choice(values, size=(simulations, len(values)), replace=True).mean(axis=1)
                q05, q95 = np.quantile(draws, [0.05, 0.95])
            else:
                q05 = q95 = np.nan
            rows.append({
                "year": year,
                "variant": variant,
                "events": len(values),
                "bootstrap_mean_q05": float(q05),
                "bootstrap_mean_q95": float(q95),
            })
    return pd.DataFrame(rows)


def reentry_acceptance_gates(
    metrics: pd.DataFrame,
    bootstrap: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    primary = metrics.loc[
        (metrics["variant"] == "combined") & (metrics["horizon_hours"] == 24)
    ]
    midpoint = metrics.loc[
        (metrics["variant"] == "midpoint_only") & (metrics["horizon_hours"] == 24)
    ]
    for year in (2023, 2024, 2025):
        current = primary.loc[primary["year"] == year].iloc[0]
        naive = midpoint.loc[midpoint["year"] == year].iloc[0]
        boot = bootstrap.loc[
            (bootstrap["year"] == year) & (bootstrap["variant"] == "combined")
        ].iloc[0]
        values = {
            "events_gte_30": int(current["reentry_events"]) >= 30,
            "coverage_gte_20pct": float(current["reentry_coverage"]) >= 0.20,
            "negative_share_gte_55pct": float(current["negative_close_return_share"]) >= 0.55,
            "mean_return_negative": float(current["mean_future_close_return"]) < 0.0,
            "bootstrap_q95_negative": float(boot["bootstrap_mean_q95"]) < 0.0,
            "beats_midpoint_only": float(current["mean_future_close_return"]) < float(naive["mean_future_close_return"]),
            "stop_breach_lte_25pct": float(current["structural_stop_breach_share_24h"]) <= 0.25,
        }
        rows.append({"year": year, **values, "all_reentry_gates_pass": all(values.values())})
    return pd.DataFrame(rows)
