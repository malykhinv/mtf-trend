"""Signal, selection and the $1000 daily-marked portfolio simulator (§2, §3, §4).

One shared portfolio engine; the strategy vs each control differs only in the
`selector` callable that maps (date, universe, scores) -> target weights. This
keeps the accounting identical across the strategy and its controls (§7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


# --------------------------------------------------------------------------- #
# Signal matrices
# --------------------------------------------------------------------------- #
def log_price(close: pd.DataFrame) -> pd.DataFrame:
    return np.log(close.where(close > 0))


def momentum_score(close: pd.DataFrame, p: XSectMomentumPolicy) -> pd.DataFrame:
    """Combined short/long log-return momentum with a skip gap (§2.3)."""
    lp = log_price(close)
    short = lp.shift(p.skip) - lp.shift(p.skip + p.short_lb)
    long = lp.shift(p.skip) - lp.shift(p.skip + p.long_lb)
    return p.alpha * short + (1.0 - p.alpha) * long


def ts_momentum_score(close: pd.DataFrame, p: XSectMomentumPolicy) -> pd.DataFrame:
    """Own trailing return (time-series momentum control, §7.5)."""
    lp = log_price(close)
    return lp.shift(p.skip) - lp.shift(p.skip + p.long_lb)


def trailing_vol(ret: pd.DataFrame, vol_lb: int) -> pd.DataFrame:
    return ret.rolling(vol_lb, min_periods=max(5, vol_lb // 2)).std()


def regime_ok(close: pd.DataFrame, p: XSectMomentumPolicy) -> pd.Series:
    """BTC trend gate per date: True = risk-on (§2.4)."""
    if p.regime_asset not in close.columns:
        return pd.Series(True, index=close.index)
    lp = np.log(close[p.regime_asset].where(close[p.regime_asset] > 0))
    trend = lp.shift(p.skip) - lp.shift(p.skip + p.regime_lb)
    return (trend > p.regime_threshold).reindex(close.index).fillna(False)


def regime_state(close: pd.DataFrame, p: XSectMomentumPolicy) -> pd.Series:
    """Tri-state market regime per date: +1 risk-on / 0 neutral / -1 risk-off.

    Uses the market trend (regime asset if present, else universe median close)
    with a symmetric neutral dead-band `regime_band` around the threshold.
    """
    if p.regime_asset in close.columns:
        base = close[p.regime_asset]
    else:
        base = close.median(axis=1)
    lp = np.log(base.where(base > 0))
    trend = (lp.shift(p.skip) - lp.shift(p.skip + p.regime_lb)).reindex(close.index)
    band = p.regime_band
    state = pd.Series(0, index=close.index, dtype=int)
    state[trend > p.regime_threshold + band] = 1
    state[trend < p.regime_threshold - band] = -1
    return state.fillna(0)


# --------------------------------------------------------------------------- #
# Weighting
# --------------------------------------------------------------------------- #
def _weights(symbols: list[str], vol_row: pd.Series, p: XSectMomentumPolicy) -> dict[str, float]:
    if not symbols:
        return {}
    if p.weighting == "equal":
        w = {s: 1.0 for s in symbols}
    else:
        raw = {}
        for s in symbols:
            v = vol_row.get(s, np.nan)
            if not np.isfinite(v) or v <= 0:
                v = np.nan
            raw[s] = 1.0 / v if np.isfinite(v) else np.nan
        # symbols with no vol estimate fall back to the median inverse-vol
        finite = [x for x in raw.values() if np.isfinite(x)]
        fill = np.median(finite) if finite else 1.0
        w = {s: (raw[s] if np.isfinite(raw[s]) else fill) for s in symbols}
    total = sum(w.values())
    w = {s: x / total for s, x in w.items()}
    if p.weighting == "capped_inverse_vol":
        for _ in range(8):  # iterative cap + renormalize
            over = {s: x for s, x in w.items() if x > p.max_weight}
            if not over:
                break
            for s in over:
                w[s] = p.max_weight
            room = 1.0 - p.max_weight * len(over)
            rest = {s: x for s, x in w.items() if s not in over}
            rest_tot = sum(rest.values())
            if rest_tot <= 0:
                break
            for s in rest:
                w[s] = rest[s] / rest_tot * room
    scale = p.gross_exposure / max(sum(w.values()), 1e-12)
    return {s: x * scale for s, x in w.items()}


# --------------------------------------------------------------------------- #
# Selectors  (date, universe, score_row, prev, ctx) -> {symbol: weight}
# --------------------------------------------------------------------------- #
@dataclass
class SelectCtx:
    policy: XSectMomentumPolicy
    vol_row: pd.Series
    rng: np.random.Generator
    regime: int = 1  # tri-state market regime at this rebalance (+1/0/-1)


def _side_weights(symbols: list[str], vol_row: pd.Series, p: XSectMomentumPolicy, gross_signed: float) -> dict[str, float]:
    """Capped inverse-vol weights on one side, summing to gross_signed (may be <0)."""
    if not symbols or gross_signed == 0:
        return {}
    base = _weights(symbols, vol_row, XSectMomentumPolicy(
        weighting=p.weighting, vol_lb=p.vol_lb, max_weight=p.max_weight, gross_exposure=1.0))
    return {s: w * gross_signed for s, w in base.items()}


def _score_order(uni: list[str], scores: pd.Series) -> pd.Series:
    return scores.reindex(uni).dropna().sort_values(ascending=False)


def sel_momentum(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    s = scores.reindex(uni).dropna()
    if p.require_positive:
        s = s[s > 0]
    if s.empty:
        return {}
    order = s.sort_values(ascending=False)
    ranks = {sym: i + 1 for i, sym in enumerate(order.index)}
    # hysteresis: keep previous holds still within exit_rank, then fill from top
    held = [sym for sym in order.index if sym in prev and ranks[sym] <= p.exit_rank]
    picks = list(held)
    for sym in order.index:
        if len(picks) >= p.top_k:
            break
        if sym not in picks and ranks[sym] <= max(p.enter_rank, p.top_k):
            picks.append(sym)
    picks = picks[: p.top_k]
    return _weights(picks, ctx.vol_row, p)


def sel_ts_momentum(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    # scores here are TS momentum; long only positive-trend names, top_k by strength
    return sel_momentum(uni, scores, prev, ctx)


def sel_blind_equal(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    return _weights(list(uni), ctx.vol_row, XSectMomentumPolicy(weighting="equal"))


def sel_random_k(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    if not uni:
        return {}
    k = min(p.top_k, len(uni))
    picks = list(ctx.rng.choice(uni, size=k, replace=False))
    return _weights(picks, ctx.vol_row, p)


# --- score-driven directional selectors (quality / anti-pump score) ---------- #
def _long_picks(order: pd.Series, p: XSectMomentumPolicy) -> list[str]:
    n = len(order)
    if n == 0:
        return []
    hi = order[order.rank(pct=True) >= p.score_gate_long] if p.score_gate_long > 0 else order
    return list(hi.index[: p.top_k])


def _short_picks(order: pd.Series, p: XSectMomentumPolicy) -> list[str]:
    n = len(order)
    if n == 0:
        return []
    lo = order[order.rank(pct=True) <= p.score_gate_short] if p.score_gate_short < 1 else order
    return list(lo.index[-p.n_short:])


def sel_quality_long(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    order = _score_order(uni, scores)
    return _side_weights(_long_picks(order, p), ctx.vol_row, p, +p.gross_exposure)


def sel_market_neutral(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    order = _score_order(uni, scores)
    longs = _side_weights(_long_picks(order, p), ctx.vol_row, p, +0.5 * p.gross_exposure)
    shorts = _side_weights(_short_picks(order, p), ctx.vol_row, p, -0.5 * p.gross_exposure)
    out = dict(longs)
    for s, w in shorts.items():
        out[s] = out.get(s, 0.0) + w
    return out


def sel_regime_adaptive(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    order = _score_order(uni, scores)
    if ctx.regime > 0:
        return _side_weights(_long_picks(order, p), ctx.vol_row, p, +p.gross_exposure)
    if ctx.regime < 0:
        return _side_weights(_short_picks(order, p), ctx.vol_row, p, -p.gross_exposure)
    return {}  # neutral regime -> skip (cash)


def sel_short_only(uni: list[str], scores: pd.Series, prev: set[str], ctx: SelectCtx) -> dict[str, float]:
    p = ctx.policy
    order = _score_order(uni, scores)
    return _side_weights(_short_picks(order, p), ctx.vol_row, p, -p.gross_exposure)


# --------------------------------------------------------------------------- #
# Portfolio simulation
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    equity: pd.Series
    daily_ret: pd.Series
    trades: pd.DataFrame
    turnover: pd.Series
    meta: dict = field(default_factory=dict)


def _cost_bps(order_frac: float, adv_frac: float, p: XSectMomentumPolicy) -> float:
    impact = p.impact_coef_bps * np.sqrt(max(adv_frac, 0.0))
    return (p.fee_bps + p.half_spread_bps + p.base_slippage_bps + impact) * p.cost_multiplier


def run_backtest(
    panel: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    quote_vol: pd.DataFrame,
    universe_mask: pd.DataFrame,
    scores: pd.DataFrame,
    selector: Callable,
    p: XSectMomentumPolicy,
    use_regime: bool = True,
    bidirectional: bool = False,
    seed: int = 0,
) -> RunResult:
    """Simulate the $1000 daily-marked portfolio.

    bidirectional=False: classic long-only path — cash forced when risk-off.
    bidirectional=True:  the selector fully owns direction; the tri-state regime
    is passed in `ctx.regime` and the selector may go long, short, or flat.
    """
    if p.stop_loss_pct != 0.0:
        raise ValueError(
            "fixed-percent stops are not supported: daily bars cannot provide "
            "causal stop fills, and project policy requires structural anchors"
        )

    dates = close.index
    ret_c2c = close.pct_change(fill_method=None)
    vol = trailing_vol(ret_c2c, p.vol_lb)
    regime = regime_ok(close, p) if use_regime else pd.Series(True, index=dates)
    reg_state = regime_state(close, p)
    rng = np.random.default_rng(seed)

    warmup = max(p.long_lb + p.skip + 5, p.liquidity_lb + 5, p.vol_lb + 5)
    reb_positions = list(range(warmup, len(dates) - p.execution_delay_days, p.rebalance_days))
    rebalance_at = {ri + p.execution_delay_days: ri for ri in reb_positions}

    equity = p.start_equity
    equity_path: dict[pd.Timestamp, float] = {}
    daily_ret: dict[pd.Timestamp, float] = {}
    turnover_rows: list[tuple] = []
    trades: list[dict] = []
    # Signed dollar notionals marked at the most recent open/close. Between
    # rebalances they drift with price; there is no hidden daily rebalancing.
    positions: dict[str, float] = {}
    segments: dict[str, dict] = {}
    forced_exit_count = 0
    execution_unavailable_count = 0

    def mark(s: str, price_return: float, date: pd.Timestamp, leg: str) -> None:
        nonlocal equity
        if not np.isfinite(price_return):
            raise ValueError(f"missing {leg} return for active {s} at {date}")
        old = positions[s]
        pnl = old * price_return
        positions[s] = old * (1.0 + price_return)
        equity += pnl

    def close_segment(s: str, exit_date: pd.Timestamp, px: float, exit_reason: str) -> None:
            seg = segments[s]
            if not np.isfinite(px) or px <= 0:
                raise ValueError(f"missing exit price for active {s} at {exit_date}")
            asset_ret = float(px / seg["entry_price"] - 1.0)
            pnl_gross = float(seg["entry_notional"] * asset_ret)
            pnl_cost = float(seg["entry_cost"] + seg["funding_cost"])
            trades.append({
                "rebalance_date": seg["rebalance_date"], "entry_date": seg["entry_date"],
                "exit_date": exit_date, "symbol": s, "weight": seg["weight"],
                "seg_ret": asset_ret, "equity_at_entry": seg["equity_at_entry"],
                "pnl_gross": pnl_gross, "pnl_cost": pnl_cost,
                "pnl_net": pnl_gross - pnl_cost,
                "ret_contrib": (pnl_gross - pnl_cost) / seg["equity_at_entry"],
                "exit_reason": exit_reason,
            })
            del segments[s]

    def close_segments(exit_date: pd.Timestamp, exit_prices: pd.Series, exit_reason: str) -> None:
        for s in list(segments):
            px = exit_prices.get(s, np.nan)
            close_segment(s, exit_date, px, exit_reason)

    first_entry_i = min(rebalance_at) if rebalance_at else len(dates)
    bankrupt = False
    for di in range(first_entry_i, len(dates)):
        d = dates[di]
        equity_at_day_start = equity

        # Existing positions earn the close(t-1)->open(t) leg exactly once.
        if positions and di > 0:
            for s in list(positions):
                prev_close = close[s].iloc[di - 1]
                day_open = open_[s].iloc[di]
                if np.isfinite(prev_close) and prev_close > 0 and not (np.isfinite(day_open) and day_open > 0):
                    # A contract that has no next bar cannot remain in the book.
                    # Exit explicitly at the last observable close and charge an
                    # ordinary taker-side exit cost. This is recorded in the
                    # trade ledger; isolated missing active bars never become a
                    # silent zero-return hold.
                    order_notional = abs(positions[s])
                    adv = quote_vol[s].iloc[max(0, di - p.liquidity_lb):di].median()
                    adv_frac = order_notional / adv if np.isfinite(adv) and adv > 0 else 0.0
                    exit_cost = order_notional * _cost_bps(
                        order_notional / equity if equity > 0 else 0.0, adv_frac, p
                    ) / 1e4
                    equity -= exit_cost
                    segments[s]["entry_cost"] += exit_cost
                    close_segment(s, d, float(prev_close), "missing_bar_forced_exit")
                    del positions[s]
                    forced_exit_count += 1
                    continue
                overnight = day_open / prev_close - 1.0 if prev_close > 0 and day_open > 0 else np.nan
                mark(s, overnight, d, "overnight")

            if equity <= 0:
                if segments:
                    close_segments(d, open_.iloc[di], "bankruptcy_at_open")
                positions.clear()
                equity = 0.0
                equity_path[d] = equity
                daily_ret[d] = -1.0
                bankrupt = True
                break

        if di in rebalance_at:
            ri = rebalance_at[di]
            r_date = dates[ri]
            # The previous segment exits at this open before target weights change.
            if segments:
                close_segments(d, open_.iloc[di], "rebalance")

            signal_uni = [
                s for s in universe_mask.columns
                if bool(universe_mask.iat[ri, universe_mask.columns.get_loc(s)])
            ]
            # Availability at the delayed execution open is known before orders
            # are submitted. Rank only executable members of the signal-date
            # universe; this handles delistings/renames without future data.
            uni = [
                s for s in signal_uni
                if np.isfinite(open_[s].iloc[di]) and open_[s].iloc[di] > 0
            ]
            execution_unavailable_count += len(signal_uni) - len(uni)
            held = {s for s, notional in positions.items() if abs(notional) > 1e-12}
            if bidirectional:
                ctx = SelectCtx(policy=p, vol_row=vol.iloc[ri], rng=rng, regime=int(reg_state.iloc[ri]))
                target = selector(uni, scores.iloc[ri], held, ctx) if uni else {}
            else:
                risk_on = bool(regime.iloc[ri])
                if risk_on and uni:
                    ctx = SelectCtx(policy=p, vol_row=vol.iloc[ri], rng=rng, regime=int(reg_state.iloc[ri]))
                    target = selector(uni, scores.iloc[ri], held, ctx)
                else:
                    target = {}

            equity_before_cost = equity
            desired = {s: w * equity_before_cost for s, w in target.items()}
            all_syms = set(desired) | set(positions)
            deltas = {s: desired.get(s, 0.0) - positions.get(s, 0.0) for s in all_syms}
            gross_turn_dollars = sum(abs(v) for v in deltas.values())
            cost_dollars = 0.0
            for s, delta in deltas.items():
                order_notional = abs(delta)
                if order_notional <= 0:
                    continue
                adv = quote_vol[s].iloc[max(0, ri - p.liquidity_lb):ri].median() if s in quote_vol else np.nan
                adv_frac = order_notional / adv if np.isfinite(adv) and adv > 0 else 0.0
                order_frac = order_notional / equity_before_cost if equity_before_cost > 0 else 0.0
                cost_dollars += order_notional * _cost_bps(order_frac, adv_frac, p) / 1e4
            equity -= cost_dollars
            if equity <= 0:
                positions.clear()
                segments.clear()
                equity = 0.0
                equity_path[d] = equity
                daily_ret[d] = -1.0
                bankrupt = True
                break

            # Express the requested weights on post-cost equity. The tiny change
            # versus the cost-estimation notionals is deliberately not charged a
            # second time; it is accounting normalization, not another fill.
            positions = {s: w * equity for s, w in target.items()}
            gross_turn = gross_turn_dollars / equity_before_cost if equity_before_cost > 0 else 0.0
            turnover_rows.append((r_date, gross_turn, cost_dollars / equity_before_cost))

            gross_target = sum(abs(v) for v in positions.values())
            segments = {}
            for s, notional in positions.items():
                px = open_[s].iloc[di]
                if not np.isfinite(px) or px <= 0:
                    raise ValueError(f"missing entry price for selected {s} at {d}")
                cost_share = cost_dollars * abs(notional) / gross_target if gross_target > 0 else 0.0
                segments[s] = {
                    "rebalance_date": r_date, "entry_date": d, "entry_price": float(px),
                    "entry_notional": float(notional), "weight": float(target[s]),
                    "equity_at_entry": float(equity), "entry_cost": float(cost_share),
                    "funding_cost": 0.0,
                }

        # Current positions earn open(t)->close(t), then pay that day's funding.
        for s in list(positions):
            day_open = open_[s].iloc[di]
            day_close = close[s].iloc[di]
            intraday = day_close / day_open - 1.0 if day_open > 0 and day_close > 0 else np.nan
            mark(s, intraday, d, "intraday")

        if equity <= 0:
            if segments:
                close_segments(d, close.iloc[di], "bankruptcy_at_close")
            positions.clear()
            equity = 0.0
            equity_path[d] = equity
            daily_ret[d] = -1.0
            bankrupt = True
            break

        gross_notional = sum(abs(v) for v in positions.values())
        funding_cost = p.funding_bps_per_day / 1e4 * gross_notional
        equity -= funding_cost
        if funding_cost > 0 and segments and gross_notional > 0:
            for s, seg in segments.items():
                seg["funding_cost"] += funding_cost * abs(positions[s]) / gross_notional

        if equity <= 0:
            if segments:
                close_segments(d, close.iloc[di], "bankruptcy_after_funding")
            positions.clear()
            equity = 0.0
            equity_path[d] = equity
            daily_ret[d] = -1.0
            bankrupt = True
            break

        equity_path[d] = equity
        daily_ret[d] = equity / equity_at_day_start - 1.0

    if segments:
        close_segments(dates[-1], close.iloc[-1], "end_of_sample")

    eq = pd.Series(equity_path).sort_index()
    dr = pd.Series(daily_ret).sort_index()
    tv = pd.DataFrame(turnover_rows, columns=["date", "turnover", "cost_frac"]).set_index("date") if turnover_rows else pd.DataFrame()
    td = pd.DataFrame(trades)
    return RunResult(
        equity=eq,
        daily_ret=dr,
        trades=td,
        turnover=tv["turnover"] if len(tv) else pd.Series(dtype=float),
        meta={
            "n_rebalances": len(reb_positions),
            "start_equity": p.start_equity,
            "final_equity": float(eq.iloc[-1]) if len(eq) else p.start_equity,
            "forced_exit_count": forced_exit_count,
            "execution_unavailable_count": execution_unavailable_count,
            "bankrupt": bankrupt,
        },
    )
