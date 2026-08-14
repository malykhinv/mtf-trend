"""$1000 deposit simulation of the three-sleeve book + STRICT look-ahead battery.

Book = PRL (short-beta market-neutral) + breakout-Donchian (long-beta trend) + sweep-fade (закол
short-reversion, close-based stop = the audited robust variant). We combine with CAUSAL trailing
vol-parity weights (NOT full-sample -- that would be look-ahead), simulate a $1000 deposit at a
FIXED, non-tuned leverage, and print the dollar equity curve / per-year $ / max drawdown $.

STRICT look-ahead battery:
  1. causal-weights vs full-sample-weights   (does using future vol to weight help? must be ~same)
  2. +1-day execution lag on the whole book   (trade one day late; edge must survive)
  3. SHUFFLE the sweep-fade per-trade PnL      (combined Sharpe must fall back to ~PRL+BO)
  4. sweep-fade trade-level battery reprinted  (conservative fills + delay + 10bps + close-stop)
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.deposit_sim --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.sweep_retest_sleeve import build as build_sweep, COST


def sweep_sleeve(tr, hidx, risk_per=0.005, maxconc=20, shuffle=False):
    hpos = {t: k for k, t in enumerate(hidx)}
    mrel = tr["mrel"].values.copy()
    if shuffle:
        np.random.default_rng(0).shuffle(mrel)
    pnl_h = np.zeros(len(hidx)); open_exits = []
    tr2 = tr.copy(); tr2["m"] = mrel
    for _, r in tr2.sort_values("entry_t").iterrows():
        je, xe = hpos[r.entry_t], hpos[r.exit_t]
        open_exits = [e for e in open_exits if e > je]
        if len(open_exits) >= maxconc:
            continue
        open_exits.append(xe)
        pnl_h[xe] += (risk_per / max(r.risk, 1e-4)) * r.m
    d = pd.Series(pnl_h, index=hidx).resample("1D").sum()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


def stats(d, label, dollars=True):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
    print(f"  {label:26s} Sharpe {sh:+.2f} CAGR {cagr*100:+.0f}% maxDD {dd*100:+.0f}%  [{ys}]")
    return sh, cagr, dd, eq


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--lev", type=float, default=2.0); args = ap.parse_args()

    print("building sweep-fade (close-based stop, audited) ...")
    tr, hidx = build_sweep(args.n)
    sweep = sweep_sleeve(tr, hidx)
    sweep_sh = sweep_sleeve(tr, hidx, shuffle=True)

    print("building PRL + breakout ...")
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]; ret = close.pct_change(fill_method=None)
    oofp = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    score = oofp.assign(date=pd.to_datetime(oofp["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sweep, sweep_sh = map(norm, (prl, bo, sweep, sweep_sh))
    A = pd.concat([prl.rename("PRL"), bo.rename("BO"), sweep.rename("SW")], axis=1).dropna()

    def combine(df, causal=True, lag=0):
        if causal:
            inv = 1.0 / df.rolling(60, min_periods=20).std().shift(1)
        else:
            inv = pd.DataFrame(np.tile(1.0 / df.std().values, (len(df), 1)), index=df.index, columns=df.columns)
        w = inv.div(inv.sum(axis=1), axis=0)
        r = (w.shift(lag) * df).sum(axis=1)
        return r.dropna()

    print("\n=== combined book: look-ahead-strict weight checks ===")
    stats(combine(A, causal=True), "causal weights")
    stats(combine(A, causal=False), "full-sample weights")
    stats(combine(A, causal=True, lag=1), "causal + 1-day exec lag")
    Ash = pd.concat([prl.rename("PRL"), bo.rename("BO"), sweep_sh.rename("SW")], axis=1).dropna()
    stats(combine(Ash, causal=True), "SHUFFLED sweep-fade")
    stats(combine(A[["PRL", "BO"]], causal=True), "PRL+BO only (no sweep)")

    # $1000 deposit sim on the causal book at fixed leverage
    book = combine(A, causal=True); L = args.lev
    print(f"\n=== $1000 deposit simulation (causal weights, FIXED {L:.0f}x leverage, net of 6bps sweep cost) ===")
    sh, cagr, dd, eq = stats(L * book, f"levered {L:.0f}x")
    dollars = 1000 * eq / eq.iloc[0]
    mo = (L * book).resample("ME").apply(lambda x: (1 + x).prod() - 1)
    yr_end = dollars.resample("YE").last()
    print(f"  start $1000  ->  end ${dollars.iloc[-1]:,.0f}   (x{dollars.iloc[-1]/1000:.1f})")
    print(f"  year-end equity: " + "  ".join(f"{d.year}:${v:,.0f}" for d, v in yr_end.items()))
    peak_dd_dollar = (dollars - dollars.cummax()).min()
    print(f"  max drawdown {dd*100:.0f}%  (${peak_dd_dollar:,.0f} peak-to-trough)   best month {mo.max()*100:+.0f}%  worst month {mo.min()*100:+.0f}%")

    # equity-curve chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        unl = 1000 * (1 + book).cumprod(); unl = unl / unl.iloc[0] * 1000
        pb = combine(A[["PRL", "BO"]], causal=True); pbd = 1000 * (1 + pb).cumprod(); pbd = pbd / pbd.iloc[0] * 1000
        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.plot(pbd.index, pbd.values, label="PRL+BO (no закол), 1x", color="#888", lw=1.3)
        ax.plot(unl.index, unl.values, label="3-sleeve (with закол), 1x", color="#1f77b4", lw=1.6)
        ax.plot(dollars.index, dollars.values, label=f"3-sleeve, {L:.0f}x", color="#d62728", lw=1.8)
        ax.set_yscale("log"); ax.set_ylabel("Equity ($, log)"); ax.set_title("$1000 deposit — three-sleeve book (IS 2023-2025, look-ahead-audited)")
        ax.legend(loc="upper left"); ax.grid(True, alpha=0.3, which="both")
        for yv in (2000, 5000, 7009):
            ax.axhline(yv, color="#ccc", lw=0.6, ls=":")
        out = ".output/results/prl_coarse/deposit_equity.png"
        fig.tight_layout(); fig.savefig(out, dpi=110); print(f"  chart -> {out}")
    except Exception as e:
        print("  (chart skipped)", e)


if __name__ == "__main__":
    main()
