"""Three-sleeve book: PRL + breakout-trend + sweep-fade -> new Calmar ceiling & per-year DD.

The sweep-fade (закол) retest sleeve is the first genuinely uncorrelated stream (corr +0.15 with
PRL) that survived the look-ahead audit (close-based stop, +0.41%/trade fully conservative). Here
we combine all three sleeves -- PRL (short-beta market-neutral), breakout-Donchian (long-beta
trend), sweep-fade (short-reversion) -- via risk-parity + a causal vol-target overlay, and score
the result the way the user cares: unlevered Calmar, WORST per-year drawdown, and the leverage that
brings each year's DD to <=15% (and the return there). Does the third uncorrelated sleeve lift the
~3.3 Calmar ceiling? IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.three_sleeve_book --n 150
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
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.sweep_retest_sleeve import build as build_sweep, COST


def per_year_dd(d):
    return {int(y): float(((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min()) for y, g in d.dropna().groupby(d.dropna().index.year)}


def stat(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / max(len(d), 1)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    wk = d.resample("W").sum(); pyd = per_year_dd(d)
    return dict(cagr=cagr, sh=sh, dd=dd, calmar=cagr / abs(dd) if dd < 0 else np.nan,
                wk=float((wk > 0).mean()), worst_pyd=min(pyd.values()) if pyd else np.nan,
                yr={int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)})


def line(nm, d):
    s = stat(d); ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
    print(f"  {nm:26s} CAGR={s['cagr']*100:+4.0f}% Sh={s['sh']:+.2f} maxDD={s['dd']*100:+4.0f}% "
          f"Calmar={s['calmar']:4.1f} +wk={s['wk']*100:2.0f}% worstYrDD={s['worst_pyd']*100:+.0f}%  [{ys}]")
    return s


def vt(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def rp(series):
    al = pd.concat(series, axis=1).dropna(); inv = 1 / al.std(); return (al * (inv / inv.sum())).sum(axis=1)


def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()

    # sweep-fade daily sleeve (risk-managed, close-based stop already in build)
    print("building sweep-fade sleeve ...")
    tr, hidx = build_sweep(args.n)
    hpos = {t: k for k, t in enumerate(hidx)}
    pnl_h = np.zeros(len(hidx)); open_exits = []
    for _, r in tr.sort_values("entry_t").iterrows():
        je, xe = hpos[r.entry_t], hpos[r.exit_t]
        open_exits = [e for e in open_exits if e > je]
        if len(open_exits) >= 20:
            continue
        open_exits.append(xe)
        pnl_h[xe] += (0.005 / max(r.risk, 1e-4)) * (r.mrel - COST)
    sweep = pd.Series(pnl_h, index=hidx).resample("1D").sum()
    sweep.index = pd.to_datetime(sweep.index).tz_localize(None).normalize()

    # daily PRL + breakout
    print("building PRL + breakout ...")
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]; ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    score = oof.assign(date=pd.to_datetime(oof["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sweep = map(norm, (prl, bo, sweep))

    print("\n=== sleeves ===")
    line("PRL", prl); line("Breakout", bo); line("Sweep-fade", sweep)
    al = pd.concat([prl.rename("p"), bo.rename("b"), sweep.rename("s")], axis=1).dropna()
    print(f"\n  corr: PRL-BO {al.p.corr(al.b):+.2f}  PRL-Sweep {al.p.corr(al.s):+.2f}  BO-Sweep {al.b.corr(al.s):+.2f}")

    print("\n=== combined books (vol-parity + vol-target overlay) ===")
    two = line("PRL+BO", rp([prl, bo]))
    line("PRL+BO +VT", vt(rp([prl, bo])))
    line("PRL+Sweep", rp([prl, sweep]))
    best = rp([prl, bo, sweep])                       # un-overlaid 3-sleeve = the winner
    line("PRL+BO+Sweep", best)
    line("PRL+BO+Sweep +VT", vt(rp([prl, bo, sweep])))

    # per-year-DD scoreboard: leverage the best 3-sleeve book to per-year DD = 15%
    print("\n=== scoreboard: lever PRL+BO+Sweep to per-year DD <= 15% and to +150%/yr ===")
    d = best.dropna()
    for tgt, lbl in ((-0.15, "per-year DD<=15%"), (None, "+150% worst-year")):
        L = None
        for LL in np.arange(1.0, 30.01, 0.1):
            if tgt is not None and min(per_year_dd(LL * d).values()) <= tgt:
                L = LL; break
            if tgt is None and min(stat(LL * d)["yr"].values()) >= 1.50:
                L = LL; break
        if L is None:
            print(f"  {lbl}: not reached by 30x"); continue
        s = stat(L * d); ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
        print(f"  {lbl}: L={L:.1f}x -> worst-year {min(s['yr'].values())*100:+.0f}% CAGR {s['cagr']*100:+.0f}% "
              f"maxDD {s['dd']*100:+.0f}% worstYrDD {s['worst_pyd']*100:+.0f}%  [{ys}]")
    print(f"  (unlevered Calmar {stat(d)['calmar']:.1f} vs old ceiling PRL+BO+VT {stat(vt(rp([prl, bo])))['calmar']:.1f})")


if __name__ == "__main__":
    main()
