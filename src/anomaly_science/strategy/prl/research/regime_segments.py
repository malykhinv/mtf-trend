"""Slice history by CAUSAL BTC regime (bull/bear/sideways x vol) and measure each sleeve per regime.

User idea: stop tying to calendar years; split the whole history into BTC/ETH regimes and get even
ACROSS REGIMES -- then years even out automatically, structurally (not curve-fit). Disciplines:
regime labels are CAUSAL (trailing BTC trend + vol, no hindsight); we BALANCE STRUCTURALLY (an engine
per regime), we do NOT fit weights to regime returns. Output: sleeve x regime table (which engine
profits where), regime coverage (any gap?), and the combined book per regime (even?). IS only.
Run: python -m anomaly_science.strategy.prl.research.regime_segments --n 150
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


def ann(x):
    return x.mean() * 252 * 100


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building short sleeves from 1h ...")
    swtr, bdtr, hidx = short_trades(args.n)
    sweep, bdown = daily_sleeve(swtr, hidx), daily_sleeve(bdtr, hidx)

    print("building PRL + breakout ...")
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    ret = close.pct_change(fill_method=None)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    # CAUSAL BTC regime: trailing 60d return (trend) + trailing 30d vol (calm/volatile)
    btc = pn.pivot(panel, "close")["BTCUSDT"]
    r60 = (btc / btc.shift(60) - 1)
    ma50 = btc.rolling(50, min_periods=25).mean()
    trend = pd.Series("side", index=btc.index)
    trend[(r60 > 0.12) & (btc > ma50)] = "bull"
    trend[(r60 < -0.12) & (btc < ma50)] = "bear"
    vol = btc.pct_change().rolling(30, min_periods=15).std()
    volq = vol.expanding(min_periods=90).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False)
    volst = pd.Series("calm", index=btc.index); volst[volq > 0.6] = "volatile"
    reg = (trend + "/" + volst).shift(1)                       # causal: known day-before
    reg.index = pd.to_datetime(reg.index).tz_localize(None).normalize()

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sweep, bdown = map(norm, (prl, bo, sweep, bdown))
    A = pd.concat([prl.rename("PRL"), bo.rename("BO"), sweep.rename("SW"), bdown.rename("BD")], axis=1).dropna()
    inv = 1 / A.std(); comb = (A * (inv / inv.sum())).sum(axis=1)
    A["COMBO"] = comb
    R = reg.reindex(A.index).fillna("side/calm")

    print("\n=== annualized return by CAUSAL BTC regime (%/yr) ===")
    order = ["bull/calm", "bull/volatile", "side/calm", "side/volatile", "bear/calm", "bear/volatile"]
    present = [g for g in order if (R == g).sum() > 10]
    hdr = "  " + "regime".ljust(16) + "days".rjust(6) + "".join(c.rjust(9) for c in ["PRL", "BO", "SW", "BD", "COMBO"])
    print(hdr)
    for g in present:
        mask = (R == g); days = int(mask.sum())
        cells = "".join(f"{ann(A.loc[mask, c]):+9.0f}" for c in ["PRL", "BO", "SW", "BD", "COMBO"])
        print(f"  {g.ljust(16)}{days:6d}{cells}")
    # trend-only (collapse vol) for a cleaner read
    print("\n=== collapsed to trend only ===")
    Rt = R.str.split("/").str[0]
    print("  " + "regime".ljust(10) + "days".rjust(6) + "%hist".rjust(7) + "".join(c.rjust(9) for c in ["PRL", "BO", "SW", "BD", "COMBO"]))
    for g in ["bull", "side", "bear"]:
        mask = (Rt == g); days = int(mask.sum())
        if days < 10:
            continue
        cells = "".join(f"{ann(A.loc[mask, c]):+9.0f}" for c in ["PRL", "BO", "SW", "BD", "COMBO"])
        print(f"  {g.ljust(10)}{days:6d}{days/len(R)*100:6.0f}%{cells}")
    print("\n  -> COMBO should be POSITIVE in every regime (structural coverage). Any regime where it")
    print("     is negative = a missing engine (gap), to fill structurally -- not by fitting weights.")


if __name__ == "__main__":
    main()
