"""OOS on the WHOLE market + cap-cohort decomposition (BTC / ETH / hicap / other), IS vs OOS.

Answers two questions honestly: (1) why top-100? -> run 3 frozen setups on ALL eligible Binance symbols
(491, >=5000 bars). (2) how does the edge split by cap cohort? -> tag each trade by IS-volume-rank
cohort {btc=rank1, eth=rank2, hicap=3-20, other=21+} and by period {IS <2026-01-01, OOS >=}. Frozen
rules/params (identical to oos_final / survivorship_delta). This is exploratory on OOS (many cells) --
reported in full, no cherry-pick; small per-cell OOS n. Note: beta-neutral setups on BTC hedge ~to zero
by construction.

python -m anomaly_science.strategy.prl.research.oos_wide_cohort
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

H1 = ".output/market/binance_vision/um_futures/klines_1h"
OOS = pd.Timestamp("2026-01-01", tz="UTC")
COST = 6 / 1e4


def load_full(s):
    d = pd.read_parquet(f"{H1}/{s}.parquet", columns=["timestamp", "high", "low", "close", "quote_volume"])
    d.index = pd.to_datetime(d["timestamp"], unit="ms", utc=True)
    return d[d["close"] > 0]


def main():
    files = glob.glob(f"{H1}/*USDT.parquet")
    btc_c = load_full("BTCUSDT")["close"]
    meta = []
    for f in files:
        s = os.path.basename(f)[:-8]
        try:
            d = load_full(s)
        except Exception:
            continue
        is_bars = (d.index < OOS)
        if is_bars.sum() < 5000:
            continue
        meta.append((s, float(d.loc[is_bars, "quote_volume"].sum())))
    meta.sort(key=lambda x: -x[1])
    rank = {s: i for i, (s, _) in enumerate(meta)}   # 0=BTC..
    def cohort(s):
        r = rank[s]
        if s == "BTCUSDT":
            return "btc"
        if s == "ETHUSDT":
            return "eth"
        return "hicap" if r < 20 else "other"
    print(f"eligible whole-market: {len(meta)} symbols\n")

    rows = {"sweep": [], "breakdown": [], "breakout": []}
    for s, _ in meta:
        try:
            d = load_full(s)
        except Exception:
            continue
        idx = d.index; co = cohort(s)
        C = d["close"].to_numpy(); Hh = d["high"].to_numpy(); L = d["low"].to_numpy()
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
            dp = datr[j]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            xb = min(start + 48, n - 1); expx = C[xb]
            for u in range(start, min(start + 48, n)):
                if C[u] >= stop:
                    expx, xb = C[u], u; break
                if L[u] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j] * (b[xb] / b[j] - 1.0) - (expx / entry - 1.0) - COST
            rows["sweep"].append((bn, co, bool(oos_bar[start])))
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
            rows["breakdown"].append((bn, co, bool(oos_bar[e])))
        for i in np.where(brk)[0]:
            dj = i + 4
            if dj + 48 >= n or not (C[dj] > 0) or not (C[dj] >= HL[i]):
                continue
            rows["breakout"].append((C[dj + 48] / C[dj] - 1.0 - COST, co, bool(oos_bar[dj])))

    cohorts = ["btc", "eth", "hicap", "other"]
    for k in ("sweep", "breakdown", "breakout"):
        d = pd.DataFrame(rows[k], columns=["pnl", "co", "oos"]).replace([np.inf, -np.inf], np.nan).dropna()
        print(f"=== {k} ===")
        allis = d[~d.oos]; alloos = d[d.oos]
        print(f"  WHOLE-MARKET   IS {allis.pnl.mean()*100:+.3f}%(n{len(allis)})   OOS {alloos.pnl.mean()*100:+.3f}%(n{len(alloos)})")
        for co in cohorts:
            di = d[(d.co == co) & (~d.oos)]; do = d[(d.co == co) & (d.oos)]
            im = f"{di.pnl.mean()*100:+.3f}%(n{len(di)})" if len(di) else "--"
            om = f"{do.pnl.mean()*100:+.3f}%(n{len(do)})" if len(do) else "--"
            print(f"    {co:6s}  IS {im:>18s}   OOS {om:>16s}")
        print()
    print("read: cohort where IS edge lived & whether ANY cohort survives OOS. beta-neutral BTC ~0 by construction.")


if __name__ == "__main__":
    main()
