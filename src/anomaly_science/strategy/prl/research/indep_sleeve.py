"""Genuinely INDEPENDENT market-neutral sleeves (non-momentum) -> raise combined Calmar.

The Calmar ceiling (~3.3) comes from having only ~2 return engines (PRL residual-momentum;
breakout-beta). Diversification lifts portfolio Calmar ~ c*sqrt(N) only if the added stream
is UNCORRELATED and has its own decent Calmar. Classic non-momentum market-neutral factors:

  A. BAB (betting-against-beta): long low-beta / short high-beta, dollar-neutral.
  B. LOW-VOL: long low idiosyncratic-vol / short high, dollar-neutral.
  C. SHORT-TERM REVERSAL (weekly): long last-week losers / short winners (residualized).

Each built via the overlapping() dollar-neutral harness; report standalone Calmar + per-year
DD + correlation with PRL and the breakout sleeve. Then add the best to the vol-parity PRL+BO
book (+vol-target overlay) and check whether combined Calmar rises above 3.3. IS only, OOS
reserved. Run: python -m anomaly_science.strategy.prl.research.indep_sleeve
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.overlap import overlapping
from anomaly_science.strategy.prl.research.policy import PRIMARY


def per_year_dd(d):
    d = d.dropna(); return {int(y): float(((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min())
                            for y, g in d.groupby(d.index.year)}


def line(name, d, ref=None):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    cal = cagr / abs(dd) if dd < 0 else np.nan
    wp = min(per_year_dd(d).values())
    cs = ""
    if ref is not None:
        cs = "  " + " ".join(f"corr({k})={d.align(v, join='inner')[0].corr(d.align(v, join='inner')[1]):+.2f}"
                             for k, v in ref.items())
    print(f"  {name:24s} CAGR={cagr*100:+4.0f}% Sh={sh:+.2f} maxDD={dd*100:+4.0f}% Calmar={cal:4.1f} "
          f"worstYrDD={wp*100:+.0f}%{cs}")
    return d


def vol_target(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1)
    sc = (1.0 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def rp(series):
    al = pd.concat(series, axis=1).dropna(); inv = 1 / al.std(); return (al * (inv / inv.sum())).sum(axis=1)


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    mret = ret.where(um).mean(axis=1)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)
    ref = {"PRL": prl, "BO": bo}

    # causal beta & idio-vol
    w = 60
    cov = ret.rolling(w, min_periods=30).cov(mret)
    var = mret.rolling(w, min_periods=30).var()
    beta = cov.div(var, axis=0).shift(1)
    resid = ret.sub(beta.mul(mret, axis=0))
    idio = resid.rolling(20, min_periods=10).std().shift(1)
    rev = (close / close.shift(5) - 1)
    rev = rev.sub(rev.where(um).mean(axis=1), axis=0)

    print("=== independent market-neutral sleeves (overlapping dollar-neutral) ===")
    bab = overlapping(close, ret, um, (-beta), p, H=15, hyst=0.5)
    lowvol = overlapping(close, ret, um, (-idio), p, H=15, hyst=0.5)
    strev = overlapping(close, ret, um, (-rev).shift(1), p, H=5, hyst=0.5)
    line("BAB (low-beta)", bab, ref)
    line("LOW-VOL", lowvol, ref)
    line("ST-REVERSAL (wk)", strev, ref)

    print("\n=== reference & combined books (vol-parity + vol-target overlay) ===")
    line("PRL", prl); line("Breakout", bo)
    j = pd.concat([prl.rename("p"), bo.rename("b")], axis=1).dropna()
    base = rp([j["p"], j["b"]])
    line("PRL+BO vp", base)
    line("PRL+BO vp +VT", vol_target(base))
    # add each independent sleeve
    for nm, s in (("+BAB", bab), ("+LOWVOL", lowvol), ("+BAB+LOWVOL", None)):
        if s is None:
            comb = rp([prl, bo, bab, lowvol])
        else:
            comb = rp([prl, bo, s])
        line(f"PRL+BO{nm} vp +VT", vol_target(comb))

    # scoreboard on the best (choose max Calmar among candidates)
    cands = {"PRL+BO": vol_target(base),
             "PRL+BO+BAB": vol_target(rp([prl, bo, bab])),
             "PRL+BO+BAB+LOWVOL": vol_target(rp([prl, bo, bab, lowvol]))}
    def cal(d):
        d = d.dropna(); eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
        return (eq.iloc[-1] ** (252 / len(d)) - 1) / abs(dd)
    bestnm = max(cands, key=lambda k: cal(cands[k])); d = cands[bestnm].dropna()
    print(f"\n=== SCOREBOARD (best: {bestnm}, Calmar {cal(d):.2f}) ===")
    for LL in np.arange(1.0, 30.01, 0.1):
        if min(per_year_dd(LL * d).values()) <= -0.15:
            yr = {y: (1 + g).prod() - 1 for y, g in (LL * d).groupby((LL * d).index.year)}
            print(f"  at per-year DD=15% (L={LL:.1f}x): worst-year {min(yr.values())*100:+.0f}%  "
                  f"[{' '.join(f'{v*100:+.0f}%' for v in yr.values())}]"); break


if __name__ == "__main__":
    main()
