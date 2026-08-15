"""Quantify SURVIVORSHIP bias on Binance (where selection happened) by including ALL delisted coins.

The main book ranks the universe by SUMMED quote_volume -> mild tilt against young-dead coins (FTT-type
drop below top-N). Here we run the 3 setups on EVERY eligible Binance symbol (>=5000 1h bars pre-2026,
491 coins incl 21 delisted), tag each trade by whether its coin is DEAD (last bar < 2025-06) or ALIVE,
and report each setup's edge on dead vs alive vs all. Hypothesis: survivorship FLATTERS long/breakout
(alive-only dodges coins that broke out then died) and is CONSERVATIVE for the hedged shorts (they'd
have won on the collapses that get dropped). Direct isolate of the bias direction & size.

python -m anomaly_science.strategy.prl.research.survivorship_delta
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

H1 = ".output/market/binance_vision/um_futures/klines_1h"
IS_END = pd.Timestamp("2026-01-01", tz="UTC")
DEAD_CUT = pd.Timestamp("2025-06-01", tz="UTC")
COST = 6 / 1e4


def load_sym(s):
    f = f"{H1}/{s}.parquet"
    d = pd.read_parquet(f, columns=["timestamp", "high", "low", "close", "quote_volume"])
    d.index = pd.to_datetime(d["timestamp"], unit="ms", utc=True)
    d = d[d.index < IS_END]
    return d[d["close"] > 0]


def main():
    files = glob.glob(f"{H1}/*USDT.parquet")
    btc = load_sym("BTCUSDT"); btc_c = btc["close"]
    # eligibility + dead/alive tag + summed-volume rank (to mark the "actually-used" top-100)
    meta = []
    for f in files:
        s = os.path.basename(f)[:-8]
        try:
            d = load_sym(s)
        except Exception:
            continue
        if len(d) < 5000:
            continue
        meta.append((s, d.index.max() < DEAD_CUT, float(d["quote_volume"].sum())))
    meta.sort(key=lambda x: -x[2])
    top100 = {s for s, _, _ in meta[:100]}
    syms = [(s, dead) for s, dead, _ in meta]
    ndead = sum(d for _, d in syms)
    print(f"eligible: {len(syms)}  dead: {ndead}  alive: {len(syms)-ndead}  (top-100 by summed-vol = the book's actual universe)\n")

    rows = {"sweep": [], "breakdown": [], "breakout": []}
    for s, dead in syms:
        try:
            d = load_sym(s)
        except Exception:
            continue
        idx = d.index
        C = d["close"].to_numpy(); Hh = d["high"].to_numpy(); L = d["low"].to_numpy()
        b = btc_c.reindex(idx, method="ffill").to_numpy()
        # beta vs btc, hourly rolling 168
        r = pd.Series(C, idx).pct_change(fill_method=None); brr = pd.Series(b, idx).pct_change(fill_method=None)
        beta = (r.rolling(168, min_periods=48).cov(brr).div(brr.rolling(168, min_periods=48).var())).shift(1).clip(0, 3).fillna(1.0).to_numpy()
        pc = d["close"].shift(1)
        atr = (d["high"] - d["low"]).combine((d["high"] - pc).abs(), np.maximum).combine((d["low"] - pc).abs(), np.maximum).rolling(24, min_periods=12).mean().to_numpy()
        dh = d["high"].resample("1D").max(); dl = d["low"].resample("1D").min(); dc = d["close"].resample("1D").last()
        dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
        datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
        hi_lvl = d["high"].resample("1D").max().rolling(20, min_periods=10).max().shift(1).reindex(idx, method="ffill")
        HL = hi_lvl.to_numpy()
        above = (d["close"].to_numpy() >= HL)
        prev_above = np.concatenate([[False], above[:-1]])
        poke = (Hh >= HL) & (~prev_above) & np.isfinite(HL)
        brk = above & (~prev_above) & np.isfinite(HL)
        n = len(idx)
        # sweep-fade
        for i in np.where(poke)[0]:
            if i + 64 >= n or not (datr[i] > 0):
                continue
            lv = HL[i]; j = None
            for t in range(i, i + 5):
                if C[t] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1]); entry = None
            for u in range(j + 1, j + 13):
                if Hh[u] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            xb = min(start + 48, n - 1); expx = C[xb]
            for u in range(start, min(start + 48, n)):
                if C[u] >= stop:
                    expx, xb = C[u], u; break
                if L[u] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j] * (b[xb] / b[j] - 1.0) - (expx / entry - 1.0) - COST
            rows["sweep"].append((bn, dead))
        # breakdown
        SH, SL = zigzag(Hh, L, atr, 4)
        below = C < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= n or not (C[e] > 0) or not np.isfinite(SH[e]) or not (atr[e] > 0):
                continue
            entry = C[e]; istop = SH[e]
            if istop <= entry or (istop - entry) / entry > 1.0:
                continue
            active = istop; xb, expx = e + 48, C[e + 48]
            for t in range(e + 1, e + 49):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t] > active:
                    xb, expx = t, C[t]; break
            bn = beta[e] * (b[xb] / b[e] - 1.0) - (expx / entry - 1.0) - COST
            rows["breakdown"].append((bn, dead))
        # breakout
        for i in np.where(brk)[0]:
            dj = i + 4
            if dj + 48 >= n or not (C[dj] > 0) or not (C[dj] >= HL[i]):
                continue
            rows["breakout"].append((C[dj + 48] / C[dj] - 1.0 - COST, dead))

    def rep(key):
        d = pd.DataFrame(rows[key], columns=["pnl", "dead"]).replace([np.inf, -np.inf], np.nan).dropna()
        al = d[~d.dead]; de = d[d.dead]
        f = lambda x: (x.pnl.mean() * 100, len(x))
        (am, an), (dm, dn), (tm, tn) = f(al), f(de), f(d)
        print(f"  {key:11s} ALL {tm:+.3f}%(n{tn})   ALIVE {am:+.3f}%(n{an})   DEAD {dm:+.3f}%(n{dn})   dead-minus-alive {dm-am:+.3f}pp")

    print("=== setup edge: ALL-eligible vs ALIVE-only vs DEAD-only (Binance, incl all delisted) ===")
    for k in ("sweep", "breakdown", "breakout"):
        rep(k)
    print("\n  survivorship direction: if DEAD < ALIVE for breakout(long) -> survivorship FLATTERS the long edge.")
    print("  if DEAD > ALIVE for hedged shorts -> those are CONSERVATIVE (understated) in the survivor book.")


if __name__ == "__main__":
    main()
