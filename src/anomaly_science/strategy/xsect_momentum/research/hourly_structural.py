"""Causal hourly structural exits for the cross-sectional quality book.

The daily score and rebalance schedule are unchanged. Positions are marked on
1h bars between scheduled daily-open rebalances. Physical exits may only use a
confirmed pre-entry swing price; fixed-percent and ATR-multiple stops are not
represented by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


Side = Literal["long", "short"]
ExitVariant = Literal["no_stop", "symmetric_structural", "short_structural"]


@dataclass(frozen=True, slots=True)
class StructuralAnchorSpec:
    left_hours: int = 6
    right_hours: int = 6
    lookback_hours: int = 168


@dataclass(frozen=True, slots=True)
class StructuralAnchor:
    price: float
    pivot_time: pd.Timestamp
    confirmed_time: pd.Timestamp


@dataclass(frozen=True, slots=True)
class HourBar:
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class HourlyBars:
    timestamp_ns: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def bar_at(self, timestamp: pd.Timestamp) -> HourBar | None:
        target = int(timestamp.value)
        i = int(np.searchsorted(self.timestamp_ns, target))
        if i >= len(self.timestamp_ns) or int(self.timestamp_ns[i]) != target:
            return None
        return HourBar(
            timestamp=timestamp,
            open=float(self.open[i]),
            high=float(self.high[i]),
            low=float(self.low[i]),
            close=float(self.close[i]),
        )


class HourlyBarStore:
    """Lazy compact numpy cache over one-parquet-per-symbol hourly data."""

    def __init__(self, root: Path, is_end: pd.Timestamp) -> None:
        self.root = root
        self.is_end_ns = int(is_end.value)
        self._cache: dict[str, HourlyBars] = {}

    def load(self, symbol: str) -> HourlyBars | None:
        if symbol in self._cache:
            return self._cache[symbol]
        path = self.root / f"{symbol}.parquet"
        if not path.exists():
            return None
        frame = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close"])
        timestamp_ns = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).astype("int64").to_numpy()
        keep = timestamp_ns < self.is_end_ns
        bars = HourlyBars(
            timestamp_ns=timestamp_ns[keep],
            open=frame["open"].to_numpy(dtype=float)[keep],
            high=frame["high"].to_numpy(dtype=float)[keep],
            low=frame["low"].to_numpy(dtype=float)[keep],
            close=frame["close"].to_numpy(dtype=float)[keep],
        )
        if len(bars.timestamp_ns) and np.any(np.diff(bars.timestamp_ns) <= 0):
            raise ValueError(f"hourly timestamps are not strictly increasing for {symbol}")
        self._cache[symbol] = bars
        return bars

    def bar_at(self, symbol: str, timestamp: pd.Timestamp) -> HourBar | None:
        bars = self.load(symbol)
        return bars.bar_at(timestamp) if bars is not None else None


def confirmed_structural_anchor(
    bars: HourlyBars,
    entry_time: pd.Timestamp,
    entry_price: float,
    side: Side,
    spec: StructuralAnchorSpec,
) -> StructuralAnchor | None:
    """Return the most recent admissible swing confirmed by entry_time."""
    end = int(np.searchsorted(bars.timestamp_ns, int(entry_time.value), side="left"))
    latest = end - spec.right_hours - 1
    earliest_time = int((entry_time - pd.Timedelta(hours=spec.lookback_hours)).value)
    earliest = max(
        spec.left_hours,
        int(np.searchsorted(bars.timestamp_ns, earliest_time, side="left")),
    )
    if latest < earliest:
        return None

    values = bars.high if side == "short" else bars.low
    for i in range(latest, earliest - 1, -1):
        window = values[i - spec.left_hours : i + spec.right_hours + 1]
        if len(window) != spec.left_hours + spec.right_hours + 1 or not np.isfinite(window).all():
            continue
        level = float(values[i])
        is_pivot = level >= float(window.max()) if side == "short" else level <= float(window.min())
        is_admissible = level > entry_price if side == "short" else level < entry_price
        if is_pivot and is_admissible:
            pivot_time = pd.Timestamp(int(bars.timestamp_ns[i]), tz="UTC")
            confirmed_i = i + spec.right_hours
            confirmed_time = pd.Timestamp(int(bars.timestamp_ns[confirmed_i]), tz="UTC") + pd.Timedelta(hours=1)
            if confirmed_time > entry_time:
                raise AssertionError("structural anchor confirmation exceeds entry time")
            return StructuralAnchor(level, pivot_time, confirmed_time)
    return None


def stop_fill_price(weight: float, stop_price: float | None, bar: HourBar) -> float | None:
    """Causal stop fill; an opening gap is never improved to the stop."""
    if stop_price is None:
        return None
    if weight > 0:
        if bar.open <= stop_price:
            return bar.open
        if bar.low <= stop_price:
            return stop_price
    elif weight < 0:
        if bar.open >= stop_price:
            return bar.open
        if bar.high >= stop_price:
            return stop_price
    return None


@dataclass
class HourlyRunResult:
    equity: pd.Series
    daily_ret: pd.Series
    trades: pd.DataFrame
    turnover: pd.Series
    meta: dict = field(default_factory=dict)


def _protected(weight: float, variant: ExitVariant) -> bool:
    if variant == "no_stop":
        return False
    if variant == "short_structural":
        return weight < 0
    return True


def run_hourly_backtest(
    panel: pd.DataFrame,
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    universe_mask: pd.DataFrame,
    scores: pd.DataFrame,
    selector: Callable,
    policy: XSectMomentumPolicy,
    store: HourlyBarStore,
    exit_variant: ExitVariant,
    anchor_spec: StructuralAnchorSpec = StructuralAnchorSpec(),
    seed: int = 0,
) -> HourlyRunResult:
    """Simulate daily-open decisions and hourly marking through the IS period."""
    if policy.stop_loss_pct != 0.0:
        raise ValueError("fixed-percent stops are incompatible with structural hourly execution")

    dates = close.index
    ret_c2c = close.pct_change(fill_method=None)
    vol = bt.trailing_vol(ret_c2c, policy.vol_lb)
    reg_state = bt.regime_state(close, policy)
    rng = np.random.default_rng(seed)
    warmup = max(
        policy.long_lb + policy.skip + 5,
        policy.liquidity_lb + 5,
        policy.vol_lb + 5,
    )
    reb_positions = list(
        range(warmup, len(dates) - policy.execution_delay_days, policy.rebalance_days)
    )
    rebalance_at = {ri + policy.execution_delay_days: ri for ri in reb_positions}

    equity = policy.start_equity
    positions: dict[str, float] = {}
    last_price: dict[str, float] = {}
    segments: dict[str, dict] = {}
    trades: list[dict] = []
    equity_path: dict[pd.Timestamp, float] = {}
    daily_ret: dict[pd.Timestamp, float] = {}
    turnover_rows: list[tuple] = []
    anchor_candidates = 0
    anchor_found = 0
    skipped_no_anchor = 0
    stopped_count = 0
    forced_exit_count = 0
    execution_unavailable_count = 0
    bankrupt = False

    def close_segment(
        symbol: str,
        exit_time: pd.Timestamp,
        exit_price: float,
        reason: str,
        exit_cost: float = 0.0,
    ) -> None:
        seg = segments.pop(symbol)
        asset_ret = float(exit_price / seg["entry_price"] - 1.0)
        pnl_gross = float(seg["entry_notional"] * asset_ret)
        pnl_cost = float(seg["entry_cost"] + seg["funding_cost"] + exit_cost)
        trades.append({
            "rebalance_date": seg["rebalance_date"],
            "entry_time": seg["entry_time"],
            "exit_time": exit_time,
            "symbol": symbol,
            "weight": seg["weight"],
            "entry_price": seg["entry_price"],
            "anchor_price": seg["anchor_price"],
            "anchor_time": seg["anchor_time"],
            "anchor_confirmed_time": seg["anchor_confirmed_time"],
            "exit_price": exit_price,
            "exit_reason": reason,
            "seg_ret": asset_ret,
            "equity_at_entry": seg["equity_at_entry"],
            "pnl_gross": pnl_gross,
            "pnl_cost": pnl_cost,
            "pnl_net": pnl_gross - pnl_cost,
            "ret_contrib": (pnl_gross - pnl_cost) / seg["equity_at_entry"],
        })

    def execution_cost(symbol: str, order_notional: float, signal_i: int) -> float:
        if order_notional <= 0:
            return 0.0
        adv = quote_volume[symbol].iloc[
            max(0, signal_i - policy.liquidity_lb) : signal_i
        ].median()
        adv_frac = order_notional / adv if np.isfinite(adv) and adv > 0 else 0.0
        order_frac = order_notional / equity if equity > 0 else 0.0
        return order_notional * bt._cost_bps(order_frac, adv_frac, policy) / 1e4

    first_entry_i = min(rebalance_at) if rebalance_at else len(dates)
    for di in range(first_entry_i, len(dates)):
        day = dates[di]
        equity_at_day_start = equity
        first_bars = {s: store.bar_at(s, day) for s in positions}

        # Mark positions from the preceding hourly close to this day's open.
        for s in list(positions):
            bar = first_bars[s]
            if bar is None:
                signal_i = rebalance_at.get(di, max(0, di - policy.execution_delay_days))
                exit_cost = execution_cost(s, abs(positions[s]), signal_i)
                equity -= exit_cost
                close_segment(s, day, last_price[s], "missing_bar_forced_exit", exit_cost)
                del positions[s]
                del last_price[s]
                forced_exit_count += 1
                continue
            old = positions[s]
            new = old * (bar.open / last_price[s])
            equity += new - old
            positions[s] = new
            last_price[s] = bar.open

        if equity <= 0:
            bankrupt = True
            equity = 0.0
            equity_path[day] = 0.0
            daily_ret[day] = -1.0
            break

        if di in rebalance_at:
            ri = rebalance_at[di]
            for s in list(segments):
                close_segment(s, day, last_price[s], "rebalance")

            signal_uni = [
                s for s in universe_mask.columns
                if bool(universe_mask.iat[ri, universe_mask.columns.get_loc(s)])
            ]
            executable = [s for s in signal_uni if store.bar_at(s, day) is not None]
            execution_unavailable_count += len(signal_uni) - len(executable)
            ctx = bt.SelectCtx(
                policy=policy,
                vol_row=vol.iloc[ri],
                rng=rng,
                regime=int(reg_state.iloc[ri]),
            )
            raw_target = selector(executable, scores.iloc[ri], set(positions), ctx) if executable else {}
            raw_target = {
                s: float(weight) for s, weight in raw_target.items()
                if abs(weight) > 1e-12
            }

            anchors: dict[str, StructuralAnchor | None] = {}
            kept_long: list[str] = []
            kept_short: list[str] = []
            for s, weight in raw_target.items():
                anchor = None
                if _protected(weight, exit_variant):
                    anchor_candidates += 1
                    bars = store.load(s)
                    entry_bar = store.bar_at(s, day)
                    if bars is None or entry_bar is None:
                        raise AssertionError("executable symbol has no hourly entry bar")
                    anchor = confirmed_structural_anchor(
                        bars,
                        day,
                        entry_bar.open,
                        "long" if weight > 0 else "short",
                        anchor_spec,
                    )
                    if anchor is None:
                        skipped_no_anchor += 1
                        continue
                    anchor_found += 1
                anchors[s] = anchor
                (kept_long if weight > 0 else kept_short).append(s)

            # Preserve the selector's exact net exposure when nothing is
            # skipped. This matters when long/short selections overlap and
            # partially cancel. If structural-anchor availability removes a
            # name, redistribute only that side's original net gross using the
            # same capped inverse-vol rule.
            target: dict[str, float] = {}
            raw_longs = [s for s, weight in raw_target.items() if weight > 0]
            raw_shorts = [s for s, weight in raw_target.items() if weight < 0]
            if len(kept_long) == len(raw_longs):
                target.update({s: raw_target[s] for s in kept_long})
            elif kept_long:
                long_gross = sum(raw_target[s] for s in raw_longs)
                target.update(bt._side_weights(kept_long, vol.iloc[ri], policy, +long_gross))
            if len(kept_short) == len(raw_shorts):
                target.update({s: raw_target[s] for s in kept_short})
            elif kept_short:
                short_gross = -sum(raw_target[s] for s in raw_shorts)
                target.update(bt._side_weights(kept_short, vol.iloc[ri], policy, -short_gross))

            equity_before_cost = equity
            desired = {s: weight * equity_before_cost for s, weight in target.items()}
            all_symbols = set(desired) | set(positions)
            deltas = {s: desired.get(s, 0.0) - positions.get(s, 0.0) for s in all_symbols}
            cost_dollars = sum(execution_cost(s, abs(delta), ri) for s, delta in deltas.items())
            gross_turn = sum(abs(delta) for delta in deltas.values()) / equity_before_cost
            equity -= cost_dollars
            if equity <= 0:
                bankrupt = True
                equity = 0.0
                equity_path[day] = 0.0
                daily_ret[day] = -1.0
                break

            positions = {s: weight * equity for s, weight in target.items()}
            last_price = {s: store.bar_at(s, day).open for s in positions}  # type: ignore[union-attr]
            turnover_rows.append((dates[ri], gross_turn, cost_dollars / equity_before_cost))
            gross_target = sum(abs(v) for v in positions.values())
            segments = {}
            for s, notional in positions.items():
                anchor = anchors[s]
                entry_cost = cost_dollars * abs(notional) / gross_target if gross_target > 0 else 0.0
                segments[s] = {
                    "rebalance_date": dates[ri],
                    "entry_time": day,
                    "entry_price": last_price[s],
                    "entry_notional": float(notional),
                    "weight": float(target[s]),
                    "equity_at_entry": float(equity),
                    "entry_cost": float(entry_cost),
                    "funding_cost": 0.0,
                    "anchor_price": anchor.price if anchor else np.nan,
                    "anchor_time": anchor.pivot_time if anchor else pd.NaT,
                    "anchor_confirmed_time": anchor.confirmed_time if anchor else pd.NaT,
                }

        # Mark each active position synchronously over the day's 24 hourly bars.
        for hour in range(24):
            timestamp = day + pd.Timedelta(hours=hour)
            total_pnl = 0.0
            stopped: list[tuple[str, float, float]] = []
            forced: list[tuple[str, float]] = []
            for s, old_notional in list(positions.items()):
                bar = store.bar_at(s, timestamp)
                if bar is None:
                    forced.append((s, last_price[s]))
                    continue
                stop = segments[s]["anchor_price"]
                stop_value = float(stop) if np.isfinite(stop) else None
                fill = stop_fill_price(old_notional, stop_value, bar)
                mark_price = fill if fill is not None else bar.close
                new_notional = old_notional * (mark_price / last_price[s])
                total_pnl += new_notional - old_notional
                positions[s] = new_notional
                last_price[s] = mark_price
                if fill is not None:
                    stopped.append((s, fill, abs(new_notional)))

            equity += total_pnl
            if equity <= 0:
                bankrupt = True
                equity = 0.0
                break

            signal_i = rebalance_at.get(di, max(0, di - policy.execution_delay_days))
            for s, fill, order_notional in stopped:
                exit_cost = execution_cost(s, order_notional, signal_i)
                equity -= exit_cost
                segments[s]["entry_cost"] += 0.0
                close_segment(s, timestamp, fill, "structural_stop", exit_cost)
                del positions[s]
                del last_price[s]
                stopped_count += 1
            for s, fill in forced:
                exit_cost = execution_cost(s, abs(positions[s]), signal_i)
                equity -= exit_cost
                close_segment(s, timestamp, fill, "missing_hour_forced_exit", exit_cost)
                del positions[s]
                del last_price[s]
                forced_exit_count += 1
            if equity <= 0:
                bankrupt = True
                equity = 0.0
                break

        if bankrupt:
            equity_path[day] = 0.0
            daily_ret[day] = -1.0
            break

        gross_notional = sum(abs(value) for value in positions.values())
        funding_cost = policy.funding_bps_per_day / 1e4 * gross_notional
        equity -= funding_cost
        if funding_cost and gross_notional:
            for s, seg in segments.items():
                seg["funding_cost"] += funding_cost * abs(positions[s]) / gross_notional
        if equity <= 0:
            bankrupt = True
            equity = 0.0
        equity_path[day] = equity
        daily_ret[day] = equity / equity_at_day_start - 1.0
        if bankrupt:
            break

    if segments and not bankrupt:
        final_time = dates[-1] + pd.Timedelta(hours=23)
        for s in list(segments):
            close_segment(s, final_time, last_price[s], "end_of_sample")

    eq = pd.Series(equity_path).sort_index()
    dr = pd.Series(daily_ret).sort_index()
    turnover_frame = pd.DataFrame(
        turnover_rows, columns=["date", "turnover", "cost_frac"]
    ).set_index("date") if turnover_rows else pd.DataFrame()
    coverage = anchor_found / anchor_candidates if anchor_candidates else np.nan
    return HourlyRunResult(
        equity=eq,
        daily_ret=dr,
        trades=pd.DataFrame(trades),
        turnover=(
            turnover_frame["turnover"] if len(turnover_frame) else pd.Series(dtype=float)
        ),
        meta={
            "start_equity": policy.start_equity,
            "final_equity": float(eq.iloc[-1]) if len(eq) else policy.start_equity,
            "bankrupt": bankrupt,
            "anchor_candidates": anchor_candidates,
            "anchor_found": anchor_found,
            "anchor_coverage": coverage,
            "skipped_no_anchor": skipped_no_anchor,
            "stopped_count": stopped_count,
            "forced_exit_count": forced_exit_count,
            "execution_unavailable_count": execution_unavailable_count,
            "exit_variant": exit_variant,
        },
    )
