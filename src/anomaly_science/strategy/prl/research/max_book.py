"""Balanced refined book -> squeeze max return at <=20% DD (no overfit). Honest ceiling check.

Balanced gates (from the grid, without over-tightening): breakout=BTC bull; sweep=BTC bull+side
(kept, not cut to side-only which hurt bull years); breakdown=coin-bear AND BTC-bear (the real
multi-anchor win). Compare baseline / over-refined / balanced by unlevered Calmar, then lever +
DD-throttle each to ~20% maxDD and report achievable CAGR. Reiterates the honest ceiling:
+150-200%/yr at 20% DD needs Calmar 7.5-10; we have ~2. IS 2020-2025. Run:
python -m anomaly_science.strategy.prl.research.max_book --n 150
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
from anomaly_science.strategy.prl.research.final_dashboard import daily_sleeve
from anomaly_science.strategy.prl.research.multi_anchor_book import build


def vt(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def dd_throttle(d, cap=0.20):
    eq = 1.0; peak = 1.0; out = []
    for x in d.values:
        dd = eq / peak - 1.0
        s = np.clip(1.0 - max(0.0, (-dd) / cap - 0.4) / 0.6, 0.3, 1.0)
        out.append(s); eq *= (1 + x * s); peak = max(peak, eq)
    return pd.Series(out, index=d.index).shift(1).fillna(1.0)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building trades ...")
    sw, bd, bo, hidx = build(args.n)

    def sleeves(scheme):
        if scheme == "baseline":
            s, b, o = sw[sw.btc.isin(["bull", "side"])], bd[bd.btc == "bear"], bo[bo.btc == "bull"]
        elif scheme == "over-refined":
            s, b, o = sw[sw.btc == "side"], bd[(bd.coin == "bear") & (bd.btc == "bear")], bo[bo.btc == "bull"]
        else:  # balanced: keep sweep bull+side, breakdown multi-anchor
            s, b, o = sw[sw.btc.isin(["bull", "side"])], bd[(bd.coin == "bear") & (bd.btc == "bear")], bo[bo.btc == "bull"]
        return daily_sleeve(s, hidx), daily_sleeve(b, hidx), daily_sleeve(o, hidx, risk_per=0.01, maxconc=15, dcap=0.06)

    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    prl, _, _ = run_book(close, close.pct_change(fill_method=None), um, score, p, step=15, hyst=0.5)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl = norm(prl)

    def book(scheme):
        s, b, o = map(norm, sleeves(scheme))
        A = pd.concat([prl.rename("p"), o.rename("bo"), s.rename("sw"), b.rename("bd")], axis=1).dropna()
        inv = 1 / A.std(); return vt((A * (inv / inv.sum())).sum(axis=1))

    def lever_to_dd(d, target=-0.20):
        for L in np.arange(0.5, 40.01, 0.25):
            e = (1 + L * d).cumprod()
            if (e / e.cummax() - 1).min() <= target:
                return max(L - 0.25, 0.25)
        return 40.0

    print("\n=== schemes: unlevered Calmar + max CAGR at ~20% DD (lever + DD-throttle) ===")
    for scheme in ["baseline", "over-refined", "balanced"]:
        d = book(scheme).dropna()
        eq = (1 + d).cumprod(); dd0 = float((eq / eq.cummax() - 1).min()); c0 = eq.iloc[-1] ** (252 / len(d)) - 1
        cal = c0 / abs(dd0)
        L = lever_to_dd(d, -0.20); dl = L * d; thr = dd_throttle(dl, cap=0.20); dlt = dl * thr
        ec = (1 + dlt).cumprod(); ddc = float((ec / ec.cummax() - 1).min()); cc = ec.iloc[-1] ** (252 / len(dlt)) - 1
        yr = {int(y): (1 + g).prod() - 1 for y, g in dlt.groupby(dlt.index.year)}
        ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
        print(f"  {scheme:12s} unlevCalmar={cal:4.1f} maxDD0={dd0*100:+3.0f}% | at20%DD: L={L:4.1f}x CAGR={cc*100:+4.0f}% "
              f"maxDD={ddc*100:+3.0f}% minYr={min(yr.values())*100:+.0f}%  [{ys}]")

    print("\n  --- honest ceiling: +150-200%/yr @20%DD needs Calmar 7.5-10; our ~2 => max ~+30-45%/yr @20%DD ---")


if __name__ == "__main__":
    main()
