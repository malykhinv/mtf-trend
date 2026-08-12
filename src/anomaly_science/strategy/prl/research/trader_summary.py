"""Signal-agnostic trader summary + portfolio/risk profile for a PRL score (§32.3,
§45-§49, §64-§69). Answers the operational questions a desk asks before OOS:

trades, win rate, per-day-of-week / week / month positivity, PnL concentration by
symbol / regime / month, how few top trades flip the total to a loss, and a
compounded portfolio at several per-name risk budgets — plus entry/exit variants.

Takes a per-date score matrix (date x symbol) + the daily panel, so it runs on the
composite quality signal today and on the CatBoost OOF score later, unchanged.
Everything is causal: entry is at the next open after the signal date.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRIMARY, PRLCoarsePolicy
from anomaly_science.strategy.prl.research.regime import regime_label
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")
FUNDING_BPS_PER_DAY = 1.5


def simulate_book(score: pd.DataFrame, close: pd.DataFrame, open_: pd.DataFrame,
                  umask: pd.DataFrame, reg: pd.Series, p: PRLCoarsePolicy,
                  top_q: float = 0.1, bottom_q: float = 0.0,
                  delay: int = 1, hold: int | None = None) -> pd.DataFrame:
    """One name-trade per selected symbol per rebalance. Long top_q (and short
    bottom_q if >0). Open-to-open fills, delay days after the signal date."""
    hold = hold or p.fwd_horizon
    idx = score.index
    warmup = _warmup(p)
    side_bps = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier
    trades = []
    for ri in range(warmup, len(idx) - delay - hold - 1, hold):
        d = idx[ri]
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        order = s.sort_values(ascending=False)
        k = max(1, int(len(order) * top_q))
        picks = [(sym, +1) for sym in order.index[:k]]
        if bottom_q > 0:
            kb = max(1, int(len(order) * bottom_q))
            picks += [(sym, -1) for sym in order.index[-kb:]]
        ei, xi = ri + delay, ri + delay + hold
        for sym, sd in picks:
            o_in, o_out = open_[sym].iloc[ei], open_[sym].iloc[xi]
            if not (np.isfinite(o_in) and o_in > 0 and np.isfinite(o_out) and o_out > 0):
                continue
            gross = sd * (o_out / o_in - 1.0)
            cost = 2 * side_bps / 1e4 + FUNDING_BPS_PER_DAY * hold / 1e4
            trades.append({"rebalance_date": d, "entry_date": idx[ei], "exit_date": idx[xi],
                           "symbol": sym, "side": sd, "gross": gross, "net": gross - cost,
                           "regime": reg.get(d, "unknown")})
    return pd.DataFrame(trades)


def _portfolio_returns(trades: pd.DataFrame, risk: float) -> pd.Series:
    """Per-rebalance portfolio return: each name weighted `risk`, gross capped at 1."""
    out = {}
    for d, g in trades.groupby("rebalance_date"):
        n = len(g)
        w = risk if n * risk <= 1.0 else 1.0 / n
        out[d] = float((g["net"] * w).sum())
    return pd.Series(out).sort_index()


def _equity_metrics(ret: pd.Series, hold: int) -> dict:
    if ret.empty:
        return {}
    eq = (1.0 + ret).cumprod()
    dd = eq / eq.cummax() - 1.0
    ppy = 252 / hold
    sharpe = float(ret.mean() / ret.std() * np.sqrt(ppy)) if ret.std() > 0 else np.nan
    cagr = float(eq.iloc[-1] ** (ppy / len(ret)) - 1.0) if len(ret) else np.nan
    return {"final_equity": float(eq.iloc[-1]), "cagr": cagr, "sharpe": sharpe,
            "max_dd": float(dd.min()), "calmar": float(cagr / abs(dd.min())) if dd.min() < 0 else np.nan,
            "worst_period": float(ret.min())}


def _top_trades_to_flip(trades: pd.DataFrame) -> float:
    """Fraction of the best trades (by net) whose removal drives total net <= 0."""
    nets = trades["net"].sort_values(ascending=False).to_numpy()
    total = nets.sum()
    if total <= 0:
        return 0.0
    run = total - np.cumsum(nets)
    hit = np.argmax(run <= 0)
    return float((hit + 1) / len(nets)) if run.min() <= 0 else 1.0


def summarize(trades: pd.DataFrame, p: PRLCoarsePolicy, hold: int, label: str) -> dict:
    n = len(trades)
    if n == 0:
        print(f"[{label}] no trades"); return {}
    net = trades["net"]
    win = float((net > 0).mean())
    per_reb = _portfolio_returns(trades, risk=0.03)
    ed = trades.assign(entry=pd.to_datetime(trades["entry_date"]))
    # calendar positivity from the 3%-risk portfolio return series
    pr = per_reb.copy(); pr.index = pd.to_datetime(pr.index)
    wk = pr.resample("W").sum(); mo = pr.resample("ME").sum()
    dow = ed.groupby(ed["entry"].dt.dayofweek)["net"].mean()
    # concentration
    by_sym = trades.groupby("symbol")["net"].sum().sort_values(ascending=False)
    pos_sym = by_sym[by_sym > 0].sum() or 1.0
    top1 = by_sym.iloc[0] / pos_sym
    top5 = by_sym.iloc[:5].sum() / pos_sym
    top10 = by_sym.iloc[:10].sum() / pos_sym
    by_mo = trades.assign(m=ed["entry"].dt.tz_localize(None).dt.to_period("M")).groupby("m")["net"].sum()
    best_mo_share = by_mo.max() / (by_mo[by_mo > 0].sum() or 1.0)
    flip = _top_trades_to_flip(trades)

    print(f"\n=== TRADER SUMMARY [{label}]  hold={hold}d ===")
    print(f"  trades={n}  winrate={win:.3f}  net mean={net.mean()*100:+.3f}%  median={net.median()*100:+.3f}%  "
          f"gross mean={trades['gross'].mean()*100:+.3f}%")
    print(f"  per-rebalance>0={float((per_reb>0).mean()):.2f}  weeks>0={float((wk>0).mean()):.2f}  "
          f"months>0={float((mo>0).mean()):.2f}  (nW={len(wk)}, nM={len(mo)})")
    print(f"  day-of-week net (Mon..Sun): " + " ".join(f"{dow.get(i,np.nan)*100:+.2f}" for i in range(7)))
    print(f"  regime net: " + "  ".join(
        f"{r}:{trades[trades['regime']==r]['net'].mean()*100:+.3f}%(n{(trades['regime']==r).sum()})"
        for r in ("bull", "bear", "sideways") if (trades["regime"] == r).any()))
    print(f"  concentration: top1 sym={top1:.2f}  top5={top5:.2f}  top10={top10:.2f} of positive PnL  "
          f"best-month share={best_mo_share:.2f}")
    print(f"  robustness: remove top {flip*100:.1f}% of trades -> total net <= 0")
    print("  portfolio (open-to-open, 3%/name unless capped):")
    rows = []
    for r in (0.02, 0.03, 0.05):
        met = _equity_metrics(_portfolio_returns(trades, r), hold)
        rows.append({"risk": r, **met})
        print(f"    risk={r*100:.0f}%/name  finalEq={met['final_equity']:.2f}x  CAGR={met['cagr']*100:+.1f}%  "
              f"Sharpe={met['sharpe']:.2f}  maxDD={met['max_dd']*100:.1f}%  Calmar={met['calmar']:.2f}  "
              f"worstReb={met['worst_period']*100:+.1f}%")
    return {"n": n, "winrate": win, "net_mean": float(net.mean()), "net_median": float(net.median()),
            "weeks_pos": float((wk > 0).mean()), "months_pos": float((mo > 0).mean()),
            "top1_sym": float(top1), "top10_sym": float(top10), "best_month_share": float(best_mo_share),
            "flip_frac": flip, "risk_rows": rows}


def _score_from_oof(path: str, col: str, close: pd.DataFrame) -> pd.DataFrame:
    """Pivot an OOF long-form score parquet into a date x symbol matrix aligned to
    the panel (dates re-localized to UTC to match the panel index)."""
    oof = pd.read_parquet(path)
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    mat = oof.pivot(index="date", columns="symbol", values=col)
    return mat.reindex(index=close.index, columns=close.columns)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-q", type=float, default=0.1)
    ap.add_argument("--bottom-q", type=float, default=0.0, help=">0 = add short leg")
    ap.add_argument("--score-file", default="", help="OOF scores parquet (else composite quality)")
    ap.add_argument("--score-col", default="score_linear")
    args = ap.parse_args()
    p = PRIMARY
    oos = pd.Timestamp(p.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, umask, p)
    close, open_ = m["close"], pn.pivot(panel, "open").reindex_like(m["close"])
    umask = m["umask"]
    if args.score_file:
        score = _score_from_oof(args.score_file, args.score_col, close)
        print(f"signal = {args.score_col} from {args.score_file}")
    else:
        feats = ql.quality_features(m["eps"], umask, p, ql.QUALITY_LB)
        score = ql.quality_composite(feats, umask)
        print("signal = composite quality")
    reg = regime_label(close, p)

    OUT.mkdir(parents=True, exist_ok=True)
    allrows = []
    for hold in (p.fwd_horizon, 2 * p.fwd_horizon, 20):
        for delay in (1, 2):
            tr = simulate_book(score, close, open_, umask, reg, p,
                               top_q=args.top_q, bottom_q=args.bottom_q, delay=delay, hold=hold)
            s = summarize(tr, p, hold, f"quality top{int(args.top_q*100)}%"
                          + (f"/short{int(args.bottom_q*100)}%" if args.bottom_q > 0 else "")
                          + f" hold{hold} delay{delay}")
            if s:
                allrows.append({"hold": hold, "delay": delay, **{k: v for k, v in s.items() if k != "risk_rows"}})
    pd.DataFrame(allrows).to_parquet(OUT / "trader_summary_quality.parquet")
    print(f"\nwrote {OUT}/trader_summary_quality.parquet")


if __name__ == "__main__":
    main()
