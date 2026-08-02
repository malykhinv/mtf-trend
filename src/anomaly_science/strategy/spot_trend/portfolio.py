from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .contracts import PortfolioConfig, SpotTrendContractError, UniverseConfig
from .universe import validate_daily_bars, validate_symbol_master


PortfolioVariant = Literal["baseline", "overlay", "negative_control"]


def ewma_shrunk_covariance(
    returns: pd.DataFrame,
    *,
    decay: float = 0.94,
    diagonal_shrinkage: float = 0.5,
) -> np.ndarray:
    """Estimate a weighted daily covariance with fixed diagonal shrinkage."""

    values = returns.to_numpy(dtype=np.float64)
    if values.ndim != 2 or len(values) < 2 or not np.isfinite(values).all():
        raise SpotTrendContractError("EWMA covariance requires a complete finite return matrix")
    powers = np.arange(len(values) - 1, -1, -1, dtype=np.float64)
    weights = np.power(decay, powers)
    weights /= weights.sum()
    mean = np.sum(values * weights[:, None], axis=0)
    demeaned = values - mean
    covariance = demeaned.T @ (demeaned * weights[:, None])
    diagonal = np.diag(np.diag(covariance))
    return (1.0 - diagonal_shrinkage) * covariance + diagonal_shrinkage * diagonal


def _lifecycle_asof(symbol_master: pd.DataFrame, dates: pd.Series, symbols: pd.Series) -> pd.DataFrame:
    requests = pd.DataFrame({"date": dates, "symbol": symbols}).reset_index(names="_row")
    master = validate_symbol_master(symbol_master).rename(columns={"as_of_date": "master_as_of_date"})
    outputs: list[pd.DataFrame] = []
    for symbol, group in requests.groupby("symbol", sort=False):
        history = master.loc[master["symbol"].eq(symbol)].sort_values("master_as_of_date")
        if history.empty:
            missing = group.copy()
            for column in master.columns.difference({"symbol"}):
                missing[column] = np.nan
            outputs.append(missing)
            continue
        outputs.append(
            pd.merge_asof(
                group.sort_values("date"),
                history,
                left_on="date",
                right_on="master_as_of_date",
                by="symbol",
                direction="backward",
                allow_exact_matches=True,
            )
        )
    return pd.concat(outputs, ignore_index=True).sort_values("_row").set_index("_row")


