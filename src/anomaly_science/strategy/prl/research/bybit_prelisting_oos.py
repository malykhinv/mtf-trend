"""Run the 3 setups ONLY on Bybit bars that predate the coin's Binance listing.

Rationale: ALL variant selection (hundreds of configs) happened on Binance IS 2020-25. For coins that
Bybit listed EARLIER than Binance, the pre-Binance-listing window is data that was NEVER available to
the selection process -> a genuinely out-of-selection slice (controls data-feed + coin-specific
overfit; does NOT control shared crypto-wide regime / rule multiple-testing). Small n by construction
-- report it honestly. Compare each setup's edge on this slice vs its full-Bybit number.

python -m anomaly_science.strategy.prl.research.bybit_prelisting_oos --n 250
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.bybit_validate import liquid, load
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4
BIN = ".output/market/binance_vision/um_futures/klines_1h"


def binance_first(sym):
    f = f"{BIN}/{sym}.parquet"
    if not os.path.exists(f):
        return None  # never on Binance at all -> fully independent
    d = pd.read_parquet(f, columns=["timestamp", "close"])
    d = d[d["close"] > 0]
    return pd.to_datetime(d["timestamp"].iloc[0], unit="ms", utc=True) if len(d) else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=250); args = ap.parse_args()
    syms = liquid(args.n)
    panel = load(syms)
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    # per-coin mask: True where bar predates Binance listing (or coin never on Binance)
    bfirst = {s: binance_first(s) for s in cols}
    never = [s for s in cols if bfirst[s] is None]
    early = [s for s in cols if bfirst[s] is not None and idx[0] < bfirst[s]]  # some pre-Binance Bybit data
    print(f"coins: {len(cols)}  | never-on-Binance: {len(never)}  | Bybit-earlier (has pre-listing window): {len(early)}")
    premask = pd.DataFrame(False, index=idx, columns=cols)
    for s in cols:
        bf = bfirst[s]
        if bf is None:
            premask[s] = True
        else:
            premask.loc[idx < bf, s] = True
    total_pre_bars = int(premask.to_numpy().sum())
    print(f"total pre-listing coin-bars (out-of-selection): {total_pre_bars:,}\n")

    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    br = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(br).div(br.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    hi_lvl = high.resample("1D").max().rolling(20, min_periods=10).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    brk = (above & (~above.shift(1).fillna(False)) & hi_lvl.notna()).to_numpy()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    PM = premask.to_numpy(); pk = poke.to_numpy()
    sw, bd, bo = [], [], []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + 64 >= len(idx) or not (datr[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + 5):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 13):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            expx, xb = C[min(start + 48, len(idx) - 1), si], min(start + 48, len(idx) - 1)
            for u in range(start, min(start + 48, len(idx))):
                if C[u, si] >= stop:
                    expx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j, si] * (btc[xb] / btc[j] - 1.0) - (expx / entry - 1.0) - COST
            sw.append((bn, PM[i, si]))
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0):
                continue
            entry = C[e, si]; istop = SH[e]
            if istop <= entry or (istop - entry) / entry > 1.0:
                continue
            active = istop; xb, expx = e + 48, C[e + 48, si]
            for t in range(e + 1, e + 49):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t, si] > active:
                    xb, expx = t, C[t, si]; break
            bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - COST
            bd.append((bn, PM[e, si]))
        for i in np.where(brk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]):
                continue
            bo.append((C[dj + 48, si] / C[dj, si] - 1.0 - COST, PM[dj, si]))

    def rep(rows, label):
        d = pd.DataFrame(rows, columns=["pnl", "pre"]).replace([np.inf, -np.inf], np.nan).dropna()
        full = d.pnl.mean() * 100
        pre = d[d.pre]; oos = pre.pnl.mean() * 100 if len(pre) else np.nan
        # simple t on the pre slice
        t = (pre.pnl.mean() / (pre.pnl.std() / np.sqrt(len(pre)))) if len(pre) > 30 else np.nan
        print(f"  {label:16s} full-Bybit {full:+.3f}% (n{len(d)})  |  PRE-LISTING(OOS) {oos:+.3f}% (n{len(pre)}, t={t:.2f})")

    print("=== setup edge on out-of-selection pre-Binance-listing slice ===")
    rep(sw, "sweep-fade")
    rep(bd, "breakdown")
    rep(bo, "breakout")
    print("\n  NOTE: pre-listing n is small by construction; shared crypto-wide regime NOT controlled.")
    print("        This checks data-feed + coin-specific overfit only, not rule multiple-testing.")


if __name__ == "__main__":
    main()
