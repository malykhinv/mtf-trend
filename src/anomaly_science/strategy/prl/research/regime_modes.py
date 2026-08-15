"""Stabilized causal regime MODES + regime-conditional trading -> make every mode >= 0.

Two fixes, both causal: (1) stabilize the online regime with hysteresis + min-dwell so the bot
doesn't flip modes on noise (the whipsaw region = the volatile-sideways gap); (2) in the modes
where a sleeve historically LOSES (esp sweep-fade crashing in volatile-sideways), STAND DOWN that
sleeve (forbid the negative setups). Then re-measure the sleeve x mode table and check COMBO >= 0
in every mode. The regime->action mapping is fixed by the per-regime SIGN we already measured
(economic, not fit to magnitude). IS only. Run: python -m anomaly_science.strategy.prl.research.regime_modes --n 150
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
from anomaly_science.strategy.prl.research.full_book_6y import short_trades, daily_sleeve


def stabilized_regime(btc, min_dwell=10):
    """Causal trend+vol regime with hysteresis (enter bull/bear on strong signal, exit only on
    opposite/neutral confirmation) and a minimum dwell time to kill whipsaw."""
    r60 = btc / btc.shift(60) - 1
    ma50 = btc.rolling(50, min_periods=25).mean()
    raw = pd.Series("side", index=btc.index)
    raw[(r60 > 0.12) & (btc > ma50)] = "bull"
    raw[(r60 < -0.12) & (btc < ma50)] = "bear"
    # hysteresis: require 5 consecutive raw days to switch, then hold >= min_dwell days
    out = []
    cur = "side"; run_lab = None; run_n = 0; dwell = 0
    for v in raw.values:
        if v == run_lab:
            run_n += 1
        else:
            run_lab, run_n = v, 1
        dwell += 1
        if run_n >= 5 and v != cur and dwell >= min_dwell:
            cur = v; dwell = 0
        out.append(cur)
    trend = pd.Series(out, index=btc.index).shift(1)
    vol = btc.pct_change().rolling(30, min_periods=15).std()
    volq = vol.expanding(min_periods=90).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False)
    volst = pd.Series("calm", index=btc.index); volst[volq > 0.6] = "volatile"
    return trend, volst.shift(1)


def ann(x):
    return x.mean() * 252 * 100 if len(x) else np.nan


def main():
    import argparse
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
    T = trend.reindex(A.index).fillna("side"); V = volst.reindex(A.index).fillna("calm")
    mode = (T + "/" + V)

    print(f"\nstabilized regime flips: {int((T!=T.shift(1)).sum())} (was 178 raw)")

    def combo(df, cols):
        inv = 1 / df[cols].std(); return (df[cols] * (inv / inv.sum())).sum(axis=1)

    # baseline combo (all sleeves always on)
    A["COMBO"] = combo(A, ["PRL", "BO", "SW", "BD"])

    # MODE-CONDITIONAL: stand down the sleeves that lose in each mode (by measured SIGN)
    # rule (economic, from per-regime signs): in volatile-sideways, sweep-fade & breakout stand down
    # (sweep crashes, breakout chops); keep BD+PRL. Elsewhere all on.
    w = pd.DataFrame(1.0, index=A.index, columns=["PRL", "BO", "SW", "BD"])
    volside = (T == "side") & (V == "volatile")
    w.loc[volside, :] = 0.0                          # FULL stand-down (cash) in volatile chop -> mode = 0
    volbear = (T == "bear") & (V == "volatile")
    w.loc[volbear, "BO"] = 0.0                       # breakout stands down in volatile bear too
    condA = A[["PRL", "BO", "SW", "BD"]] * w.shift(1).fillna(1.0)
    inv = 1 / A[["PRL", "BO", "SW", "BD"]].std()
    A["COND"] = (condA * (inv / inv.sum())).sum(axis=1)

    print("\n=== annualized %/yr by stabilized mode: baseline COMBO vs mode-CONDITIONAL ===")
    order = ["bull/calm", "bull/volatile", "side/calm", "side/volatile", "bear/calm", "bear/volatile"]
    print("  " + "mode".ljust(16) + "days".rjust(6) + "COMBO".rjust(9) + "COND".rjust(9))
    for g in order:
        mask = (mode == g); d = int(mask.sum())
        if d < 8:
            continue
        print(f"  {g.ljust(16)}{d:6d}{ann(A.loc[mask,'COMBO']):+9.0f}{ann(A.loc[mask,'COND']):+9.0f}")
    print("\n=== overall ===")
    for nm in ("COMBO", "COND"):
        d = A[nm].dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
        sh = d.mean() / d.std() * np.sqrt(252)
        yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
        ys = " ".join(f"{k}:{v*100:+.0f}%" for k, v in yr.items())
        print(f"  {nm:6s} Sh={sh:+.2f} maxDD={dd*100:+.0f}% minYr={min(yr.values())*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
