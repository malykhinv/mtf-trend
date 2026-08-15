"""Find the honest MAX return at <=20% DD: smart risk-control (per-setup VT + DD-throttle) + breadth.

+150-200%/yr at <=20% DD needs Calmar ~7.5-10; we're at ~2. Rather than assert, push Calmar with the
real levers: (1) SMART risk control -- per-setup vol-target (constant vol) instead of crude hard caps,
+ a portfolio DD-throttle -- vs the crude-cap book; (2) more independent bets via top-300 breadth;
then lever each to exactly 20% maxDD and report the achievable annual return. Honest ceiling, no fit.
IS 2020-2025. Run: python -m anomaly_science.strategy.prl.research.max_calmar --n 150
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
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.final_dashboard import build_trades, daily_sleeve


def vt(d, lb=20, cap=4.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def dd_throttle(d, cap=0.20):
    eq = 1.0; peak = 1.0; out = []
    for x in d.values:
        dd = eq / peak - 1.0
        s = np.clip(1.0 - max(0.0, (-dd) / cap - 0.4) / 0.6, 0.3, 1.0)
        out.append(s); eq *= (1 + x * s); peak = max(peak, eq)
    return pd.Series(out, index=d.index).shift(1).fillna(1.0)


def lever_to_dd(d, target=-0.20):
    for L in np.arange(0.5, 30.01, 0.1):
        e = (1 + L * d).cumprod()
        if (e / e.cummax() - 1).min() <= target:
            return max(L - 0.1, 0.1)
    return 30.0


def rep(d, label, target=-0.20):
    d = d.dropna(); c = (1 + d).cumprod().iloc[-1] ** (252 / len(d)) - 1
    dd = float(((1 + d).cumprod() / (1 + d).cumprod().cummax() - 1).min())
    cal = c / abs(dd) if dd < 0 else np.nan
    L = lever_to_dd(d, target); dl = L * d - 0  # note: DD not linear; recompute
    thr = dd_throttle(dl, cap=-target)
    dlt = dl * thr
    cc = (1 + dlt).cumprod().iloc[-1] ** (252 / len(dlt)) - 1
    ddc = float(((1 + dlt).cumprod() / (1 + dlt).cumprod().cummax() - 1).min())
    yr = {int(y): (1 + g).prod() - 1 for y, g in dlt.groupby(dlt.index.year)}
    ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
    print(f"  {label:26s} unlev Calmar={cal:4.1f} | at ~20%DD: L={L:4.1f}x CAGR={cc*100:+4.0f}% maxDD={ddc*100:+3.0f}% minYr={min(yr.values())*100:+.0f}%  [{ys}]")


def build_book(n, smart):
    sw, bd, bo, hidx = build_trades(n)
    sw_g = sw[sw.regime.isin(["bull", "side"])]; bd_g = bd[bd.regime == "bear"]; bo_g = bo[bo.regime == "bull"]
    if smart:
        sweep = daily_sleeve(sw_g, hidx, risk_per=0.005, maxconc=20, dcap=0.10)
        bdown = daily_sleeve(bd_g, hidx, risk_per=0.005, maxconc=20, dcap=0.10)
        bout = daily_sleeve(bo_g, hidx, risk_per=0.01, maxconc=25, dcap=0.15)
    else:
        sweep = daily_sleeve(sw_g, hidx, risk_per=0.005, maxconc=8, dcap=0.03)
        bdown = daily_sleeve(bd_g, hidx, risk_per=0.005, maxconc=8, dcap=0.03)
        bout = daily_sleeve(bo_g, hidx, risk_per=0.01, maxconc=15, dcap=0.06)
    return sweep, bdown, bout


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    prl, _, _ = run_book(close, close.pct_change(fill_method=None), um, score, p, step=15, hyst=0.5)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl = norm(prl)

    print("building setup sleeves (crude-cap vs smart per-setup VT) ...")
    for smart, nm in [(False, "CRUDE hard-cap"), (True, "SMART per-setup VT")]:
        sweep, bdown, bout = build_book(args.n, smart)
        sweep, bdown, bout = map(norm, (sweep, bdown, bout))
        cols = [prl.rename("p")]
        if smart:
            cols += [vt(bout).rename("bo"), vt(sweep).rename("sw"), vt(bdown).rename("bd")]
        else:
            cols += [bout.rename("bo"), sweep.rename("sw"), bdown.rename("bd")]
        A = pd.concat(cols, axis=1).dropna(); inv = 1 / A.std()
        raw = (A * (inv / inv.sum())).sum(axis=1)
        book = vt(raw) if smart else raw
        print(f"\n--- {nm} ---")
        rep(book, "regime-gated book")


if __name__ == "__main__":
    main()
