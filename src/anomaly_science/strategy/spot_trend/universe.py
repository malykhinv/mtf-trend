from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import SpotTrendContractError, UniverseConfig


BAR_COLUMNS = {
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
}
MASTER_COLUMNS = {
    "as_of_date",
    "symbol",
    "base_asset",
    "canonical_asset_id",
    "quote_asset",
    "trading_start_date",
    "trading_end_date",
    "spot_trading_allowed",
    "status",
    "delisting_announcement_date",
    "delisting_date",
    "monitoring_tag",
    "monitoring_tag_known",
    "is_stablecoin",
    "is_leveraged_token",
}


@dataclass(frozen=True, slots=True)
class PointInTimeUniverse:
    """Auditable monthly snapshots plus their daily effective membership."""

    snapshots: pd.DataFrame
    daily_membership: pd.DataFrame
    audit: pd.DataFrame


def validate_daily_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(BAR_COLUMNS.difference(frame.columns))
    if missing:
        raise SpotTrendContractError(f"daily bars missing columns: {missing}")
    bars = frame.copy()
    bars["date"] = pd.to_datetime(bars["date"], utc=True).dt.normalize()
    if bars[["date", "symbol"]].duplicated().any():
        raise SpotTrendContractError("daily bars must be unique by (date, symbol)")
    numeric = sorted(BAR_COLUMNS.difference({"date", "symbol"}))
    for column in numeric:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    invalid_ohlc = (
        bars[["open", "high", "low", "close"]].isna().any(axis=1)
        | (bars[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (bars["high"] < bars[["open", "close"]].max(axis=1))
        | (bars["low"] > bars[["open", "close"]].min(axis=1))
    )
    if invalid_ohlc.any():
        raise SpotTrendContractError("daily bars contain invalid OHLC rows")
    if (bars[["base_volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"]] < 0).any().any():
        raise SpotTrendContractError("daily bars contain negative activity fields")
    if (bars["taker_buy_quote_volume"] > bars["quote_volume"] + 1e-9).any():
        raise SpotTrendContractError("taker_buy_quote_volume cannot exceed quote_volume")
    return bars.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def validate_symbol_master(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(MASTER_COLUMNS.difference(frame.columns))
    if missing:
        raise SpotTrendContractError(f"symbol master missing point-in-time columns: {missing}")
    master = frame.copy()
    date_columns = (
        "as_of_date",
        "trading_start_date",
        "trading_end_date",
        "delisting_announcement_date",
        "delisting_date",
    )
    for column in date_columns:
        master[column] = pd.to_datetime(master[column], utc=True, errors="coerce").dt.normalize()
    if master["as_of_date"].isna().any() or master["trading_start_date"].isna().any():
        raise SpotTrendContractError("as_of_date and trading_start_date are required")
    if master[["as_of_date", "symbol"]].duplicated().any():
        raise SpotTrendContractError("symbol master must be unique by (as_of_date, symbol)")
    bool_columns = (
        "spot_trading_allowed",
        "monitoring_tag",
        "monitoring_tag_known",
        "is_stablecoin",
        "is_leveraged_token",
    )
    for column in bool_columns:
        if master[column].isna().any() or not master[column].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise SpotTrendContractError(f"symbol master {column} must be explicit boolean history")
    if (master["monitoring_tag"] & ~master["monitoring_tag_known"]).any():
        raise SpotTrendContractError("monitoring_tag cannot be true when its point-in-time state is unknown")
    return master.sort_values(["as_of_date", "symbol"], kind="stable").reset_index(drop=True)


def _master_asof(master: pd.DataFrame, snapshot_date: pd.Timestamp) -> pd.DataFrame:
    known = master.loc[master["as_of_date"] <= snapshot_date]
    if known.empty:
        return known.copy()
    return (
        known.sort_values(["symbol", "as_of_date"], kind="stable")
        .groupby("symbol", sort=False, as_index=False)
        .tail(1)
        .copy()
    )


def _reason_list(row: pd.Series, config: UniverseConfig) -> tuple[str, ...]:
    reasons: list[str] = []
    if row["quote_asset"] != config.quote_asset:
        reasons.append("wrong_quote_asset")
    if not bool(row["spot_trading_allowed"]) or str(row["status"]).upper() != "TRADING":
        reasons.append("spot_not_trading")
    if row["trading_start_date"] > row["snapshot_date"]:
        reasons.append("not_listed_yet")
    if pd.notna(row["trading_end_date"]) and row["trading_end_date"] <= row["snapshot_date"]:
        reasons.append("trading_ended")
    if int(row["history_bars"]) < config.minimum_history_bars:
        reasons.append("insufficient_history")
    if not np.isfinite(row["liquidity_30"]) or row["liquidity_30"] < config.minimum_median_quote_volume:
        reasons.append("liquidity_below_threshold")
    if bool(row["is_stablecoin"]):
        reasons.append("stablecoin")
    if bool(row["is_leveraged_token"]):
        reasons.append("leveraged_token")
    if pd.notna(row["delisting_announcement_date"]) and row["delisting_announcement_date"] <= row["snapshot_date"]:
        reasons.append("delisting_announced")
    if bool(row["monitoring_tag_known"]) and bool(row["monitoring_tag"]):
        reasons.append("monitoring_tag")
    return tuple(reasons)


def _month_end_dates(calendar: pd.Series) -> list[pd.Timestamp]:
    unique = pd.Series(pd.DatetimeIndex(calendar.drop_duplicates()).sort_values())
    candidates = unique.groupby(unique.dt.strftime("%Y-%m")).max()
    return [value for value in candidates if value.day == value.days_in_month]


def build_point_in_time_universe(
    daily_bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
    config: UniverseConfig = UniverseConfig(),
) -> PointInTimeUniverse:
    """Build buffered monthly membership without using present-day metadata."""

    bars = validate_daily_bars(daily_bars)
    master = validate_symbol_master(symbol_master)
    calendar = pd.Series(bars["date"].drop_duplicates().sort_values().to_numpy())
    month_ends = _month_end_dates(calendar)
    prior_members: tuple[str, ...] = ()
    snapshot_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []

    grouped_bars = {symbol: group.sort_values("date") for symbol, group in bars.groupby("symbol", sort=False)}
    for snapshot_date in month_ends:
        known_master = _master_asof(master, snapshot_date)
        candidates: list[dict[str, object]] = []
        for row in known_master.to_dict("records"):
            history = grouped_bars.get(str(row["symbol"]))
            history_asof = history.loc[history["date"] <= snapshot_date] if history is not None else bars.iloc[0:0]
            candidate = dict(row)
            candidate["snapshot_date"] = snapshot_date
            candidate["history_bars"] = len(history_asof)
            liquidity_start = snapshot_date - pd.Timedelta(days=config.liquidity_window - 1)
            liquidity_window = history_asof.loc[history_asof["date"].between(liquidity_start, snapshot_date)]
            candidate["liquidity_30"] = (
                float(liquidity_window["quote_volume"].median())
                if len(liquidity_window) == config.liquidity_window
                else np.nan
            )
            candidate["exclusion_reasons"] = _reason_list(pd.Series(candidate), config)
            candidates.append(candidate)

        candidate_frame = pd.DataFrame(candidates)
        if candidate_frame.empty:
            prior_members = ()
            continue
        eligible = candidate_frame.loc[candidate_frame["exclusion_reasons"].map(len).eq(0)].copy()
        if not eligible.empty:
            eligible = eligible.sort_values(["liquidity_30", "symbol"], ascending=[False, True], kind="stable")
            duplicate = eligible.duplicated("canonical_asset_id", keep="first")
            duplicate_symbols = set(eligible.loc[duplicate, "symbol"])
            eligible = eligible.loc[~duplicate].copy()
            candidate_frame.loc[candidate_frame["symbol"].isin(duplicate_symbols), "exclusion_reasons"] = candidate_frame.loc[
                candidate_frame["symbol"].isin(duplicate_symbols), "exclusion_reasons"
            ].map(lambda reasons: tuple(reasons) + ("duplicate_canonical_asset",))
            eligible["liquidity_rank"] = np.arange(1, len(eligible) + 1, dtype=np.int64)
        else:
            eligible["liquidity_rank"] = pd.Series(dtype="int64")

        incumbent = eligible.loc[
            eligible["symbol"].isin(prior_members) & (eligible["liquidity_rank"] <= config.retention_rank)
        ].sort_values("liquidity_rank")
        selected = list(incumbent["symbol"].head(config.maximum_members))
        entrants = eligible.loc[
            ~eligible["symbol"].isin(selected) & (eligible["liquidity_rank"] <= config.entry_rank)
        ].sort_values("liquidity_rank")
        selected.extend(list(entrants["symbol"].head(config.maximum_members - len(selected))))
        selected_set = set(selected)

        later_dates = calendar.loc[calendar > snapshot_date]
        effective_date = later_dates.iloc[0] if not later_dates.empty else pd.NaT
        selected_frame = eligible.loc[eligible["symbol"].isin(selected_set)].copy()
        selected_frame["snapshot_date"] = snapshot_date
        selected_frame["effective_date"] = effective_date
        selected_frame["was_incumbent"] = selected_frame["symbol"].isin(prior_members)
        snapshot_rows.extend(selected_frame.to_dict("records"))

        rank_by_symbol = dict(zip(eligible["symbol"], eligible["liquidity_rank"], strict=False))
        for row in candidate_frame.to_dict("records"):
            symbol = str(row["symbol"])
            reasons = tuple(row["exclusion_reasons"])
            rank = rank_by_symbol.get(symbol)
            if not reasons and symbol not in selected_set:
                reasons = ("outside_buffered_top_20",)
            audit_rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "effective_date": effective_date,
                    "symbol": symbol,
                    "master_as_of_date": row["as_of_date"],
                    "history_bars": row["history_bars"],
                    "liquidity_30": row["liquidity_30"],
                    "liquidity_rank": rank,
                    "was_incumbent": symbol in prior_members,
                    "selected": symbol in selected_set,
                    "exclusion_reasons": ";".join(reasons),
                }
            )
        prior_members = tuple(selected)

    snapshots = pd.DataFrame(snapshot_rows)
    audit = pd.DataFrame(audit_rows)
    daily_rows: list[dict[str, object]] = []
    if not snapshots.empty:
        effective_dates = sorted(date for date in snapshots["effective_date"].dropna().unique())
        for index, effective_date in enumerate(effective_dates):
            next_effective = effective_dates[index + 1] if index + 1 < len(effective_dates) else None
            active_dates = calendar.loc[calendar >= effective_date]
            if next_effective is not None:
                active_dates = active_dates.loc[active_dates < next_effective]
            members = snapshots.loc[snapshots["effective_date"] == effective_date]
            for day in active_dates:
                for row in members.to_dict("records"):
                    daily_rows.append(
                        {
                            "date": day,
                            "symbol": row["symbol"],
                            "base_asset": row["base_asset"],
                            "canonical_asset_id": row["canonical_asset_id"],
                            "snapshot_date": row["snapshot_date"],
                            "effective_date": effective_date,
                            "liquidity_rank": int(row["liquidity_rank"]),
                            "liquidity_30_at_snapshot": float(row["liquidity_30"]),
                            "trading_start_date": row["trading_start_date"],
                        }
                    )
    daily_membership = pd.DataFrame(daily_rows)
    if not daily_membership.empty:
        daily_membership = daily_membership.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    return PointInTimeUniverse(snapshots=snapshots.reset_index(drop=True), daily_membership=daily_membership, audit=audit)
