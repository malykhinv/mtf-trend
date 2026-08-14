"""Causal coin-level audit for the top-liquid directional regime hypothesis."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.market_context.sessions import block_seq_for_ms
from anomaly_science.strategy.xsect_momentum.research import panel as pn


OUT = Path(".output/results/top_liquid_regime_directional_v1")
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
PROTOCOL = Path("docs/strategies/top_liquid_regime_directional_protocol_v1.md")
IS_START = pd.Timestamp("2023-01-01", tz="UTC")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")
UNIVERSE_SIZES = (10, 20, 30)
BREADTH_THRESHOLDS = (0.55, 0.60, 0.65, 0.70)
EMA_HOURS = (24, 72, 168)
HORIZONS = (6, 12, 24, 48, 72, 120)
RULES = (
    "strict_all", "score_4_of_5", "score_3_of_5", "session_confirmed", "transition",
    "clean_transition", "smooth_transition", "vertical_transition", "shock_transition",
    "exhaustion_filtered_transition",
)
GEOMETRY_HOURS = (6, 12, 24)
GEOMETRY_METRICS = (
    "path_efficiency",
    "minimum_efficiency",
    "directional_purity",
    "verticality",
    "minimum_verticality",
    "aligned_candle_share",
    "terminal_acceleration",
)
MARKET_GEOMETRY_FIELDS = tuple(
    f"{metric}_{hours}h" for hours in GEOMETRY_HOURS for metric in GEOMETRY_METRICS
)


def _load_hourly(symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.date_range(IS_START, IS_END - pd.Timedelta(hours=1), freq="h")
    fields: dict[str, dict[str, pd.Series]] = {name: {} for name in ("open", "high", "low", "close")}
    for number, symbol in enumerate(symbols, start=1):
        path = HOURLY_ROOT / f"{symbol}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close"])
        timestamp = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
        frame.index = timestamp
        frame = frame.loc[(frame.index >= IS_START) & (frame.index < IS_END)]
        for field in fields:
            fields[field][symbol] = frame[field].reindex(index)
        if number % 25 == 0:
            print(f"loaded hourly {number}/{len(symbols)}", flush=True)
    matrices = tuple(pd.DataFrame(fields[field], index=index) for field in ("open", "high", "low", "close"))
    return matrices  # type: ignore[return-value]


def _hourly_universe(mask: pd.DataFrame, hourly_index: pd.DatetimeIndex) -> pd.DataFrame:
    known_date = hourly_index.normalize() - pd.Timedelta(days=1)
    return mask.reindex(known_date).set_axis(hourly_index).fillna(False)


def _session_features(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    eligible: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series]:
    timestamp_ms = (close.index.astype("int64") // 1_000_000).to_numpy(dtype=np.int64)
    seq = block_seq_for_ms(timestamp_ms)
    day = timestamp_ms // 86_400_000
    session_id = pd.Series(day * 5 + seq, index=close.index)
    first_open = open_.groupby(session_id, sort=False).transform("first")
    current_returns = close / first_open - 1.0
    market_current = current_returns.where(eligible).median(axis=1)
    session_final = market_current.groupby(session_id, sort=False).last()
    previous_map = session_final.shift(1)
    previous_market = session_id.map(previous_map)
    completed = session_id.groupby(session_id, sort=False).cumcount() + 1
    current_returns = current_returns.where(completed >= 3, axis=0)
    return current_returns, previous_market


def _deduplicate(signal: pd.Series, reset_false_hours: int = 6) -> pd.Series:
    values = signal.fillna(False).to_numpy(dtype=bool)
    emitted = np.zeros(len(values), dtype=bool)
    ready = True
    false_hours = reset_false_hours
    for index, active in enumerate(values):
        if active:
            if ready:
                emitted[index] = True
                ready = False
            false_hours = 0
        else:
            false_hours += 1
            if false_hours >= reset_false_hours:
                ready = True
    return pd.Series(emitted, index=signal.index)


def _market_path_geometry(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    eligible: pd.DataFrame,
) -> dict[str, dict[str, pd.Series]]:
    log_return = np.log(close).diff()
    components = pd.DataFrame({
        "btc": log_return["BTCUSDT"],
        "eth": log_return["ETHUSDT"],
        "market": log_return.where(eligible).median(axis=1),
    })
    body = pd.DataFrame({
        "btc": np.log(close["BTCUSDT"] / open_["BTCUSDT"]),
        "eth": np.log(close["ETHUSDT"] / open_["ETHUSDT"]),
        "market": np.log(close / open_).where(eligible).median(axis=1),
    })
    prior_vol = components.shift(1).rolling(24 * 30, min_periods=24 * 10).std()
    result: dict[str, dict[str, pd.Series]] = {"long": {}, "short": {}}
    for hours in GEOMETRY_HOURS:
        net = components.rolling(hours, min_periods=hours).sum()
        absolute = components.abs().rolling(hours, min_periods=hours).sum()
        efficiency = net.abs() / absolute.replace(0.0, np.nan)
        for direction, sign in (("long", 1.0), ("short", -1.0)):
            purity = (sign * components > 0.0).rolling(hours, min_periods=hours).mean()
            verticality = sign * net / (prior_vol * np.sqrt(hours)).replace(0.0, np.nan)
            aligned = (sign * body > 0.0).rolling(hours, min_periods=hours).mean()
            acceleration = sign * components.rolling(3, min_periods=3).sum() / net.abs().replace(0.0, np.nan)
            target = result[direction]
            target[f"path_efficiency_{hours}h"] = efficiency.median(axis=1)
            target[f"minimum_efficiency_{hours}h"] = efficiency.min(axis=1)
            target[f"directional_purity_{hours}h"] = purity.median(axis=1)
            target[f"verticality_{hours}h"] = verticality.median(axis=1)
            target[f"minimum_verticality_{hours}h"] = verticality.min(axis=1)
            target[f"aligned_candle_share_{hours}h"] = aligned.median(axis=1)
            target[f"terminal_acceleration_{hours}h"] = acceleration.median(axis=1)
    return result


def _direction_signals(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    eligible: pd.DataFrame,
    universe_n: int,
    breadth_threshold: float,
    ema_hours: int,
    geometry: dict[str, dict[str, pd.Series]],
) -> dict[tuple[str, str], pd.Series]:
    returns = {hours: close / close.shift(hours) - 1.0 for hours in (6, 12, 24)}
    market_returns = {hours: value.where(eligible).median(axis=1) for hours, value in returns.items()}
    positive_breadth = {
        hours: value.gt(0.0).where(value.notna() & eligible).mean(axis=1)
        for hours, value in returns.items()
    }
    ema = close.ewm(span=ema_hours, adjust=False, min_periods=ema_hours).mean()
    above_ema = close.gt(ema).where(close.notna() & eligible).mean(axis=1)
    session_returns, previous_market_session = _session_features(
        open_=open_.reindex(columns=close.columns), close=close, eligible=eligible,
    )
    btc_session = session_returns.get("BTCUSDT", pd.Series(np.nan, index=close.index))
    eth_session = session_returns.get("ETHUSDT", pd.Series(np.nan, index=close.index))
    market_session = session_returns.where(eligible).median(axis=1)
    out: dict[tuple[str, str], pd.Series] = {}
    for direction, sign in (("long", 1.0), ("short", -1.0)):
        btc_vote = pd.concat([sign * returns[h]["BTCUSDT"] > 0.0 for h in (6, 12, 24)], axis=1).all(axis=1)
        eth_vote = pd.concat([sign * returns[h]["ETHUSDT"] > 0.0 for h in (6, 12, 24)], axis=1).all(axis=1)
        market_vote = pd.concat([sign * market_returns[h] > 0.0 for h in (6, 12, 24)], axis=1).all(axis=1)
        if direction == "long":
            breadth_vote = pd.concat([positive_breadth[h] >= breadth_threshold for h in (6, 12, 24)], axis=1).all(axis=1)
            ema_vote = above_ema >= breadth_threshold
            session_vote = (
                (btc_session > 0.0) & (eth_session > 0.0) & (market_session > 0.0)
                & (previous_market_session > 0.0)
            )
        else:
            breadth_vote = pd.concat([positive_breadth[h] <= 1.0 - breadth_threshold for h in (6, 12, 24)], axis=1).all(axis=1)
            ema_vote = above_ema <= 1.0 - breadth_threshold
            session_vote = (
                (btc_session < 0.0) & (eth_session < 0.0) & (market_session < 0.0)
                & (previous_market_session < 0.0)
            )
        votes = pd.concat([btc_vote, eth_vote, market_vote, breadth_vote, ema_vote], axis=1).sum(axis=1)
        score_4 = votes >= 4
        transition = score_4 & ~score_4.shift(1, fill_value=False)
        efficiency = geometry[direction]["path_efficiency_12h"]
        purity = geometry[direction]["directional_purity_12h"]
        verticality = geometry[direction]["verticality_12h"]
        acceleration = geometry[direction]["terminal_acceleration_12h"]
        clean = transition & (efficiency >= 0.70) & (purity >= 0.70)
        raw = {
            "strict_all": votes == 5,
            "score_4_of_5": score_4,
            "score_3_of_5": votes >= 3,
            "session_confirmed": score_4 & session_vote,
            "transition": transition,
            "clean_transition": clean,
            "smooth_transition": clean & verticality.between(0.75, 2.0),
            "vertical_transition": transition & (verticality >= 2.0) & (efficiency >= 0.55),
            "shock_transition": transition & (verticality >= 2.5) & (purity >= 0.70),
            "exhaustion_filtered_transition": transition & ~(
                (verticality >= 3.0) & (acceleration >= 0.65)
            ),
        }
        for rule, signal in raw.items():
            out[(rule, direction)] = _deduplicate(signal)
    return out


def _event_outcomes(
    signals: pd.DataFrame,
    universe_masks: dict[int, pd.DataFrame],
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
) -> pd.DataFrame:
    log_return = np.log(close).diff()
    prior_vol = log_return.shift(1).rolling(24 * 30, min_periods=24 * 10).std()
    rows: list[dict[str, object]] = []
    for event in signals.itertuples(index=False):
        signal_time = pd.Timestamp(event.signal_time)
        signal_index = close.index.get_loc(signal_time)
        entry_index = signal_index + 1
        if entry_index >= len(close):
            continue
        entry_time = close.index[entry_index]
        eligible = universe_masks[event.universe_n].loc[signal_time]
        symbols = eligible.index[eligible].tolist()
        for symbol in symbols:
            entry_price = float(open_.iat[entry_index, open_.columns.get_loc(symbol)])
            if not np.isfinite(entry_price) or entry_price <= 0.0:
                continue
            sign = 1.0 if event.direction == "long" else -1.0
            base = {
                "path_id": event.path_id,
                "signal_time": signal_time,
                "entry_time": entry_time,
                "entry_year": entry_time.year,
                "entry_session": int(block_seq_for_ms(np.asarray([int(entry_time.value // 1_000_000)]))[0]),
                "universe_n": event.universe_n,
                "direction": event.direction,
                "symbol": symbol,
                "asset_role": "context" if symbol in {"BTCUSDT", "ETHUSDT"} else "basket",
                **{
                    f"market_{field}": getattr(event, field)
                    for field in MARKET_GEOMETRY_FIELDS
                },
            }
            column = close.columns.get_loc(symbol)
            for geometry_hours in GEOMETRY_HOURS:
                start_index = signal_index - geometry_hours + 1
                if start_index < 1:
                    continue
                path_return = log_return.iloc[start_index : signal_index + 1, column]
                net_log_return = float(path_return.sum())
                absolute_path = float(path_return.abs().sum())
                volatility = float(prior_vol.iat[signal_index, column])
                candle_body = np.log(
                    close.iloc[start_index : signal_index + 1, column]
                    / open_.iloc[start_index : signal_index + 1, column]
                )
                base[f"coin_path_efficiency_{geometry_hours}h"] = (
                    abs(net_log_return) / absolute_path if absolute_path > 0.0 else np.nan
                )
                base[f"coin_directional_purity_{geometry_hours}h"] = float(
                    (sign * path_return > 0.0).mean()
                )
                base[f"coin_verticality_{geometry_hours}h"] = (
                    sign * net_log_return / (volatility * np.sqrt(geometry_hours))
                    if np.isfinite(volatility) and volatility > 0.0 else np.nan
                )
                base[f"coin_aligned_candle_share_{geometry_hours}h"] = float(
                    (sign * candle_body > 0.0).mean()
                )
                base[f"coin_terminal_acceleration_{geometry_hours}h"] = (
                    sign * float(path_return.tail(3).sum()) / abs(net_log_return)
                    if net_log_return else np.nan
                )
            for horizon in HORIZONS:
                end_index = entry_index + horizon - 1
                if end_index >= len(close):
                    continue
                exit_price = float(close.iat[end_index, column])
                path_high = high.iloc[entry_index : end_index + 1, column]
                path_low = low.iloc[entry_index : end_index + 1, column]
                if not np.isfinite(exit_price) or path_high.isna().any() or path_low.isna().any():
                    continue
                signed_return = sign * (exit_price / entry_price - 1.0)
                if sign > 0.0:
                    mfe = float(path_high.max() / entry_price - 1.0)
                    mae = float(path_low.min() / entry_price - 1.0)
                else:
                    mfe = float(1.0 - path_low.min() / entry_price)
                    mae = float(1.0 - path_high.max() / entry_price)
                rows.append({
                    **base,
                    "horizon_hours": horizon,
                    "signed_return": signed_return,
                    "mfe": mfe,
                    "mae": mae,
                    "giveback": mfe - signed_return,
                })
    return pd.DataFrame(rows)


def _metric_table(outcomes: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for keys, group in outcomes.groupby(group_columns, dropna=False, sort=False):
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        values = group["signed_return"]
        count = max(1, int(np.ceil(len(values) * 0.01)))
        event_column = "path_id" if "path_id" in group else "event_id"
        rows.append({
            **dict(zip(group_columns, key_tuple, strict=True)),
            "events": int(group[event_column].nunique()),
            "observations": len(group),
            "mean_signed_return": float(values.mean()),
            "median_signed_return": float(values.median()),
            "win_share": float((values > 0.0).mean()),
            "mean_mfe": float(group["mfe"].mean()),
            "mean_mae": float(group["mae"].mean()),
            "mean_giveback": float(group["giveback"].mean()),
            "top1_drop_mean_return": float((values.sum() - values.nlargest(count).sum()) / len(values)),
        })
    return pd.DataFrame(rows)


def _configured_metrics(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    basket_events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config_columns = [
        "universe_n", "breadth_threshold", "ema_hours", "rule", "direction",
    ]
    coin_index = outcomes.set_index("path_id", drop=False).sort_index()
    basket_index = basket_events.set_index("path_id", drop=False).sort_index()
    coin_parts: list[pd.DataFrame] = []
    basket_parts: list[pd.DataFrame] = []
    grouped = signals.groupby(config_columns, sort=False, observed=True)
    for number, (keys, group) in enumerate(grouped, start=1):
        config = dict(zip(config_columns, keys, strict=True))
        path_ids = group["path_id"].drop_duplicates().to_numpy()
        coin_path_ids = np.intersect1d(path_ids, coin_index.index.unique(), assume_unique=False)
        basket_path_ids = np.intersect1d(path_ids, basket_index.index.unique(), assume_unique=False)
        if len(coin_path_ids) == 0 or len(basket_path_ids) == 0:
            continue
        coin = coin_index.loc[coin_path_ids]
        coin_metric = _metric_table(coin, ["symbol", "entry_year", "horizon_hours"])
        for column, value in config.items():
            coin_metric[column] = value
        coin_parts.append(coin_metric)
        basket = basket_index.loc[basket_path_ids]
        basket_metric = _metric_table(basket, ["entry_year", "horizon_hours"])
        for column, value in config.items():
            basket_metric[column] = value
        basket_parts.append(basket_metric)
        if number % 100 == 0:
            print(f"metrics complete configurations={number}/{grouped.ngroups}", flush=True)
    metric_order = config_columns
    coin_metrics = pd.concat(coin_parts, ignore_index=True)
    basket_metrics = pd.concat(basket_parts, ignore_index=True)
    return (
        coin_metrics[metric_order + [c for c in coin_metrics if c not in metric_order]],
        basket_metrics[metric_order + [c for c in basket_metrics if c not in metric_order]],
    )


def _write_aggregates(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    loaded_symbol_count: int | None,
) -> None:
    basket_events = outcomes.loc[outcomes["asset_role"] == "basket"].groupby(
        ["path_id", "signal_time", "entry_year", "universe_n", "direction", "horizon_hours"],
        as_index=False,
    ).agg(
        signed_return=("signed_return", "mean"),
        mfe=("mfe", "mean"),
        mae=("mae", "mean"),
        giveback=("giveback", "mean"),
        coins=("symbol", "nunique"),
    )
    coin_metrics, basket_metrics = _configured_metrics(signals, outcomes, basket_events)
    coin_metrics.to_parquet(OUT / "coin_metrics.parquet", index=False)
    coin_metrics.to_csv(OUT / "coin_metrics.csv", index=False)
    basket_events.to_parquet(OUT / "basket_events.parquet", index=False)
    basket_metrics.to_parquet(OUT / "basket_metrics.parquet", index=False)
    basket_metrics.to_csv(OUT / "basket_metrics.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "universe_contract": "point_in_time_trailing_liquidity_proxy_not_market_cap",
        "hourly_source_file_count": len(list(HOURLY_ROOT.glob("*.parquet"))),
        "loaded_symbol_count": loaded_symbol_count,
        "outcome_symbol_count": int(outcomes["symbol"].nunique()),
        "signal_count": len(signals),
        "unique_path_count": int(signals["path_id"].nunique()),
        "paths_with_outcomes": int(outcomes["path_id"].nunique()),
        "coin_outcome_rows": len(outcomes),
        "is_end_exclusive": IS_END.isoformat(),
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"completed signals={len(signals)} paths={signals['path_id'].nunique()} "
        f"outcomes={len(outcomes)}",
        flush=True,
    )


def main(*, aggregate_only: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if aggregate_only:
        signals = pd.read_parquet(OUT / "signals.parquet")
        outcomes = pd.read_parquet(OUT / "coin_outcomes.parquet")
        _write_aggregates(signals, outcomes, loaded_symbol_count=None)
        return
    panel = pn.load_panel(is_only=True, source_glob=str(Path(pn.KLINES_DAILY_PANEL)), is_end=IS_END)
    quote_volume = pn.pivot(panel, "quote_volume")
    daily_masks = {
        size: pn.build_universe_mask(panel, quote_volume, size, liquidity_lb=30, min_age_days=180)
        for size in UNIVERSE_SIZES
    }
    symbols = sorted(set(daily_masks[30].columns[daily_masks[30].any()]) | {"BTCUSDT", "ETHUSDT"})
    open_, high, low, close = _load_hourly(symbols)
    hourly_masks = {
        size: _hourly_universe(mask.reindex(columns=close.columns, fill_value=False), close.index)
        for size, mask in daily_masks.items()
    }
    signal_rows: list[dict[str, object]] = []
    event_id = 0
    for universe_n in UNIVERSE_SIZES:
        eligible = hourly_masks[universe_n]
        geometry = _market_path_geometry(open_, close, eligible)
        for threshold in BREADTH_THRESHOLDS:
            for ema_hours in EMA_HOURS:
                signals = _direction_signals(
                    open_, close, eligible, universe_n, threshold, ema_hours, geometry,
                )
                for (rule, direction), signal in signals.items():
                    for timestamp in signal.index[signal]:
                        path_geometry = {
                            name: float(values.loc[timestamp])
                            for name, values in geometry[direction].items()
                        }
                        signal_rows.append({
                            "event_id": event_id,
                            "signal_time": timestamp,
                            "universe_n": universe_n,
                            "breadth_threshold": threshold,
                            "ema_hours": ema_hours,
                            "rule": rule,
                            "direction": direction,
                            **path_geometry,
                        })
                        event_id += 1
        print(f"signals complete universe={universe_n}", flush=True)
    signals = pd.DataFrame(signal_rows)
    path_keys = ["signal_time", "universe_n", "direction"]
    paths = signals.sort_values("event_id").drop_duplicates(path_keys).copy()
    paths["path_id"] = np.arange(len(paths), dtype=np.int64)
    signals = signals.merge(paths[path_keys + ["path_id"]], on=path_keys, how="left", validate="many_to_one")
    signals.to_parquet(OUT / "signals.parquet", index=False)
    paths.to_parquet(OUT / "paths.parquet", index=False)
    print(f"building outcomes for signals={len(signals)} unique_paths={len(paths)}", flush=True)
    outcomes = _event_outcomes(paths, hourly_masks, open_, high, low, close)
    outcomes.to_parquet(OUT / "coin_outcomes.parquet", index=False)
    _write_aggregates(signals, outcomes, loaded_symbol_count=len(close.columns))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate-only", action="store_true")
    arguments = parser.parse_args()
    main(aggregate_only=arguments.aggregate_only)
