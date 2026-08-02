from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import PortfolioConfig, SimulationConfig, SpotTrendContractError
from .universe import validate_daily_bars, validate_symbol_master


@dataclass(frozen=True, slots=True)
class SimulationResult:
    daily: pd.DataFrame
    fills: pd.DataFrame
    episodes: pd.DataFrame


def _known_master_state(master: pd.DataFrame, symbol: str, day: pd.Timestamp) -> pd.Series | None:
    known = master.loc[master["symbol"].eq(symbol) & master["as_of_date"].le(day)]
    if known.empty:
        return None
    return known.sort_values("as_of_date", kind="stable").iloc[-1]


def simulate_spot_portfolio(
    daily_bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    simulation: SimulationConfig = SimulationConfig(),
    portfolio: PortfolioConfig = PortfolioConfig(),
    evaluation_start: pd.Timestamp | str | None = None,
    evaluation_end: pd.Timestamp | str | None = None,
) -> SimulationResult:
    """Execute close-time targets at later opens with cash and capacity limits."""

    bars = validate_daily_bars(daily_bars)
    master = validate_symbol_master(symbol_master)
    required_targets = {"date", "symbol", "target_weight", "force_rebalance", "median_quote_volume_30"}
    missing = sorted(required_targets.difference(targets.columns))
    if missing:
        raise SpotTrendContractError(f"target schedule missing columns: {missing}")
    schedule = targets.copy()
    schedule["date"] = pd.to_datetime(schedule["date"], utc=True).dt.normalize()
    if schedule[["date", "symbol"]].duplicated().any():
        raise SpotTrendContractError("target schedule must be unique by (date, symbol)")
    if (schedule["target_weight"] < 0).any() or (schedule.groupby("date")["target_weight"].sum() > 1.0 + 1e-12).any():
        raise SpotTrendContractError("simulation accepts long-only unlevered targets only")

    start = pd.Timestamp(evaluation_start) if evaluation_start is not None else bars["date"].min()
    end = pd.Timestamp(evaluation_end) if evaluation_end is not None else bars["date"].max()
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    start, end = start.normalize(), end.normalize()
    calendar = list(pd.DatetimeIndex(bars.loc[bars["date"].between(start, end), "date"].drop_duplicates()).sort_values())
    if not calendar:
        raise SpotTrendContractError("evaluation interval contains no market dates")
    bars_by_day = {day: group.set_index("symbol") for day, group in bars.groupby("date", sort=True)}
    targets_by_day = {day: group.set_index("symbol") for day, group in schedule.groupby("date", sort=True)}
    cost_rate = simulation.one_way_cost_bps / 10_000.0
    cash = float(simulation.initial_capital)
    positions: dict[str, float] = {}
    last_price: dict[str, float] = {}
    desired: dict[str, dict[str, object]] = {}
    episodes_open: dict[str, dict[str, object]] = {}
    daily_rows: list[dict[str, object]] = []
    fill_rows: list[dict[str, object]] = []
    episode_rows: list[dict[str, object]] = []
    previous_nav = float(simulation.initial_capital)

    # Every evaluation starts from cash.  The first eligible instruction is
    # formed at the first evaluation close and can execute only on a later open.

    def close_episode(symbol: str, day: pd.Timestamp, reason: str) -> None:
        episode = episodes_open.pop(symbol, None)
        if episode is None:
            return
        pnl = float(episode["inflow"]) - float(episode["outflow"])
        episode_rows.append(
            {
                "symbol": symbol,
                "entry_date": episode["entry_date"],
                "exit_date": day,
                "duration_days": max((day - episode["entry_date"]).days, 1),
                "net_pnl": pnl,
                "exit_reason": reason,
            }
        )

    for day in calendar:
        day_bars = bars_by_day[day]
        daily_cost = 0.0
        daily_delisting_loss = 0.0
        unpriceable_held = False

        # Emergency lifecycle exit uses only state known on this date and the
        # last real execution price.  It applies when no prior announcement
        # produced an orderly next-open exit.
        for symbol in list(positions):
            if positions.get(symbol, 0.0) <= 1e-15:
                continue
            state = _known_master_state(master, symbol, day)
            if state is None:
                continue
            actual = state["delisting_date"]
            announcement = state["delisting_announcement_date"]
            unannounced = pd.notna(actual) and actual <= day and (pd.isna(announcement) or announcement >= actual)
            if unannounced and symbol not in day_bars.index and symbol in last_price:
                units = positions.pop(symbol)
                notional = units * last_price[symbol]
                transaction_cost = notional * cost_rate
                penalty = notional * simulation.delisting_penalty
                cash += notional - transaction_cost - penalty
                daily_cost += transaction_cost
                daily_delisting_loss += penalty
                if symbol in episodes_open:
                    episodes_open[symbol]["inflow"] = float(episodes_open[symbol]["inflow"]) + notional - transaction_cost - penalty
                fill_rows.append(
                    {
                        "date": day,
                        "signal_date": pd.NaT,
                        "symbol": symbol,
                        "side": "sell",
                        "price": last_price[symbol],
                        "notional": notional,
                        "transaction_cost": transaction_cost,
                        "participation": np.nan,
                        "partial_fill": False,
                        "delisting_penalty": penalty,
                        "reason": "unannounced_delisting_emergency",
                    }
                )
                close_episode(symbol, day, "unannounced_delisting_emergency")
                desired[symbol] = {"weight": 0.0, "force": True, "liquidity": np.nan, "signal_date": day}

        open_values: dict[str, float] = {}
        for symbol, units in positions.items():
            if units <= 0:
                continue
            if symbol in day_bars.index:
                price = float(day_bars.at[symbol, "open"])
            elif symbol in last_price:
                price = last_price[symbol]
                unpriceable_held = True
            else:
                raise SpotTrendContractError(f"held symbol has never had a real price: {symbol}")
            open_values[symbol] = units * price
        nav_open = cash + sum(open_values.values())
        if nav_open <= 0:
            raise SpotTrendContractError("portfolio NAV became non-positive")

        for symbol in sorted(set(desired) | set(positions)):
            instruction = desired.get(symbol, {"weight": 0.0, "force": True, "liquidity": np.nan, "signal_date": pd.NaT})
            target_weight = float(instruction["weight"])
            current_units = positions.get(symbol, 0.0)
            if symbol not in day_bars.index:
                if current_units > 0:
                    unpriceable_held = True
                continue
            open_price = float(day_bars.at[symbol, "open"])
            current_notional = current_units * open_price
            desired_notional = target_weight * nav_open
            delta = desired_notional - current_notional
            current_weight = current_notional / nav_open
            difference = abs(delta) / nav_open
            rebalance_threshold = max(
                portfolio.absolute_rebalance_band,
                portfolio.relative_rebalance_band * max(target_weight, current_weight),
            )
            if not bool(instruction["force"]) and difference < rebalance_threshold:
                continue
            if abs(delta) <= max(nav_open * 1e-12, 1e-9):
                continue
            liquidity = float(instruction["liquidity"])
            if not np.isfinite(liquidity) or liquidity <= 0:
                continue
            capacity = simulation.participation_cap * liquidity
            fill_notional = min(abs(delta), capacity)
            side = "buy" if delta > 0 else "sell"
            if side == "buy":
                fill_notional = min(fill_notional, cash / (1.0 + cost_rate))
            else:
                fill_notional = min(fill_notional, current_notional)
            if fill_notional <= max(nav_open * 1e-12, 1e-9):
                continue
            transaction_cost = fill_notional * cost_rate
            units = fill_notional / open_price
            prior_units = positions.get(symbol, 0.0)
            if side == "buy":
                cash -= fill_notional + transaction_cost
                positions[symbol] = prior_units + units
                if prior_units <= 1e-15:
                    episodes_open[symbol] = {"entry_date": day, "outflow": 0.0, "inflow": 0.0}
                episodes_open[symbol]["outflow"] = float(episodes_open[symbol]["outflow"]) + fill_notional + transaction_cost
            else:
                cash += fill_notional - transaction_cost
                remaining = max(prior_units - units, 0.0)
                positions[symbol] = remaining
                if symbol in episodes_open:
                    episodes_open[symbol]["inflow"] = float(episodes_open[symbol]["inflow"]) + fill_notional - transaction_cost
                if remaining * open_price <= max(nav_open * 1e-10, 1e-8):
                    positions.pop(symbol, None)
                    close_episode(symbol, day, "target_exit")
            daily_cost += transaction_cost
            remaining_notional = max(abs(delta) - fill_notional, 0.0)
            partial = remaining_notional > max(nav_open * 1e-10, 1e-8)
            fill_rows.append(
                {
                    "date": day,
                    "signal_date": instruction["signal_date"],
                    "symbol": symbol,
                    "side": side,
                    "price": open_price,
                    "notional": fill_notional,
                    "transaction_cost": transaction_cost,
                    "participation": fill_notional / liquidity,
                    "partial_fill": partial,
                    "delisting_penalty": 0.0,
                    "reason": "target_rebalance",
                }
            )

        close_values: dict[str, float] = {}
        for symbol, units in positions.items():
            if symbol in day_bars.index:
                price = float(day_bars.at[symbol, "close"])
                last_price[symbol] = price
            elif symbol in last_price:
                price = last_price[symbol]
                unpriceable_held = True
            else:
                raise SpotTrendContractError(f"held symbol is unpriceable at close: {symbol}")
            close_values[symbol] = units * price
        for symbol in day_bars.index:
            last_price[symbol] = float(day_bars.at[symbol, "close"])
        nav_close = cash + sum(close_values.values())
        gross_exposure = sum(close_values.values()) / nav_close if nav_close > 0 else np.nan
        net_return = nav_close / previous_nav - 1.0 if previous_nav > 0 else np.nan
        if unpriceable_held:
            net_return = np.nan
        daily_rows.append(
            {
                "date": day,
                "nav_before_return": previous_nav,
                "nav": nav_close,
                "net_return": net_return,
                "gross_exposure": gross_exposure,
                "cash_weight": cash / nav_close,
                "transaction_cost": daily_cost,
                "delisting_loss": daily_delisting_loss,
                "unpriceable_held_asset": unpriceable_held,
            }
        )
        previous_nav = nav_close

        # Close t produces instructions for opens strictly after t.
        if day in targets_by_day:
            for symbol, row in targets_by_day[day].iterrows():
                desired[symbol] = {
                    "weight": float(row["target_weight"]),
                    "force": bool(row["force_rebalance"]),
                    "liquidity": float(row["median_quote_volume_30"]),
                    "signal_date": day,
                }

    return SimulationResult(
        daily=pd.DataFrame(daily_rows),
        fills=pd.DataFrame(fill_rows),
        episodes=pd.DataFrame(episode_rows),
    )
