"""Port the whole PRL study to 1h klines (full 2023-2026 period, ~24x more bars).

We only did daily (coarse) and short-window 1m. But klines_1h covers the FULL
multi-regime period with 24x more observations -> more statistical power and, for a
book, ~24x more independent bets (the direct lever on the weekly-positive ceiling).

Stage A here: build a 1h panel for the top liquid symbols, bar-scaled policy, and
measure residual-momentum IC, quality-composite IC (per year) and a rank-weighted
book's weekly% / Sharpe / per-year. IS only (< 2026-01-01), OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.hourly_study --n 150 --hold 24
"""

from __future__ import annotations

import argparse
import glob
import os
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup

H1_DIR = ".output/market/binance_vision/um_futures/klines_1h"
IS_END = pd.Timestamp("2026-01-01", tz="UTC")
COLS = ["open", "high", "low", "close", "quote_volume", "trade_count", "taker_buy_quote_volume"]


def _liquid(n):
    files = glob.glob(f"{H1_DIR}/*USDT.parquet")
    vols = []
    for f in files:
        try:
            v = pd.read_parquet(f, columns=["timestamp", "quote_volume"])
        except Exception:
            continue
        ts = pd.to_datetime(v["timestamp"], unit="ms", utc=True)
        mask = ts < IS_END
        if mask.sum() < 5000:
            continue
        vols.append((os.path.basename(f)[:-8], float(v.loc[mask, "quote_volume"].sum())))
    vols.sort(key=lambda x: -x[1])
    return [s for s, _ in vols[:n]]


def load_1h(syms):
    frames = []
    for s in syms:
        f = f"{H1_DIR}/{s}.parquet"
        if not os.path.exists(f):
            continue
        df = pd.read_parquet(f, columns=["timestamp", *COLS])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df[df["date"] < IS_END]
        if df.empty:
            continue
        df["symbol"] = s
        df = df.rename(columns={"trade_count": "number_of_trades"})
        frames.append(df[["date", "symbol", *[c for c in COLS if c != "trade_count"], "number_of_trades"]])
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)


def _book(score, close, ret, um, p, hold, hyst=0.5, cost_mult=1.0):
    idx = close.index; daily = pd.Series(0.0, index=idx); prev = pd.Series(dtype=float)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
    warm = _warmup(p)
    for ri in range(warm, len(idx) - hold - 1, hold):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
        if len(prev):
            w = 0.5 * w + 0.5 * prev.reindex(w.index).fillna(0); w = w - w.mean(); w = w / w.abs().sum()
        turn = float((w.subtract(prev, fill_value=0.0)).abs().sum())
        for dd in range(ri + 1, min(ri + 1 + hold, len(idx))):
            daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True))
        daily.iloc[ri + 1] -= turn * side
        prev = w
    d = daily.loc[daily.ne(0).cumsum() > 0]
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--hold", type=int, default=24, help="rebalance/hold in 1h bars")
    args = ap.parse_args()
    print(f"selecting {args.n} liquid symbols + loading 1h (IS<{IS_END.date()}) ...")
    syms = _liquid(args.n)
    panel = load_1h(syms)
    print(f"  panel: {panel['symbol'].nunique()} symbols x {panel['date'].nunique()} hourly bars")

    # 1h-scaled policy: momentum over 1d/3d/7d, beta over ~20d, forward 1d
    p = replace(PRIMARY, mom_lbs=(24, 72, 168), skip=1, fwd_horizon=24, beta_lb=480,
                beta_min_periods=240, universe_n=100, liquidity_lb=720, min_age_days=720, min_xs=20)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um, eps, label = m["close"], m["umask"], m["eps"], m["label"]
    ret = close.pct_change(fill_method=None)
    warm = _warmup(p)
    ev = close.index[warm:]

    beta, rm = m["beta"], m["rm"]
    print("\n=== intraday rolling horizons: residual-momentum IC (neg=reversal) + fade book ===")
    print(f"  {'H(h)':>4} {'IC':>8} {'t':>6} {'wk>0':>5} | fade book (rank-weight -mom, hold=H):")
    for H in (3, 6, 12, 16, 24):
        momH = eps.rolling(H, min_periods=max(2, H // 2)).sum().shift(p.skip)
        fwd_sym = ret.rolling(H, min_periods=H).sum().shift(-H)
        fwd_mkt = rm.rolling(H, min_periods=H).sum().shift(-H)
        labH = fwd_sym.sub(beta.mul(fwd_mkt, axis=0))
        ic = icmod.daily_ic(momH.where(um), labH, p, ev)
        s = icmod.summarize_ic(ic)
        rev = (-momH).where(um)
        parts = []
        for cm in (1.0, 2.0):
            d = _book(rev, close, ret, um, p, H, cost_mult=cm)
            if len(d):
                sh = d.mean() / d.std() * np.sqrt(24 * 365)
                cagr = (1 + d).prod() ** (24 * 365 / len(d)) - 1
                wkp = float((d.resample("W").sum() > 0).mean())
                parts.append(f"x{int(cm)}: CAGR{cagr*100:+.0f}% Sh{sh:+.2f} wk{wkp*100:.0f}%")
        print(f"  {H:>4} {s['ic_mean']:+8.4f} {s['t_stat']:+6.1f} {s['week_pos_share']:5.2f} | " + "  ".join(parts))

    qf = ql.quality_features(eps, um, p, q_lb=168)
    comp = ql.quality_composite(qf, um)
    ic = icmod.daily_ic(comp, label, p, ev); s = icmod.summarize_ic(ic)
    print(f"\n  quality composite (fwd=24h) IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.1f})  wk>0={s['week_pos_share']:.2f}")


if __name__ == "__main__":
    main()
