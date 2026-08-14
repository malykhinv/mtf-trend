"""Plain trader's-eye stats for the breakout sleeve: entries, exits, takes, stops, streaks.

The sleeve enters a confirmed-hold 20d-high breakout (wait T=4h after the break; if still above
the level, go long at that price) and exits by TIME (hold 48h, take whatever the market gives --
no hard stop). This script speaks trader: win-rate, average take, average loss, payoff ratio,
expectancy, the SPREAD of takes/stops (percentiles), MFE/MAE (how far price ran for/against
before exit), win/loss STREAKS in time order, and how all of it shifts by regime / volume / year.
Both absolute (raw price move) and market-relative (vs BTC). IS only. Run:
python -m anomaly_science.strategy.prl.research.trade_talk --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, T, HOLD = 20, 4, 48


def desc(r, label):
    r = np.asarray(r); w = r[r > 0]; l = r[r <= 0]
    wr = len(w) / len(r); aw = w.mean() if len(w) else 0; al = l.mean() if len(l) else 0
    payoff = aw / abs(al) if al != 0 else np.nan
    exp = r.mean()
    print(f"  {label:22s} n={len(r):5d} win={wr:.1%} avgTake={aw*100:+.2f}% avgLoss={al*100:+.2f}% "
          f"payoff={payoff:.2f} exp={exp*100:+.2f}% med={np.median(r)*100:+.2f}%")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = (above & (~above.shift(1).fillna(False)) & level.notna()).to_numpy()
    breadth = above.mean(axis=1).to_numpy()
    vs = (qv / qv.rolling(168, min_periods=48).mean()).to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    C, Hh, L, A, Ln = close.to_numpy(), high.to_numpy(), low.to_numpy(), atr.to_numpy(), level.to_numpy()

    rows = []
    for si in range(len(cols)):
        for i in np.where(brk[:, si])[0]:
            dj = i + T
            if dj + HOLD >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= Ln[i, si]) or not (A[i, si] > 0):
                continue
            p0 = C[dj, si]; path_h = Hh[dj + 1:dj + HOLD + 1, si]; path_l = L[dj + 1:dj + HOLD + 1, si]
            raw = C[dj + HOLD, si] / p0 - 1.0
            rel = raw - (btc[dj + HOLD] / btc[dj] - 1.0)
            mfe = np.nanmax(path_h) / p0 - 1.0; mae = np.nanmin(path_l) / p0 - 1.0
            rows.append(dict(date=idx[dj], si=si, raw=raw, rel=rel, mfe=mfe, mae=mae,
                             breadth=breadth[dj], vsurge=vs[i, si], atr_pct=A[i, si] / p0,
                             year=idx[dj].year))
    tr = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    print(f"\n=== how the trade works ===")
    print(f"  ENTRY: 20d-high breaks -> wait {T}h -> if still above the level, buy at market")
    print(f"  EXIT:  hold {HOLD}h, sell at market. NO stop, NO target. {len(tr)} trades.\n")

    print("=== average trade (market-relative, our real edge) ===")
    desc(tr.rel, "all trades")
    print("=== average trade (absolute / raw price move, how a chartist sees it) ===")
    desc(tr.raw, "all trades")

    print("\n=== the SPREAD (market-relative %, percentiles) ===")
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    vals = np.percentile(tr.rel * 100, ps)
    print("  " + "  ".join(f"p{p}={v:+.1f}%" for p, v in zip(ps, vals)))
    print(f"  biggest win {tr.rel.max()*100:+.0f}%   biggest loss {tr.rel.min()*100:+.0f}%   "
          f"std {tr.rel.std()*100:.1f}%")

    print("\n=== where price went DURING the 48h hold (MFE = best unrealized, MAE = worst) ===")
    print(f"  avg MFE (max run up)   {tr.mfe.mean()*100:+.1f}%  median {tr.mfe.median()*100:+.1f}%")
    print(f"  avg MAE (max drawdown) {tr.mae.mean()*100:+.1f}%  median {tr.mae.median()*100:+.1f}%")
    print(f"  -> winners' avg MAE {tr[tr.rel>0].mae.mean()*100:+.1f}% (how far a winner dips first), "
          f"losers' avg MFE {tr[tr.rel<=0].mfe.mean()*100:+.1f}% (how far a loser teases up first)")

    print("\n=== STREAKS (trades in time order, market-relative win/loss) ===")
    signs = (tr.rel > 0).astype(int).values
    runs = []; cur = signs[0]; ln = 1
    for s in signs[1:]:
        if s == cur:
            ln += 1
        else:
            runs.append((cur, ln)); cur = s; ln = 1
    runs.append((cur, ln))
    winruns = [n for s, n in runs if s == 1]; losruns = [n for s, n in runs if s == 0]
    print(f"  longest win streak {max(winruns)}  longest loss streak {max(losruns)}")
    print(f"  avg win streak {np.mean(winruns):.1f}  avg loss streak {np.mean(losruns):.1f}  "
          f"(random at win-rate {signs.mean():.0%} -> ~{1/(1-signs.mean()):.1f}/{1/signs.mean():.1f})")

    print("\n=== what it DEPENDS ON ===")
    print("  by RISK-ON regime (market breadth tercile):")
    tr["breg"] = pd.qcut(tr.breadth, 3, labels=["risk-off", "mid", "risk-on"])
    for a, gg in tr.groupby("breg", observed=True):
        desc(gg.rel, f"    {a}")
    print("  by BREAKOUT VOLUME (relative-volume tercile):")
    tr["vreg"] = pd.qcut(tr.vsurge.clip(upper=tr.vsurge.quantile(0.99)), 3, labels=["quiet", "mid", "loud"])
    for a, gg in tr.groupby("vreg", observed=True):
        desc(gg.rel, f"    {a}")
    print("  by YEAR:")
    for a, gg in tr.groupby("year"):
        desc(gg.rel, f"    {a}")


if __name__ == "__main__":
    main()
