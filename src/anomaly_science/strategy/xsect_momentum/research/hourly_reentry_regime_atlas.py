"""Broad causal market-regime atlas for midpoint short re-entry events."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.hourly_adverse_long import (
    regularize_symbol_bars,
)
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY


OUT = Path(".output/results/xsect_momentum")
SOURCE = OUT / "hourly_structure_reentry_audit_v1"
ATLAS_OUT = OUT / "hourly_reentry_regime_atlas_v1"
SIGNALS_PATH = SOURCE / "reentry_signals.parquet"
SNAPSHOTS_PATH = SOURCE / "structure_snapshots.parquet"
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
PROTOCOL = Path("docs/strategies/xsect_momentum_1h_reentry_regime_atlas_protocol_v1.md")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")


@dataclass(frozen=True, slots=True)
class Hypothesis:
    name: str
    family: str
    description: str


def _rolling_z(value: pd.Series, bars: int = 60) -> pd.Series:
    baseline = value.shift(1).rolling(bars, min_periods=bars)
    return (value - baseline.mean()) / baseline.std(ddof=0).where(lambda item: item > 0.0)


def _asset_context(symbol: str) -> pd.DataFrame:
    frame = regularize_symbol_bars(pd.read_parquet(HOURLY_ROOT / f"{symbol}.parquet"))
    frame = frame.loc[frame.index + pd.Timedelta(hours=1) < IS_END].copy()
    close = frame["close"].astype(float)
    log_return = np.log(close).diff()
    out = pd.DataFrame(index=frame.index)
    out["signal_snapshot_time"] = frame.index + pd.Timedelta(hours=1)
    prefix = symbol.removesuffix("USDT").lower()
    for hours in (6, 24, 72, 168, 720, 2160):
        out[f"{prefix}_return_{hours}h"] = close / close.shift(hours) - 1.0
    for hours in (168, 720, 2160):
        mean = close.rolling(hours, min_periods=hours).mean()
        high = close.rolling(hours, min_periods=hours).max()
        out[f"{prefix}_close_vs_mean_{hours}h"] = close / mean - 1.0
        out[f"{prefix}_drawdown_high_{hours}h"] = close / high - 1.0
    for hours in (24, 168, 720):
        out[f"{prefix}_volatility_{hours}h"] = log_return.rolling(hours, min_periods=hours).std(ddof=0)
    out[f"{prefix}_vol_ratio_7d_30d"] = (
        out[f"{prefix}_volatility_168h"] / out[f"{prefix}_volatility_720h"]
    )
    out[f"{prefix}_fast_minus_slow_mean"] = (
        close.rolling(168, min_periods=168).mean()
        / close.rolling(720, min_periods=720).mean()
        - 1.0
    )
    out[f"{prefix}_activity_quote_z60"] = _rolling_z(frame["quote_volume"].astype(float))
    out[f"{prefix}_activity_trade_z60"] = _rolling_z(frame["trade_count"].astype(float))
    candle_range = np.log(frame["high"].astype(float) / frame["low"].astype(float))
    out[f"{prefix}_activity_range_z60"] = _rolling_z(candle_range)
    below = out[f"{prefix}_close_vs_mean_720h"] < 0.0
    crossed_below = below & ~below.shift(1, fill_value=False)
    crossed_above = ~below & below.shift(1, fill_value=False)
    out[f"{prefix}_crossed_below_30d_recent72h"] = crossed_below.rolling(72, min_periods=1).max().astype(bool)
    out[f"{prefix}_crossed_above_30d_recent72h"] = crossed_above.rolling(72, min_periods=1).max().astype(bool)
    out[f"{prefix}_acceleration_24h_vs_7d"] = (
        out[f"{prefix}_return_24h"] - out[f"{prefix}_return_168h"] / 7.0
    )
    out[f"{prefix}_acceleration_7d_vs_30d"] = (
        out[f"{prefix}_return_168h"] - out[f"{prefix}_return_720h"] * (168.0 / 720.0)
    )
    return out.reset_index(drop=True)


def _daily_breadth() -> pd.DataFrame:
    panel = pn.load_panel(
        is_only=True,
        source_glob=str(Path(pn.KLINES_DAILY_PANEL)),
        is_end=IS_END,
    )
    close = pn.pivot(panel, "close")
    quote_volume = pn.pivot(panel, "quote_volume")
    mask = pn.build_universe_mask(
        panel,
        quote_volume,
        100,
        PRIMARY.liquidity_lb,
        PRIMARY.min_age_days,
    )
    returns = {days: close / close.shift(days) - 1.0 for days in (1, 7, 30)}
    sma = {days: close.rolling(days, min_periods=days).mean() for days in (7, 30)}
    count = mask.sum(axis=1).replace(0, np.nan)
    out = pd.DataFrame(index=close.index)
    for days, values in returns.items():
        eligible = values.where(mask)
        out[f"market_median_return_{days}d"] = eligible.median(axis=1)
        out[f"market_positive_share_{days}d"] = (eligible > 0.0).sum(axis=1) / count
        out[f"market_dispersion_{days}d"] = eligible.std(axis=1, ddof=0)
    for days, mean in sma.items():
        above = (close > mean).where(mask)
        out[f"market_above_mean_share_{days}d"] = above.sum(axis=1) / count
    out["market_dispersion_ratio_1d_90d"] = (
        out["market_dispersion_1d"]
        / out["market_dispersion_1d"].shift(1).rolling(90, min_periods=30).median()
    )
    out["market_breadth_acceleration"] = (
        out["market_positive_share_1d"] - out["market_positive_share_7d"]
    )
    out["market_universe_count"] = count
    out["context_day"] = out.index + pd.Timedelta(days=1)
    return out.reset_index(drop=True)


def _short_book_context(snapshots: pd.DataFrame) -> pd.DataFrame:
    grouped = snapshots.groupby("snapshot_time", sort=True)
    rows = pd.DataFrame(index=grouped.size().index)
    rows["book_short_count"] = grouped.size()
    for feature, output in (
        ("move_1h", "book_positive_1h_share"),
        ("return_3h", "book_positive_3h_share"),
        ("return_12h", "book_positive_12h_share"),
    ):
        rows[output] = grouped[feature].apply(lambda values: float((values > 0.0).mean()))
        rows[output.replace("positive", "median_return")] = grouped[feature].median()
        rows[output.replace("positive", "dispersion")] = grouped[feature].std(ddof=0)
    rows["book_core_z2_share"] = grouped["core_z2"].apply(lambda values: float(values.fillna(False).mean()))
    rows["book_core_z2_count"] = grouped["core_z2"].apply(lambda values: int(values.fillna(False).sum()))
    rows["book_quote_z60_median"] = grouped["quote_volume_z60"].median()
    rows["book_trade_z60_median"] = grouped["trade_count_z60"].median()
    return rows.reset_index(names="signal_snapshot_time")


def _coin_context(signals: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for symbol, group in signals.groupby("symbol", sort=True):
        raw = regularize_symbol_bars(pd.read_parquet(HOURLY_ROOT / f"{symbol}.parquet"))
        close = raw["close"].astype(float)
        context = pd.DataFrame(index=raw.index)
        for hours in (24, 168, 720):
            context[f"coin_return_{hours}h"] = close / close.shift(hours) - 1.0
        context["coin_prior_high_168h"] = raw["high"].astype(float).shift(1).rolling(168, min_periods=168).max()
        context["coin_prior_high_720h"] = raw["high"].astype(float).shift(1).rolling(720, min_periods=720).max()
        context["signal_open_time"] = context.index
        selected = group[["trade_id", "signal_snapshot_time", "signal_open_time", "event_high_price"]].merge(
            context.reset_index(drop=True), on="signal_open_time", how="left", validate="many_to_one"
        )
        selected["event_is_prior_7d_high"] = selected["event_high_price"] >= selected["coin_prior_high_168h"]
        selected["event_is_prior_30d_high"] = selected["event_high_price"] >= selected["coin_prior_high_720h"]
        pieces.append(selected.drop(columns=["signal_open_time", "event_high_price"]))
    return pd.concat(pieces, ignore_index=True)


def _secondary_outcomes(signals: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    result = signals.copy()
    lookup = snapshots[["trade_id", "snapshot_time", "close"]].rename(
        columns={"snapshot_time": "target_time", "close": "target_close"}
    )
    for hours in (48, 72):
        target = result[["trade_id", "signal_snapshot_time", "signal_close"]].copy()
        target["target_time"] = target["signal_snapshot_time"] + pd.Timedelta(hours=hours)
        target = target.merge(lookup, on=["trade_id", "target_time"], how="left", validate="many_to_one")
        result[f"future_close_return_{hours}h"] = target["target_close"] / target["signal_close"] - 1.0
    last = snapshots.sort_values("snapshot_time").groupby("trade_id", sort=False).tail(1)[
        ["trade_id", "close"]
    ].rename(columns={"close": "mandate_last_close"})
    result = result.merge(last, on="trade_id", how="left", validate="many_to_one")
    result["return_to_mandate_end"] = result["mandate_last_close"] / result["signal_close"] - 1.0
    result["event_high_breached_24h"] = (
        result["signal_close"] * (1.0 + result["future_mae_24h"])
        >= result["event_high_price"]
    )
    return result


def build_context_dataset() -> pd.DataFrame:
    all_signals = pd.read_parquet(SIGNALS_PATH)
    signals = all_signals.loc[all_signals["variant"] == "midpoint_only"].copy()
    snapshots = pd.read_parquet(SNAPSHOTS_PATH)
    signals = _secondary_outcomes(signals, snapshots)
    btc = _asset_context("BTCUSDT")
    eth = _asset_context("ETHUSDT")
    book = _short_book_context(snapshots)
    daily = _daily_breadth()
    coin = _coin_context(signals)
    frame = signals.merge(btc, on="signal_snapshot_time", how="left", validate="many_to_one")
    frame = frame.merge(eth, on="signal_snapshot_time", how="left", validate="many_to_one")
    frame = frame.merge(book, on="signal_snapshot_time", how="left", validate="many_to_one")
    frame = frame.merge(coin, on=["trade_id", "signal_snapshot_time"], how="left", validate="one_to_one")
    frame["context_day"] = frame["signal_snapshot_time"].dt.floor("D")
    frame = frame.merge(daily, on="context_day", how="left", validate="many_to_one")
    frame["eth_minus_btc_24h"] = frame["eth_return_24h"] - frame["btc_return_24h"]
    frame["eth_minus_btc_168h"] = frame["eth_return_168h"] - frame["btc_return_168h"]
    frame["eth_minus_btc_720h"] = frame["eth_return_720h"] - frame["btc_return_720h"]
    frame["coin_minus_btc_24h"] = frame["coin_return_24h"] - frame["btc_return_24h"]
    frame["coin_minus_btc_168h"] = frame["coin_return_168h"] - frame["btc_return_168h"]
    frame["event_size"] = frame["event_high_price"] / frame["impulse_origin_price"] - 1.0
    frame["event_high_stop_distance"] = frame["event_high_price"] / frame["signal_close"] - 1.0
    frame["signal_hour_utc"] = frame["signal_snapshot_time"].dt.hour
    frame["weekend"] = frame["signal_snapshot_time"].dt.dayofweek >= 5
    if bool((frame["signal_snapshot_time"] >= IS_END).any()):
        raise AssertionError("regime atlas entered frozen OOS")
    return frame


def registered_hypotheses(frame: pd.DataFrame) -> tuple[list[Hypothesis], pd.DataFrame]:
    values: dict[str, pd.Series] = {}
    specs: list[Hypothesis] = []

    def add(name: str, family: str, description: str, condition: pd.Series) -> None:
        specs.append(Hypothesis(name, family, description))
        values[name] = condition.fillna(False).astype(bool)

    add("btc_below_30d_mean", "btc_trend", "BTC close below causal 30d mean.", frame["btc_close_vs_mean_720h"] < 0)
    add("btc_above_30d_mean", "btc_trend", "BTC close above causal 30d mean.", frame["btc_close_vs_mean_720h"] >= 0)
    add("btc_fast_below_slow", "btc_trend", "BTC 7d mean below 30d mean.", frame["btc_fast_minus_slow_mean"] < 0)
    add("btc_fast_above_slow", "btc_trend", "BTC 7d mean above 30d mean.", frame["btc_fast_minus_slow_mean"] >= 0)
    add("btc_24h_down", "btc_impulse", "BTC 24h return negative.", frame["btc_return_24h"] < 0)
    add("btc_7d_down", "btc_impulse", "BTC 7d return negative.", frame["btc_return_168h"] < 0)
    add("btc_30d_down", "btc_impulse", "BTC 30d return negative.", frame["btc_return_720h"] < 0)
    add("btc_drawdown_30d_gt10", "btc_impulse", "BTC more than 10% below 30d high.", frame["btc_drawdown_high_720h"] <= -0.10)
    add("btc_crossed_below_30d_recent", "btc_transition", "BTC crossed below 30d mean in last 72h.", frame["btc_crossed_below_30d_recent72h"])
    add("btc_crossed_above_30d_recent", "btc_transition", "BTC crossed above 30d mean in last 72h.", frame["btc_crossed_above_30d_recent72h"])
    add("btc_vol_expansion", "btc_volatility", "BTC 7d/30d volatility ratio above 1.25.", frame["btc_vol_ratio_7d_30d"] > 1.25)
    add("btc_vol_compression", "btc_volatility", "BTC 7d/30d volatility ratio below 0.75.", frame["btc_vol_ratio_7d_30d"] < 0.75)
    add("breadth_7d_bear", "market_breadth", "Less than 40% of universe positive over 7d.", frame["market_positive_share_7d"] < 0.40)
    add("breadth_7d_bull", "market_breadth", "More than 60% of universe positive over 7d.", frame["market_positive_share_7d"] > 0.60)
    add("breadth_below_30d_majority", "market_breadth", "Less than 40% above 30d mean.", frame["market_above_mean_share_30d"] < 0.40)
    add("breadth_above_30d_majority", "market_breadth", "More than 60% above 30d mean.", frame["market_above_mean_share_30d"] > 0.60)
    add("breadth_deteriorating", "market_breadth", "1d positive share below 7d share.", frame["market_breadth_acceleration"] < 0)
    add("dispersion_expansion", "market_dispersion", "1d dispersion above 1.25x trailing median.", frame["market_dispersion_ratio_1d_90d"] > 1.25)
    add("book_synchronised_squeeze", "book_synchrony", "At least 20% of active shorts warning together.", frame["book_core_z2_share"] >= 0.20)
    add("book_isolated_anomaly", "book_synchrony", "No more than 10% of active shorts warning.", frame["book_core_z2_share"] <= 0.10)
    add("book_majority_up_3h", "book_synchrony", "More than 60% of shorts up over 3h.", frame["book_positive_3h_share"] > 0.60)
    add("book_not_broadly_up_3h", "book_synchrony", "Less than 40% of shorts up over 3h.", frame["book_positive_3h_share"] < 0.40)
    add("eth_underperforms_btc_7d", "relative_market", "ETH underperforms BTC over 7d.", frame["eth_minus_btc_168h"] < 0)
    add("eth_outperforms_btc_7d", "relative_market", "ETH outperforms BTC over 7d.", frame["eth_minus_btc_168h"] >= 0)
    add("coin_underperforms_btc_24h", "coin_relative", "Coin underperforms BTC over 24h.", frame["coin_minus_btc_24h"] < 0)
    add("coin_underperforms_btc_7d", "coin_relative", "Coin underperforms BTC over 7d.", frame["coin_minus_btc_168h"] < 0)
    add("event_is_7d_high", "event_geometry", "Event high reaches prior 7d high.", frame["event_is_prior_7d_high"])
    add("event_is_30d_high", "event_geometry", "Event high reaches prior 30d high.", frame["event_is_prior_30d_high"])
    add("event_size_gt10", "event_geometry", "Causal anomaly event exceeds 10%.", frame["event_size"] >= 0.10)
    add("event_size_gt20", "event_geometry", "Causal anomaly event exceeds 20%.", frame["event_size"] >= 0.20)
    add("event_high_stop_gt10", "event_geometry", "Event-high stop distance exceeds 10%.", frame["event_high_stop_distance"] >= 0.10)
    add("early_reentry_lte12h", "event_timing", "Midpoint return within 12h.", frame["signal_delay_hours"] <= 12)
    add("middle_reentry_13_24h", "event_timing", "Midpoint return from 13h through 24h.", frame["signal_delay_hours"].between(13, 24))
    add("late_reentry_gt24h", "event_timing", "Midpoint return after 24h.", frame["signal_delay_hours"] > 24)
    add("sell_flow_taker_lte45", "event_flow", "3h taker-buy share at most 45%.", frame["taker_buy_share_3h"] <= 0.45)
    add("activity_below_baseline", "event_flow", "Quote and trade activity z-scores both below zero.", (frame["quote_volume_z60"] < 0) & (frame["trade_count_z60"] < 0))
    add("weekend", "calendar", "Signal occurs on weekend.", frame["weekend"])
    add("asia_session", "calendar", "Signal snapshot hour 00-07 UTC.", frame["signal_hour_utc"].between(0, 7))
    add("europe_session", "calendar", "Signal snapshot hour 08-15 UTC.", frame["signal_hour_utc"].between(8, 15))
    add("us_session", "calendar", "Signal snapshot hour 16-23 UTC.", frame["signal_hour_utc"].between(16, 23))
    add("btc_up_breadth_weak", "divergence", "BTC up 7d while median universe return is negative.", (frame["btc_return_168h"] > 0) & (frame["market_median_return_7d"] < 0))
    add("btc_down_breadth_improving", "divergence", "BTC down 7d while daily breadth improves.", (frame["btc_return_168h"] < 0) & (frame["market_breadth_acceleration"] > 0))
    add("calm_btc_violent_event", "divergence", "BTC volatility compressed while event exceeds 10%.", (frame["btc_vol_ratio_7d_30d"] < 0.75) & (frame["event_size"] >= 0.10))
    return specs, pd.DataFrame(values, index=frame.index)


def _bootstrap_mean(values: np.ndarray, rng: np.random.Generator, simulations: int = 500) -> tuple[float, float, float]:
    if len(values) == 0:
        return np.nan, np.nan, np.nan
    draws = rng.choice(values, size=(simulations, len(values)), replace=True).mean(axis=1)
    q05, q95 = np.quantile(draws, [0.05, 0.95])
    p_nonnegative = (float((draws >= 0.0).sum()) + 1.0) / (simulations + 1.0)
    return float(q05), float(q95), p_nonnegative


def _evaluate_mask(
    frame: pd.DataFrame,
    mask: pd.Series,
    *,
    name: str,
    family: str,
    kind: str,
    rng: np.random.Generator,
) -> dict[str, object]:
    row: dict[str, object] = {"hypothesis": name, "family": family, "kind": kind}
    support_ok = True
    means: list[float] = []
    negative_shares: list[float] = []
    breach_shares: list[float] = []
    for year in (2023, 2024, 2025):
        year_all = frame["entry_year"] == year
        selected = frame.loc[year_all & mask]
        coverage = len(selected) / int(year_all.sum()) if year_all.any() else np.nan
        values = selected["future_close_return_24h"].dropna()
        mean = float(values.mean()) if len(values) else np.nan
        negative = float((values < 0.0).mean()) if len(values) else np.nan
        breach = float(selected["event_high_breached_24h"].mean()) if len(selected) else np.nan
        row.update({
            f"n_{year}": len(values),
            f"coverage_{year}": coverage,
            f"mean_return_24h_{year}": mean,
            f"negative_share_{year}": negative,
            f"event_high_breach_{year}": breach,
            f"mean_return_to_end_{year}": float(selected["return_to_mandate_end"].mean()) if len(selected) else np.nan,
        })
        support_ok &= len(values) >= 30 and coverage >= 0.15
        means.append(mean)
        negative_shares.append(negative)
        breach_shares.append(breach)
    pooled = frame.loc[mask, "future_close_return_24h"].dropna().to_numpy(dtype=float)
    q05, q95, p_value = _bootstrap_mean(pooled, rng)
    finite_means = [value for value in means if np.isfinite(value)]
    finite_negative = [value for value in negative_shares if np.isfinite(value)]
    finite_breaches = [value for value in breach_shares if np.isfinite(value)]
    row.update({
        "n_total": len(pooled),
        "support_ok": support_ok,
        "worst_year_mean_return_24h": max(finite_means) if finite_means else np.nan,
        "best_year_mean_return_24h": min(finite_means) if finite_means else np.nan,
        "min_negative_share": min(finite_negative) if finite_negative else np.nan,
        "max_event_high_breach": max(finite_breaches) if finite_breaches else np.nan,
        "all_year_means_negative": bool(all(value < 0.0 for value in means)),
        "bootstrap_mean_q05": q05,
        "bootstrap_mean_q95": q95,
        "bootstrap_p_nonnegative": p_value,
    })
    return row


def _bh_qvalues(p_values: pd.Series) -> pd.Series:
    values = p_values.fillna(1.0).to_numpy(dtype=float)
    order = np.argsort(values)
    ranked = values[order]
    adjusted = ranked * len(values) / np.arange(1, len(values) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1].clip(0.0, 1.0)
    result = np.empty(len(values), dtype=float)
    result[order] = adjusted
    return pd.Series(result, index=p_values.index)


def evaluate_hypothesis_grid(
    frame: pd.DataFrame,
    specs: list[Hypothesis],
    filters: pd.DataFrame,
) -> pd.DataFrame:
    rng = np.random.default_rng(20_260_805)
    rows: list[dict[str, object]] = []
    by_name = {spec.name: spec for spec in specs}
    for spec in specs:
        rows.append(_evaluate_mask(frame, filters[spec.name], name=spec.name, family=spec.family, kind="single", rng=rng))
    for left_i, left in enumerate(specs):
        for right in specs[left_i + 1 :]:
            if left.family == right.family:
                continue
            name = f"{left.name}__AND__{right.name}"
            rows.append(_evaluate_mask(
                frame,
                filters[left.name] & filters[right.name],
                name=name,
                family=f"{left.family}+{right.family}",
                kind="pair",
                rng=rng,
            ))
    result = pd.DataFrame(rows)
    result["fdr_q"] = _bh_qvalues(result["bootstrap_p_nonnegative"])
    result["robust_candidate"] = (
        result["support_ok"]
        & result["all_year_means_negative"]
        & (result["min_negative_share"] > 0.50)
        & (result["max_event_high_breach"] <= 0.25)
        & (result["bootstrap_mean_q95"] < 0.0)
        & (result["fdr_q"] <= 0.10)
    )
    return result.sort_values(
        ["robust_candidate", "support_ok", "worst_year_mean_return_24h", "fdr_q"],
        ascending=[False, False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def regime_cube(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["btc_30d_state"] = np.where(work["btc_close_vs_mean_720h"] < 0.0, "below", "above")
    work["breadth_7d_state"] = np.select(
        [work["market_positive_share_7d"] < 0.40, work["market_positive_share_7d"] > 0.60],
        ["bear", "bull"],
        default="neutral",
    )
    work["book_sync_state"] = np.select(
        [work["book_core_z2_share"] <= 0.10, work["book_core_z2_share"] >= 0.20],
        ["isolated", "broad"],
        default="intermediate",
    )
    return work.groupby(
        ["entry_year", "btc_30d_state", "breadth_7d_state", "book_sync_state"],
        observed=True,
    ).agg(
        events=("future_close_return_24h", "size"),
        mean_return_24h=("future_close_return_24h", "mean"),
        negative_share=("future_close_return_24h", lambda values: float((values < 0.0).mean())),
        event_high_breach=("event_high_breached_24h", "mean"),
        mean_return_to_end=("return_to_mandate_end", "mean"),
    ).reset_index()


def main() -> None:
    for path in (SIGNALS_PATH, SNAPSHOTS_PATH, PROTOCOL):
        if not path.exists():
            raise FileNotFoundError(path)
    ATLAS_OUT.mkdir(parents=True, exist_ok=True)
    print("building causal BTC/ETH/breadth/book/event context ...", flush=True)
    frame = build_context_dataset()
    specs, filters = registered_hypotheses(frame)
    dictionary = pd.DataFrame([
        {"name": spec.name, "family": spec.family, "description": spec.description}
        for spec in specs
    ])
    context = pd.concat([frame, filters.add_prefix("hyp_")], axis=1)
    context.to_parquet(ATLAS_OUT / "event_context.parquet", index=False)
    dictionary.to_csv(ATLAS_OUT / "feature_dictionary.csv", index=False)
    print(f"  events={len(frame)} hypotheses={len(specs)}", flush=True)

    print("evaluating single filters, cross-family pairs, bootstrap and FDR ...", flush=True)
    grid = evaluate_hypothesis_grid(frame, specs, filters)
    cube = regime_cube(frame)
    candidates = grid.loc[grid["robust_candidate"]].copy()
    rejected = grid.loc[~grid["robust_candidate"]].copy()
    for name, artifact in (
        ("hypothesis_grid", grid),
        ("regime_cube", cube),
        ("robust_candidates", candidates),
        ("rejected_hypotheses", rejected),
    ):
        artifact.to_parquet(ATLAS_OUT / f"{name}.parquet", index=False)
        artifact.to_csv(ATLAS_OUT / f"{name}.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "signals": str(SIGNALS_PATH),
        "signals_sha256": hashlib.sha256(SIGNALS_PATH.read_bytes()).hexdigest(),
        "snapshots": str(SNAPSHOTS_PATH),
        "snapshot_rows": 499_540,
        "event_count": len(frame),
        "registered_single_hypotheses": len(specs),
        "evaluated_grid_rows": len(grid),
        "robust_candidate_count": len(candidates),
        "is_end_exclusive": IS_END.isoformat(),
        "hourly_source_count": len(list(HOURLY_ROOT.glob("*.parquet"))),
    }
    (ATLAS_OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(f"  grid={len(grid)} robust_candidates={len(candidates)}", flush=True)
    columns = [
        "hypothesis", "kind", "n_2023", "n_2024", "n_2025",
        "mean_return_24h_2023", "mean_return_24h_2024", "mean_return_24h_2025",
        "worst_year_mean_return_24h", "min_negative_share", "max_event_high_breach",
        "bootstrap_mean_q95", "fdr_q", "robust_candidate",
    ]
    print(grid.loc[:, columns].head(20).to_string(index=False), flush=True)
    print(f"wrote {ATLAS_OUT}", flush=True)


if __name__ == "__main__":
    main()
