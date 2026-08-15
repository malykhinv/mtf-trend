"""THE reserved out-of-sample test: Binance 2026-H1 (>= 2026-01-01), one shot, frozen.

Discipline: (1) universe = _liquid(n) which ranks ONLY on data < 2026-01-01 -> the universe knowable at
OOS start, no look-ahead; (2) setup rules + parameters are FROZEN (identical to survivorship_delta /
bybit_prelisting_oos, already committed); (3) a trade counts ONLY if its ENTRY bar is >= 2026-01-01.
Features (ATR/levels/beta) use trailing data crossing the boundary, exactly as a live trader would.
No tuning, no variant choice on this data. Report per-setup OOS edge, N, t, per-month stability, and
the IS number next to it. This is the one test that addresses rule-level multiple-testing.

python -m anomaly_science.strategy.prl.research.oos_final --n 100
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

H1 = ".output/market/binance_vision/um_futures/klines_1h"
OOS = pd.Timestamp("2026-01-01", tz="UTC")
COST = 6 / 1e4
# IS numbers (from survivorship_delta ALL-eligible, frozen reference)
IS_REF = {"sweep": 0.385, "breakdown": 0.270, "breakout": 0.704}


def load_full(s):
    d = pd.read_parquet(f"{H1}/{s}.parquet", columns=["timestamp", "high", "low", "close"])
    d.index = pd.to_datetime(d["timestamp"], unit="ms", utc=True)
    return d[d["close"] > 0]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=100); args = ap.parse_args()
    syms = _liquid(args.n)                      # frozen on IS (<2026-01-01)
    btc_c = load_full("BTCUSDT")["close"]
    btc_oos_ret = btc_c[btc_c.index >= OOS].iloc[-1] / btc_c[btc_c.index >= OOS].iloc[0] - 1
    print(f"universe: top-{args.n} (frozen on IS)  |  BTC 2026-H1 return: {btc_oos_ret*100:+.1f}%  |  OOS = bars >= {OOS.date()}\n")

    rows = {"sweep": [], "breakdown": [], "breakout": []}
    for s in syms:
        try:
            d = load_full(s)
        except Exception:
            continue
        idx = d.index
        if (idx >= OOS).sum() < 24:
            continue
        C = d["close"].to_numpy(); Hh = d["high"].to_numpy(); L = d["low"].to_numpy()
        tsec = idx.to_numpy()
        oos_bar = np.asarray(idx >= OOS)
        b = btc_c.reindex(idx, method="ffill").to_numpy()
        r = d["close"].pct_change(fill_method=None); brr = pd.Series(b, idx).pct_change(fill_method=None)
        beta = (r.rolling(168, min_periods=48).cov(brr).div(brr.rolling(168, min_periods=48).var())).shift(1).clip(0, 3).fillna(1.0).to_numpy()
        pc = d["close"].shift(1)
        atr = (d["high"] - d["low"]).combine((d["high"] - pc).abs(), np.maximum).combine((d["low"] - pc).abs(), np.maximum).rolling(24, min_periods=12).mean().to_numpy()
        dh = d["high"].resample("1D").max(); dl = d["low"].resample("1D").min(); dc = d["close"].resample("1D").last()
        dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
        datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
        hi_lvl = d["high"].resample("1D").max().rolling(20, min_periods=10).max().shift(1).reindex(idx, method="ffill")
        HL = hi_lvl.to_numpy()
        above = C >= HL; prev_above = np.concatenate([[False], above[:-1]])
        poke = (Hh >= HL) & (~prev_above) & np.isfinite(HL)
        brk = above & (~prev_above) & np.isfinite(HL)
        n = len(idx)
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
            if not oos_bar[start]:                 # ENTRY must be in OOS
                continue
            dp = datr[j]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            xb = min(start + 48, n - 1); expx = C[xb]
            for u in range(start, min(start + 48, n)):
                if C[u] >= stop:
                    expx, xb = C[u], u; break
                if L[u] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j] * (b[xb] / b[j] - 1.0) - (expx / entry - 1.0) - COST
            rows["sweep"].append((bn, idx[start]))
        SH, SL = zigzag(Hh, L, atr, 4)
        below = C < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= n or not oos_bar[e] or not (C[e] > 0) or not np.isfinite(SH[e]) or not (atr[e] > 0):
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
            rows["breakdown"].append((bn, idx[e]))
        for i in np.where(brk)[0]:
            dj = i + 4
            if dj + 48 >= n or not oos_bar[dj] or not (C[dj] > 0) or not (C[dj] >= HL[i]):
                continue
            rows["breakout"].append((C[dj + 48] / C[dj] - 1.0 - COST, idx[dj]))

    print(f"{'setup':11s} {'IS%':>7s} {'OOS%':>8s} {'N':>6s} {'t':>6s} {'win%':>6s}   per-month(2026):")
    for k in ("sweep", "breakdown", "breakout"):
        d = pd.DataFrame(rows[k], columns=["pnl", "ts"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 10:
            print(f"  {k:11s} too few (n={len(d)})"); continue
        m = d.pnl.mean() * 100; t = d.pnl.mean() / (d.pnl.std() / np.sqrt(len(d))); win = (d.pnl > 0).mean() * 100
        d["mo"] = d.ts.dt.month
        mo = "  ".join(f"{mm}:{g.pnl.mean()*100:+.2f}" for mm, g in d.groupby("mo"))
        print(f"  {k:11s} {IS_REF[k]:+7.3f} {m:+8.3f} {len(d):6d} {t:6.2f} {win:6.1f}   {mo}")
    print("\n  read: OOS >0 with t>~2 and IS-consistent per-month = edge GENERALIZES; near 0 / negative = it was IS overfit.")
    print("  (sweep/breakdown beta-neutral; breakout raw/directional -> depends on BTC 2026-H1 regime above.)")


if __name__ == "__main__":
    main()
