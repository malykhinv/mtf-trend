"""Adaptive position sizing: per-setup x per-regime x regime-DURATION, DD-capped at 15%.

Two parts. (A) Test the user's regime-DURATION idea causally: does each setup's edge GROW or SHRINK
as its regime ages (momentum "ride the established trend" vs reversion "extended move is due")?
(B) Build the adaptive book: each setup gated to its home regime (breakout=bull, sweep=side,
breakdown=bear) with risk scaled by regime-fit and (if part A supports) duration, then a causal
VOL-TARGET + DRAWDOWN-THROTTLE, levered to hold maxDD ~15% at max return. Compare to the static
book. All signals causal. IS only (2020-2025), OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.adaptive_sizing --n 150
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
from anomaly_science.strategy.prl.research.full_book_6y import short_trades, daily_sleeve
from anomaly_science.strategy.prl.research.regime_modes import stabilized_regime


def dd_throttle(d, cap=0.15, lookback_peak=252):
    """Causal: scale next-day gross down as trailing drawdown approaches cap."""
    eq = 1.0; peak = 1.0; scale = []
    for x in d.values:
        dd = eq / peak - 1.0
        s = np.clip(1.0 - max(0.0, (-dd) / cap - 0.3) / 0.7, 0.25, 1.0)   # start cutting at 30% of cap
        scale.append(s)
        eq *= (1 + x * s); peak = max(peak, eq)
    return pd.Series(scale, index=d.index).shift(1).fillna(1.0)


def vt(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return sc


def stats(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1; sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    return cagr, dd, sh, yr


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building sleeves ...")
    swtr, bdtr, hidx = short_trades(args.n)
    sweep, bdown = daily_sleeve(swtr, hidx), daily_sleeve(bdtr, hidx)
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    ret = close.pct_change(fill_method=None)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)
    btc = pn.pivot(panel, "close")["BTCUSDT"]
    trend, volst = stabilized_regime(btc)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sweep, bdown = map(norm, (prl, bo, sweep, bdown))
    for x in (trend, volst):
        x.index = pd.to_datetime(x.index).tz_localize(None).normalize()
    A = pd.concat([prl.rename("PRL"), bo.rename("BO"), sweep.rename("SW"), bdown.rename("BD")], axis=1).dropna()
    T = trend.reindex(A.index).ffill().fillna("side"); V = volst.reindex(A.index).ffill().fillna("calm")

    # regime AGE (causal days since current trend started)
    age = (T != T.shift(1)).cumsum(); age = T.groupby(age).cumcount()

    print("\n=== (A) does each setup's edge depend on regime AGE? (home regime, age terciles) ===")
    home = {"BO": "bull", "SW": "side", "BD": "bear"}
    for s, hr in home.items():
        mask = (T == hr)
        a = age[mask]
        if a.nunique() < 3 or mask.sum() < 60:
            continue
        q = pd.qcut(a, 3, labels=["young", "mid", "old"], duplicates="drop")
        cells = "  ".join(f"{lab}:{A.loc[mask][q==lab][s].mean()*252*100:+.0f}%" for lab in ["young", "mid", "old"] if (q==lab).any())
        print(f"  {s} in {hr:5s}: {cells}")

    # (B) adaptive book: gate each setup to home regime (sign-based on/off), risk-parity, vt, dd-throttle
    print("\n=== (B) adaptive regime-gated book vs static ===")
    G = pd.DataFrame(0.0, index=A.index, columns=["PRL", "BO", "SW", "BD"])
    G["PRL"] = 1.0                                          # market-neutral, always on
    G.loc[T == "bull", "BO"] = 1.0
    G.loc[T != "bear", "SW"] = 1.0                          # sweep in bull+side (not bear)
    G.loc[T == "bear", "BD"] = 1.0
    G.loc[(T == "side") & (V == "volatile"), :] = 0.0       # stand down in volatile chop
    inv = 1 / A.std()
    gated = (A * G.shift(1).fillna(0.0) * inv).sum(axis=1) / (G.shift(1).fillna(0.0) * inv).sum(axis=1).replace(0, np.nan)
    gated = gated.fillna(0.0)
    static = (A * inv).sum(axis=1) / inv.sum()

    for nm, d in [("static all-on", static), ("regime-gated", gated), ("gated + VT", gated * vt(gated))]:
        c, dd, sh, yr = stats(d); ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
        print(f"  {nm:16s} CAGR={c*100:+4.0f}% maxDD={dd*100:+4.0f}% Sh={sh:+.2f}  [{ys}]")

    # lever gated+VT+dd-throttle to maxDD ~15%
    base = (gated * vt(gated)).dropna()
    print("\n=== lever gated+VT+DD-throttle to hold maxDD ~15% ===")
    for L in (3, 4, 5, 6, 8):
        thr = dd_throttle(base * L, cap=0.15)
        d = base * L * thr
        c, dd, sh, yr = stats(d); ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
        print(f"  L={L}x  CAGR={c*100:+4.0f}% maxDD={dd*100:+4.0f}% minYr={min(yr.values())*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
