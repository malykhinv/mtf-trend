"""Fine-tier hypothesis test (user thread #2): does a higher-frequency (5m/15m)
version of the residual signal give MORE positive weeks than the daily book?

Uses ONLY the already-downloaded enriched_1m data (no new downloads), resampled to
5m/15m (less noisy than raw 1m). Restricted to the IS window 2025-06..2026-01 — the
reserved OOS (2026-H1) is NOT touched even though 1m covers it.

Reuses the frequency-agnostic residual machinery in factors.py. The question is
weekly consistency: more independent bets per week should smooth the equity, which
is the only lever that can move the ~58% positive-weeks ceiling toward the 80% target.

Run: python -m anomaly_science.strategy.prl.research.fine_tier --tf 15min --n 80
"""

from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy

M1_DIR = ".output/market/binance_vision/um_futures/enriched_1m"
IS_START = pd.Timestamp("2025-06-01", tz="UTC")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")  # reserved OOS begins here
AGG = {"open": "first", "high": "max", "low": "min", "close": "last",
       "quote_volume": "sum", "trade_count": "sum", "taker_buy_quote_volume": "sum"}


def _liquid_symbols(n: int) -> list[str]:
    """Top-n perpetuals by total 1m quote volume over the IS window (plain USDT perps)."""
    files = [f for f in glob.glob(f"{M1_DIR}/*.parquet")
             if "_" not in os.path.basename(f) and os.path.basename(f).endswith("USDT.parquet")]
    vols = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["timestamp", "quote_volume"])
        except Exception:
            continue
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        m = (ts >= IS_START) & (ts < IS_END)
        if m.sum() < 20000:  # need enough IS coverage
            continue
        vols.append((os.path.basename(f)[:-8], float(df.loc[m, "quote_volume"].sum())))
    vols.sort(key=lambda x: -x[1])
    return [s for s, _ in vols[:n]]


def load_resampled(symbols: list[str], tf: str) -> pd.DataFrame:
    """Long-form panel at `tf`, IS window only, columns matching the daily pipeline."""
    frames = []
    for s in symbols:
        f = f"{M1_DIR}/{s}.parquet"
        if not os.path.exists(f):
            continue
        df = pd.read_parquet(f, columns=["timestamp", *AGG.keys()])
        df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df[(df.index >= IS_START) & (df.index < IS_END)]
        if df.empty:
            continue
        r = df.resample(tf).agg(AGG).dropna(subset=["close"])
        r["symbol"] = s
        r = r.rename(columns={"trade_count": "number_of_trades"})
        r["date"] = r.index
        frames.append(r.reset_index(drop=True))
    panel = pd.concat(frames, ignore_index=True)
    return panel.sort_values(["symbol", "date"]).reset_index(drop=True)


def _daily_marked(score, close, umask, p, step, hyst=0.5, cost_mult=1.0, bars_per_day=96):
    ret = close.pct_change(fill_method=None)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
    idx = close.index
    barret = pd.Series(0.0, index=idx)
    prev = pd.Series(dtype=float)
    warm = max(max(p.mom_lbs) + p.skip + 2, p.beta_lb + 2)
    for ri in range(warm, len(idx) - step - 1, step):
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
        if hyst > 0 and len(prev):
            w = (1 - hyst) * w + hyst * prev.reindex(w.index).fillna(0.0); w = w - w.mean(); w = w / w.abs().sum()
        turn = float((w.subtract(prev, fill_value=0.0)).abs().sum())
        for dd in range(ri + 1, min(ri + 1 + step, len(idx))):
            barret.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True))
        barret.iloc[ri + 1] -= turn * side
        prev = w
    b = barret.loc[barret.ne(0).cumsum() > 0]
    if b.empty:
        return None
    wk = b.resample("W").sum(); mo = b.resample("ME").sum()
    ppy = bars_per_day * 252
    eq = (1 + b).cumprod()
    cagr = eq.iloc[-1] ** (252 * bars_per_day / len(b)) - 1
    sh = b.mean() / b.std() * np.sqrt(ppy) if b.std() > 0 else np.nan
    return {"cagr": cagr, "sharpe": sh, "wk_pos": float((wk > 0).mean()),
            "mo_pos": float((mo > 0).mean()), "n_weeks": len(wk), "final_eq": float(eq.iloc[-1])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min", help="5min | 15min")
    ap.add_argument("--n", type=int, default=80, help="liquid symbols")
    args = ap.parse_args()
    bpd = {"15min": 96, "5min": 288}[args.tf]

    print(f"selecting {args.n} liquid symbols ...")
    syms = _liquid_symbols(args.n)
    print(f"loading + resampling to {args.tf} (IS {IS_START.date()}..{IS_END.date()}) ...")
    panel = load_resampled(syms, args.tf)
    print(f"  panel: {panel['symbol'].nunique()} symbols x {panel['date'].nunique()} bars")

    close = fx.pn.pivot(panel, "close")
    umask = close.notna() & (fx.pn.pivot(panel, "quote_volume").rolling(bpd, min_periods=bpd // 2).median() > 0)
    # bar-scaled lookbacks: momentum over ~2h/6h/1d, beta over ~5d, forward ~ fwd bars
    lbf = {"15min": (8, 24, 96), "5min": (24, 72, 288)}[args.tf]
    for fwd in ({"15min": [16, 32, 96], "5min": [48, 96, 288]}[args.tf]):
        p = PRLCoarsePolicy(mom_lbs=lbf, skip=1, fwd_horizon=fwd, beta_lb=bpd * 5,
                            beta_min_periods=bpd, min_xs=20)
        m = fx.build_coarse(panel, umask, p)
        # simple score: residual momentum + quality composite (no ML here)
        qf = ql.quality_features(m["eps"], m["umask"], p, q_lb=lbf[-1])
        comp = ql.quality_composite(qf, m["umask"])
        score = (m["score"].rank(axis=1, pct=True) + comp.rank(axis=1, pct=True)) / 2
        label = m["label"]
        # rank-IC
        ics = []
        for i in range(p.beta_lb + 5, len(close) - fwd, fwd):
            a = score.iloc[i].to_numpy(); b = label.iloc[i].to_numpy()
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() >= p.min_xs:
                ics.append(np.corrcoef(pd.Series(a[ok]).rank(), pd.Series(b[ok]).rank())[0, 1])
        ic = float(np.nanmean(ics)) if ics else np.nan
        res = _daily_marked(score, close, m["umask"], p, step=fwd, bars_per_day=bpd)
        hold_h = fwd * (15 if args.tf == "15min" else 5) / 60
        if res:
            print(f"  [{args.tf} fwd={fwd}bars(~{hold_h:.0f}h)]  IC={ic:+.4f}  "
                  f"CAGR={res['cagr']*100:+.0f}%  Sharpe={res['sharpe']:.2f}  "
                  f"+weeks={res['wk_pos']*100:.0f}%  +months={res['mo_pos']*100:.0f}%  (nW={res['n_weeks']})")


if __name__ == "__main__":
    main()
