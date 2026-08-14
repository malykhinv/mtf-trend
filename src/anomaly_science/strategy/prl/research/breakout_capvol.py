"""Breakout winners vs losers by CAP (liquidity proxy) and RELATIVE VOLUME on the
break (user). Splits held/failed events by dollar-volume (cap proxy), breakout-bar
volume surge, and trailing-24h relative volume; reports win-rate, MEDIAN forward
(mean is fat-tail), runner-rate, per year. Causal, market-relative to BTC. IS only.

Run: python -m anomaly_science.strategy.prl.research.breakout_capvol --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

T, H, LVL = 4, 24, 20


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    high = panel.pivot(index="date", columns="symbol", values="high")
    qv = panel.pivot(index="date", columns="symbol", values="quote_volume")
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).reindex(idx).to_numpy()

    dhigh = high.resample("1D").max()
    level = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()

    # cap proxy = trailing 30d median hourly dollar volume; xs rank per hour
    dvol = _roll(qv, 720, "median")
    cap_rank = dvol.rank(axis=1, pct=True)                      # 1 = biggest cap that hour
    vsurge = qv / (_roll(qv, 168, "mean") + 1e-9)              # volume on the breakout bar
    rvol24 = _roll(qv, 24, "sum") / (_roll(qv, 720, "mean") * 24 + 1e-9)  # last-24h vs typical day

    bn, lv = close.to_numpy(), level.to_numpy()
    caps, vss, rvs = cap_rank.to_numpy(), vsurge.to_numpy(), rvol24.to_numpy()
    logdv = np.log(dvol.to_numpy() + 1.0)
    rows = []
    bmask = brk.to_numpy()
    for si, s in enumerate(cols):
        for i in np.where(bmask[:, si])[0]:
            dj, fj = i + T, i + T + H
            if fj >= len(idx) or not (bn[dj, si] > 0):
                continue
            fwd = bn[fj, si] / bn[dj, si] - 1.0
            rel = fwd - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            rows.append({"date": idx[i], "held": bool(bn[dj, si] >= lv[i, si]), "fwd_rel": rel,
                         "cap_rank": caps[i, si], "log_dvol": logdv[i, si],
                         "vsurge": vss[i, si], "rvol24": rvs[i, si]})
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["win"] = (tr.fwd_rel > 0).astype(int)
    tr["runner"] = (tr.fwd_rel > 0.05).astype(int)
    print(f"events={len(tr)}  held={int(tr.held.sum())}  failed={int((~tr.held).sum())}\n")

    def report(g, by, name):
        g = g.dropna(subset=[by]).copy()
        g["q"] = pd.qcut(g[by], 3, labels=["low", "mid", "high"], duplicates="drop")
        print(f"  by {name}:")
        for q, gg in g.groupby("q", observed=True):
            yr = {y: gy.win.mean() for y, gy in gg.groupby("year")}
            ys = " ".join(f"{y}:{v:.2f}" for y, v in yr.items())
            print(f"    {q:4s} n={len(gg):5d}  win={gg.win.mean():.3f}  medFwd={gg.fwd_rel.median()*100:+.2f}%  "
                  f"meanFwd={gg.fwd_rel.mean()*100:+.2f}%  runner={gg.runner.mean():.3f}  win/yr[{ys}]")

    for side, nm in ((True, "HELD (long)"), (False, "FAILED (short)")):
        g = tr[tr.held == side]
        print(f"=== {nm}: n={len(g)}  base win={g.win.mean():.3f}  runner={g.runner.mean():.3f} ===")
        report(g, "cap_rank", "CAP (dollar-volume rank; high=big cap)")
        report(g, "vsurge", "breakout-bar VOLUME SURGE")
        report(g, "rvol24", "last-24h RELATIVE VOLUME")
        print()


if __name__ == "__main__":
    main()