def _base_rows(
    features: pd.DataFrame,
    symbol_master: pd.DataFrame,
    forecasts: pd.DataFrame | None,
    variant: PortfolioVariant,
    portfolio: PortfolioConfig,
    universe: UniverseConfig,
) -> pd.DataFrame:
    frame = features.copy().reset_index(drop=True)
    if frame.empty:
        return frame
    lifecycle = _lifecycle_asof(symbol_master, frame["date"], frame["symbol"])
    for column in (
        "master_as_of_date",
        "spot_trading_allowed",
        "status",
        "delisting_announcement_date",
        "delisting_date",
    ):
        frame[column] = lifecycle[column].to_numpy()
    for column in ("master_as_of_date", "delisting_announcement_date", "delisting_date"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.normalize()
    if frame["master_as_of_date"].isna().any():
        raise SpotTrendContractError("every target row requires point-in-time symbol master state")
    announced = frame["delisting_announcement_date"].notna() & frame["delisting_announcement_date"].le(frame["date"])
    frame["eligible_now"] = (
        frame["spot_trading_allowed"].astype(bool)
        & frame["status"].astype(str).str.upper().eq("TRADING")
        & ~announced
        & frame["median_quote_volume_30"].ge(universe.minimum_median_quote_volume)
    )
    valid_volatility = frame["vol_90"].notna() & np.isfinite(frame["vol_90"]) & frame["vol_90"].gt(0)
    frame["vol_multiplier"] = np.where(
        valid_volatility,
        np.minimum(portfolio.asset_volatility_target / frame["vol_90"], 1.0),
        0.0,
    )
    frame["base_raw_weight"] = (
        portfolio.base_slot_weight * frame["trend_signal"] * frame["vol_multiplier"] * frame["eligible_now"].astype(float)
    )
    if variant == "baseline":
        frame["ml_multiplier"] = 1.0
        frame["model_available"] = False
    else:
        if forecasts is None:
            raise SpotTrendContractError(f"{variant} requires an explicit forecast frame")
        overlay = forecasts[["date", "symbol", "ml_multiplier", "model_available"]]
        frame = frame.merge(overlay, on=["date", "symbol"], how="left", validate="one_to_one")
        frame["model_available"] = frame["model_available"].fillna(False).astype(bool)
        frame["ml_multiplier"] = frame["ml_multiplier"].where(frame["model_available"], 1.0).fillna(1.0)
        if frame["ml_multiplier"].lt(0.5).any() or frame["ml_multiplier"].gt(1.0).any():
            raise SpotTrendContractError("CatBoost multiplier must stay in [0.5, 1.0]")
    frame["raw_weight"] = frame["base_raw_weight"] * frame["ml_multiplier"]
    if (frame["raw_weight"] > frame["base_raw_weight"] + 1e-12).any():
        raise SpotTrendContractError("ML overlay increased a baseline target")
    return frame


def build_target_weights(
    features: pd.DataFrame,
    daily_bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
    *,
    variant: PortfolioVariant = "baseline",
    forecasts: pd.DataFrame | None = None,
    daily_membership: pd.DataFrame | None = None,
    portfolio: PortfolioConfig = PortfolioConfig(),
    universe: UniverseConfig = UniverseConfig(),
) -> pd.DataFrame:
    """Build close-time long-only targets with explicit covariance failures."""

    if variant not in {"baseline", "overlay", "negative_control"}:
        raise SpotTrendContractError(f"unknown portfolio variant: {variant}")
    bars = validate_daily_bars(daily_bars)
    master = validate_symbol_master(symbol_master)
    frame = _base_rows(features, master, forecasts, variant, portfolio, universe)
    if frame.empty:
        return frame
    returns = bars.pivot(index="date", columns="symbol", values="close").sort_index().apply(np.log).diff()
    output_rows: list[dict[str, object]] = []
    previous_members: set[str] = set()
    last_liquidity: dict[str, float] = {}
    last_lifecycle: dict[str, dict[str, object]] = {}
    last_targets: dict[str, float] = {}
    membership_by_day = (
        {
            day: set(group["symbol"])
            for day, group in daily_membership.groupby("date", sort=True)
        }
        if daily_membership is not None and not daily_membership.empty
        else {}
    )

    for day, cross_section in frame.groupby("date", sort=True):
        cross_section = cross_section.copy()
        feature_symbols = set(cross_section["symbol"])
        current_members = membership_by_day.get(day, feature_symbols)
        active = cross_section.loc[cross_section["raw_weight"].gt(0)].copy()
        covariance_status = "no_positive_raw_weights"
        portfolio_multiplier = 1.0
        portfolio_volatility = 0.0
        if not active.empty:
            symbols = list(active["symbol"])
            expected_dates = pd.date_range(
                day - pd.Timedelta(days=portfolio.covariance_window - 1),
                day,
                freq="D",
                tz="UTC",
            )
            common = returns.reindex(index=expected_dates, columns=symbols)
            if len(common) != portfolio.covariance_window or common.isna().any().any():
                covariance_status = "insufficient_complete_common_returns"
                portfolio_multiplier = 0.0
                portfolio_volatility = np.nan
            else:
                covariance = ewma_shrunk_covariance(
                    common,
                    decay=portfolio.ewma_lambda,
                    diagonal_shrinkage=portfolio.covariance_diagonal_shrinkage,
                )
                raw = active["raw_weight"].to_numpy(dtype=float)
                variance = float(raw @ covariance @ raw)
                portfolio_volatility = math_sqrt_nonnegative(variance) * np.sqrt(365.0)
                portfolio_multiplier = (
                    min(portfolio.portfolio_volatility_target / portfolio_volatility, 1.0)
                    if portfolio_volatility > 0
                    else 1.0
                )
                covariance_status = "estimated"
        cross_section["target_weight"] = (
            cross_section["raw_weight"] * portfolio_multiplier
        ).clip(lower=0.0, upper=portfolio.maximum_asset_weight)
        gross = float(cross_section["target_weight"].sum())
        if gross > portfolio.maximum_gross_exposure + 1e-12:
            cross_section["target_weight"] *= portfolio.maximum_gross_exposure / gross
        for row in cross_section.to_dict("records"):
            symbol = str(row["symbol"])
            last_liquidity[symbol] = float(row["median_quote_volume_30"])
            last_targets[symbol] = float(row["target_weight"])
            last_lifecycle[symbol] = {
                "delisting_announcement_date": row["delisting_announcement_date"],
                "delisting_date": row["delisting_date"],
            }
            row["variant"] = variant
            row["portfolio_multiplier"] = portfolio_multiplier
            row["portfolio_volatility"] = portfolio_volatility
            row["covariance_status"] = covariance_status
            row["membership_changed"] = symbol not in previous_members
            row["force_rebalance"] = bool(
                row["membership_changed"] or row["trend_state_changed"] or not row["eligible_now"]
            )
            output_rows.append(row)
        for symbol in sorted(current_members - feature_symbols):
            known = master.loc[master["symbol"].eq(symbol) & master["as_of_date"].le(day)]
            lifecycle = last_lifecycle.get(symbol, {}).copy()
            lifecycle_exit = False
            if not known.empty:
                current_lifecycle = known.sort_values("as_of_date", kind="stable").iloc[-1]
                lifecycle.update(
                    {
                        "delisting_announcement_date": current_lifecycle["delisting_announcement_date"],
                        "delisting_date": current_lifecycle["delisting_date"],
                    }
                )
                announcement = current_lifecycle["delisting_announcement_date"]
                lifecycle_exit = bool(
                    not current_lifecycle["spot_trading_allowed"]
                    or str(current_lifecycle["status"]).upper() != "TRADING"
                    or (pd.notna(announcement) and announcement <= day)
                )
            carried_target = 0.0 if lifecycle_exit else last_targets.get(symbol, 0.0)
            last_targets[symbol] = carried_target
            last_lifecycle[symbol] = lifecycle
            output_rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "variant": variant,
                    "target_weight": carried_target,
                    "base_raw_weight": np.nan,
                    "raw_weight": np.nan,
                    "ml_multiplier": np.nan,
                    "model_available": False,
                    "median_quote_volume_30": last_liquidity.get(symbol, np.nan),
                    "portfolio_multiplier": np.nan,
                    "portfolio_volatility": np.nan,
                    "covariance_status": (
                        "missing_current_bar_lifecycle_exit" if lifecycle_exit else "missing_current_bar_target_carried"
                    ),
                    "membership_changed": symbol not in previous_members,
                    "trend_state_changed": False,
                    "eligible_now": not lifecycle_exit,
                    "force_rebalance": lifecycle_exit,
                    "delisting_announcement_date": lifecycle.get("delisting_announcement_date", pd.NaT),
                    "delisting_date": lifecycle.get("delisting_date", pd.NaT),
                }
            )
        for symbol in sorted(previous_members - current_members):
            lifecycle = last_lifecycle.get(symbol, {})
            output_rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "variant": variant,
                    "target_weight": 0.0,
                    "base_raw_weight": 0.0,
                    "raw_weight": 0.0,
                    "ml_multiplier": 1.0,
                    "model_available": False,
                    "median_quote_volume_30": last_liquidity.get(symbol, np.nan),
                    "portfolio_multiplier": portfolio_multiplier,
                    "portfolio_volatility": portfolio_volatility,
                    "covariance_status": covariance_status,
                    "membership_changed": True,
                    "trend_state_changed": False,
                    "eligible_now": False,
                    "force_rebalance": True,
                    "delisting_announcement_date": lifecycle.get("delisting_announcement_date", pd.NaT),
                    "delisting_date": lifecycle.get("delisting_date", pd.NaT),
                }
            )
            last_targets[symbol] = 0.0
        previous_members = current_members
    output = pd.DataFrame(output_rows).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    gross_by_day = output.groupby("date")["target_weight"].sum()
    if (gross_by_day > portfolio.maximum_gross_exposure + 1e-12).any() or (output["target_weight"] < 0).any():
        raise SpotTrendContractError("target weights violate long-only gross exposure")
    return output


def math_sqrt_nonnegative(value: float) -> float:
    if value < -1e-15:
        raise SpotTrendContractError("covariance produced negative portfolio variance")
    return float(np.sqrt(max(value, 0.0)))
