"""Breakdown-SHORT continuation with a STRUCTURAL 1h-swing trailing stop (user idea).

Downside sweep-fade LONG failed ("dumps keep flying") -> is the mirror true: SHORT the breakdown
and trail structurally, letting the down-continuation run and exiting only on a structural reversal
(close back above the trailing 1h swing high)? We enter short on a breakdown (20d-low and/or an
ATR-zigzag structural swing-low break) and compare exits: hold-to-time, a STRUCTURAL SWING TRAIL
(exit when close > the lowest recent confirmed swing high since entry, ratcheting down), and a fixed
stop/target. Beta-NEUTRAL P&L (shorts fight the up-drift; strip it to see the real edge). Per-year,
mean, win, down-runner tail. On 2023-25 the up-drift is a headwind -- the real proving ground is the
2022 bear (downloading). IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.breakdown_short --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, T, MAXH = 20, 4, 48


def zigzag(H, L, atr, thr):
    """Return per-bar causal 'most recent confirmed swing HIGH' and 'swing LOW' price series (ffill)."""
    n = len(H); sh = np.full(n, np.nan); sl = np.full(n, np.nan)
    direction = 1; ext_i, ext_p = 0, H[0]
    for i in range(1, n):
        if direction == 1:
            if H[i] > ext_p:
                ext_i, ext_p = i, H[i]
            elif atr[ext_i] > 0 and (ext_p - L[i]) >= thr * atr[ext_i]:
                sh[i] = ext_p; direction = -1; ext_i, ext_p = i, L[i]
        else:
            if L[i] < ext_p:
                ext_i, ext_p = i, L[i]
            elif atr[ext_i] > 0 and (H[i] - ext_p) >= thr * atr[ext_i]:
                sl[i] = ext_p; direction = 1; ext_i, ext_p = i, H[i]
    return pd.Series(sh).ffill().to_numpy(), pd.Series(sl).ffill().to_numpy()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1))
    btc_ret = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(btc_ret).div(btc_ret.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    lo_lvl = low.resample("1D").min().rolling(LVL, min_periods=LVL // 2).min().shift(1).reindex(idx, method="ffill")
    below = close <= lo_lvl
    brk = (below & (~below.shift(1).fillna(False)) & lo_lvl.notna()).to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    C, Hh, L, A, LL = (x.to_numpy() for x in (close, high, low, atr, lo_lvl))
    SH = np.column_stack([zigzag(Hh[:, si], L[:, si], A[:, si], 3)[0] for si in range(len(cols))])

    def bnret(si, e, xb, exitpx):
        coin = exitpx / C[e, si] - 1.0
        return beta[e, si] * (btc[xb] / btc[e] - 1.0) - coin - 6 / 1e4     # beta-neutral short

    rows = []
    for si in range(len(cols)):
        for i in np.where(brk[:, si])[0]:
            e = i + T
            if e + MAXH >= len(idx) or not (C[e, si] > 0) or not (C[e, si] <= LL[i, si]) or not (A[i, si] > 0):
                continue
            entry = C[e, si]
            # exits
            # 1) hold-to-time
            xb_h = e + MAXH; r_hold = bnret(si, e, xb_h, C[xb_h, si])
            # 2) structural swing-high trail: exit when close > lowest swing-high seen since entry
            active = SH[e, si] if np.isfinite(SH[e, si]) else entry * 5
            xb_t, expx_t = e + MAXH, C[e + MAXH, si]
            for t in range(e + 1, e + MAXH + 1):
                if np.isfinite(SH[t, si]):
                    active = min(active, SH[t, si])
                if C[t, si] > active:
                    xb_t, expx_t = t, C[t, si]; break
            r_trail = bnret(si, e, xb_t, expx_t)
            # 3) fixed: stop at entry+1*ATR (close), target entry-3*ATR
            stopf = entry + 1.0 * A[i, si]; tpf = entry - 3.0 * A[i, si]
            xb_f, expx_f = e + MAXH, C[e + MAXH, si]
            for t in range(e + 1, e + MAXH + 1):
                if C[t, si] >= stopf:
                    xb_f, expx_f = t, C[t, si]; break
                if L[t, si] <= tpf:
                    xb_f, expx_f = t, tpf; break
            r_fix = bnret(si, e, xb_f, expx_f)
            rows.append(dict(date=idx[e], hold=r_hold, trail=r_trail, fix=r_fix,
                             held_bars=xb_t - e))
    tr = pd.DataFrame(rows); tr["year"] = pd.to_datetime(tr["date"]).dt.year
    print(f"\nbreakdown-short events (confirmed close below 20d-low): {len(tr)}\n")
    print("=== beta-neutral short continuation, exit comparison ===")
    for col, nm in (("hold", "hold-to-time 48h"), ("trail", "STRUCTURAL swing-high trail"), ("fix", "fixed 1ATR stop/3ATR tp")):
        r = tr[col]
        ys = " ".join(f"{y}:{g[col].mean()*100:+.2f}" for y, g in tr.groupby("year"))
        p10 = np.percentile(r, 90)  # short's big winner = coin dumped = positive; p90 of return
        print(f"  {nm:30s} exp={r.mean()*100:+.3f}% win={(r>0).mean():.0%} p90={p10*100:+.1f}% worst={r.min()*100:+.0f}%  [{ys}]")
    print(f"\n  avg trail hold: {tr.held_bars.mean():.0f}h  (vs 48h max) -- how long the trail rides")


if __name__ == "__main__":
    main()
