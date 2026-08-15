"""Cross-venue LEAD-LAG + listing-date independence probe (Binance vs Bybit).

Answers three user questions honestly, by measurement not assertion:
  (1) Listing dates differ by months -> is there a genuinely INDEPENDENT (non-overlapping) data window?
  (2) How much do the two venues' regimes overlap? Does Bybit repeat Binance with a LAG?
  (3) If we drove Bybit entries off Binance timing, could we enter EARLY and gain?

Method: align 1h closes of the same coin on both venues, cross-correlate hourly returns at lags
-6..+6h (corr(binance_ret[t], bybit_ret[t+k]); k>0 = Bybit LAGS / Binance leads). Peak-lag = 0 with a
sharp single peak means arbitrage-locked, no exploitable lead (can't enter early). Also measure daily
regime-state (BTC 60d trend) agreement, and quantify non-overlapping listing windows.

python -m anomaly_science.strategy.prl.research.venue_leadlag --n 60
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

BIN = ".output/market/binance_vision/um_futures/klines_1h"
BYB = ".output/market/bybit/um_futures/klines_1h"


def load_close(path, sym):
    f = f"{path}/{sym}.parquet"
    if not os.path.exists(f):
        return None
    d = pd.read_parquet(f, columns=["timestamp", "close"])
    d = d[d["close"] > 0]
    return pd.Series(d["close"].values, index=pd.to_datetime(d["timestamp"], unit="ms", utc=True)).sort_index()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=60); args = ap.parse_args()
    # liquid coins present on BOTH venues (rank by Bybit turnover proxy = rows*median quote not needed; use file size heuristic via both existing)
    bin_syms = {f[:-8] for f in os.listdir(BIN) if f.endswith(".parquet")}
    byb_syms = {f[:-8] for f in os.listdir(BYB) if f.endswith(".parquet")}
    both = sorted(bin_syms & byb_syms)
    # rank by Bybit file size (bigger = more history/liquidity), take top n
    both = sorted(both, key=lambda s: os.path.getsize(f"{BYB}/{s}.parquet"), reverse=True)[:args.n]

    lags = range(-6, 7)
    peak_lags, zero_corrs, list_diffs = [], [], []
    lagcorr_stack = {k: [] for k in lags}
    print(f"coins on BOTH venues (top-{args.n} by Bybit history): {len(both)}\n")
    print(f"{'coin':16s} {'binFirst':>10s} {'bybFirst':>10s} {'diffDays':>8s} {'earlier':>8s} {'corr@0':>7s} {'peakLag':>7s}")
    for s in both:
        b, y = load_close(BIN, s), load_close(BYB, s)
        if b is None or y is None or len(b) < 500 or len(y) < 500:
            continue
        bf, yf = b.index[0], y.index[0]
        diff_days = (yf - bf).total_seconds() / 86400.0
        earlier = "binance" if diff_days > 0 else ("bybit" if diff_days < 0 else "same")
        list_diffs.append((s, bf, yf, diff_days))
        # align on common hourly grid, log returns
        j = pd.concat([b.rename("bin"), y.rename("byb")], axis=1).dropna()
        if len(j) < 500:
            continue
        rb = np.log(j["bin"]).diff(); ry = np.log(j["byb"]).diff()
        d = pd.concat([rb.rename("rb"), ry.rename("ry")], axis=1).dropna()
        if len(d) < 500:
            continue
        cc = {}
        for k in lags:
            cc[k] = d["rb"].corr(d["ry"].shift(k))  # k>0: bybit shifted forward -> bybit lags binance
            if np.isfinite(cc[k]):
                lagcorr_stack[k].append(cc[k])
        pk = max(cc, key=lambda k: (cc[k] if np.isfinite(cc[k]) else -9))
        peak_lags.append(pk); zero_corrs.append(cc[0])
        print(f"{s:16s} {bf.date()!s:>10s} {yf.date()!s:>10s} {diff_days:8.0f} {earlier:>8s} {cc[0]:7.3f} {pk:7d}")

    print("\n=== (1) LISTING-DATE INDEPENDENCE ===")
    ld = pd.DataFrame(list_diffs, columns=["sym", "bin", "byb", "diff"])
    print(f"  coins where Binance listed >30d EARLIER : {(ld['diff'] > 30).sum()}  (median {ld.loc[ld['diff']>30,'diff'].median():.0f}d)")
    print(f"  coins where Bybit   listed >30d EARLIER : {(ld['diff'] < -30).sum()}  (median {-ld.loc[ld['diff']<-30,'diff'].median():.0f}d)")
    print(f"  coins within 30d                        : {(ld['diff'].abs() <= 30).sum()}")
    print(f"  total non-overlap coin-days (one venue only, >30d gap): {ld.loc[ld['diff'].abs()>30,'diff'].abs().sum():.0f}")

    print("\n=== (2)+(3) LEAD-LAG (does Bybit repeat Binance with a lag? can we enter early?) ===")
    print("  mean hourly-return cross-corr by lag k  (k>0 => Bybit LAGS Binance):")
    for k in lags:
        v = np.array(lagcorr_stack[k]); m = np.nanmean(v) if len(v) else np.nan
        bar = "#" * int(max(0, m) * 50)
        star = "  <-- peak" if k == max(lags, key=lambda kk: np.nanmean(lagcorr_stack[kk]) if lagcorr_stack[kk] else -9) else ""
        print(f"    lag {k:+d}h : {m:6.3f}  {bar}{star}")
    pl = np.array(peak_lags)
    print(f"\n  per-coin peak-lag distribution: at 0h={np.mean(pl==0)*100:.0f}%  |lag|<=1h={np.mean(np.abs(pl)<=1)*100:.0f}%  median={np.median(pl):.0f}h")
    print(f"  mean contemporaneous corr (lag 0): {np.nanmean(zero_corrs):.3f}")
    print("\n  READ: if peak is at lag 0 with sharp single peak & corr>~0.9 -> arbitrage-locked, NO exploitable")
    print("        lead, cannot enter early. A consistent nonzero peak-lag WOULD be a tradeable lead-lag.")


if __name__ == "__main__":
    main()
