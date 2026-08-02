"""Exit-strategy sweep for the scale-in reclaim short. Base position: 0.5 at the
reclaim + 0.5 limit at the breakout high (ref_high); hard stop = 1 close above
ref_high (cover all at that close). On top of that, sweep the PROFIT-side exits:

  mid1        : full position at range mid
  low1        : full position at range low
  mid_low     : 0.5 at mid, 0.5 at low
  mid_trailS  : 0.5 at mid, 0.5 structural trail (SMALL swing 1.0*ATR -- tight, noise-prone)
  mid_trailL  : 0.5 at mid, 0.5 structural trail (LARGE swing 2.5*ATR -- obvious eye-swings)
  trailL      : full structural trail (LARGE 2.5*ATR), no fixed TP

R is vs the structural risk (ref_high - entry). One forward pass per event records
t_mid/t_low/t_stop and the min-low path; each config's R is derived from these. Also
records leg2 fill and pre-leg2 behaviour for the win/loss + limit-fill diagnostic.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.reclaim import _resample

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
HORIZON = 72
BIG = np.iinfo(np.int64).max


# (fixed covers, trail-swing, scale-in add?)
CONFIGS = {
    "mid1":       ([(1.0, "mid")], None, True),
    "low1":       ([(1.0, "low")], None, True),
    "mid_low":    ([(0.5, "mid"), (0.5, "low")], None, True),
    "mid_trailS": ([(0.5, "mid")], 1.0, True),
    "mid_trailL": ([(0.5, "mid")], 2.5, True),
    "trailL":     ([], 2.5, True),
    # no scale-in (base 0.5 only) -- the add looks anti-selective, so test dropping it
    "mid_noadd":  ([(1.0, "mid")], None, False),
    "midlow_noadd": ([(0.5, "mid"), (0.5, "low")], None, False),
    "midtrailL_noadd": ([(0.5, "mid")], 2.5, False),
}


def sim_config(o, h, lo, c, atr, ei, we, hi, mid, low, entry, covers, trail, add=True):
    """Event-driven short with scale-in. lot1 0.5 @ entry (always); resting add 0.5 @ hi
    (leg2, fills when high>=hi). Fixed limit covers (favourable, checked before stops);
    residual optionally trailed (short trail = run_min_low + swing*ATR); hard stop = a
    close above hi covers everything at that close. avg-basis P&L, R vs risk=hi-entry.
    Returns (R, leg2, reason)."""
    risk = hi - entry
    avg = entry; size = 0.5; added = False
    pnl = 0.0
    pend = list(covers)  # remaining fixed covers (frac of full 1.0, level-key)
    lvl = {"mid": mid, "low": low}
    run_min = lo[ei]
    reason = "time"
    for j in range(ei, we):
        run_min = min(run_min, lo[j])
        # resting add at the breakout high
        if add and not added and h[j] >= hi:
            avg = (avg * size + hi * 0.5) / (size + 0.5); size += 0.5; added = True
        # favourable fixed covers first (limit buys hit intrabar when low<=level)
        k = 0
        while k < len(pend):
            frac, key = pend[k]
            if lo[j] <= lvl[key] and size > 1e-12:
                cs = min(frac, size)
                pnl += (avg - lvl[key]) * cs; size -= cs
                pend.pop(k); reason = "tp"
            else:
                k += 1
        if size <= 1e-12:
            return pnl / risk, int(added), reason
        # residual trail (short): exit when high rallies swing*ATR off the run-min
        if trail is not None:
            stop_lvl = run_min + trail * atr[j]
            if h[j] >= stop_lvl:
                pnl += (avg - stop_lvl) * size; size = 0.0
                return pnl / risk, int(added), "trail"
        # hard close-based stop: cover everything at the close
        if c[j] > hi:
            pnl += (avg - c[j]) * size; size = 0.0
            return pnl / risk, int(added), "stop"
    pnl += (avg - c[we - 1]) * size
    return pnl / risk, int(added), reason


def run_symbol(path: Path, ev: pd.DataFrame, tf_min: int) -> pd.DataFrame:
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "volume", "quote_volume", "trade_count",
                                         "taker_buy_quote_volume", "open_interest"])
    df = _resample(raw.sort_values("timestamp").reset_index(drop=True), tf_min)
    ts = df["timestamp"].to_numpy(np.int64); o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    n = len(df); idx = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for r in ev.itertuples():
        d0 = idx.get(int(r.break_ts))
        if d0 is None or d0 + 2 >= n:
            continue
        ei = d0 + 1
        entry = o[ei]; hi = float(r.ref_high); mid = float(r.ref_mid); low = float(r.ref_low)
        risk = hi - entry
        if not (entry > mid and risk > 0):
            continue
        we = min(ei + HORIZON, n)
        out = {"symbol": r.symbol, "week": r.week, "score": float(r.score), "label": int(r.label),
               "risk_frac": risk / entry, "reward_frac": (entry - mid) / entry}
        for name, (covers, trail, add) in CONFIGS.items():
            R, leg2, reason = sim_config(o, h, lo, c, atr, ei, we, hi, mid, low, entry, covers, trail, add)
            out[name] = R
            if name == "mid1":  # canonical leg2/reason for the diagnostic
                out["leg2"] = leg2
                out["reason"] = reason
        rows.append(out)
    return pd.DataFrame(rows)


def _one(a):
    sym, p, shard, tf = a
    try:
        return run_symbol(Path(p), pd.read_parquet(shard), tf)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/exits_trades.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    sc = pd.read_parquet(args.scored)
    sc = sc[(sc.break_ts < DEV_END) & sc.score.notna()].drop_duplicates(["symbol", "break_ts"])
    tmp = args.out.parent / "_ex_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in sc.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard), args.tf))
    print(f"symbols={len(tasks)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            f = fut.result()
            if len(f):
                frames.append(f)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(tasks)} {sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    configs = list(CONFIGS.keys())

    def rep(sub, col):
        r = sub[col].clip(-10, 10)  # winsorize: tiny risk denominators create huge R outliers
        wk = sub.assign(_r=r).groupby("week")["_r"].mean()
        return (f"{col:16s} n={len(sub):6d} win={(r>0).mean():.2f} mean_R={r.mean():+.3f} "
                f"med={r.median():+.3f} pos_wk={(wk>0).mean():.2f}")

    print(f"\nwrote {len(t):,} trades | leg2 fill(mid1)={t.leg2.mean():.2f} | GROSS R (vs entry->level)")
    for col in configs:
        print(rep(t, col))
    print("\n--- leg2 fill vs outcome (mid1) ---")
    for g, sub in t.groupby("leg2"):
        print(f"  leg2={g}: n={len(sub):6d} mid1_meanR={sub.mid1.clip(-10,10).mean():+.3f} win={(sub.mid1>0).mean():.2f} "
              f"reason={sub.reason.value_counts(normalize=True).round(2).to_dict()}")
    print("\n--- best config on the NO-ADD family, by MID-score tier ---")
    for qs, lbl in [(0.0, "all"), (0.5, "score>50%"), (0.8, "score>80%")]:
        s = t[t.score >= t.score.quantile(qs)]
        print(f"  {lbl:10s} " + " | ".join(rep(s, cc).strip() for cc in ["mid_noadd", "midlow_noadd"]))
    print(f"\nrisk_frac (entry->level) median={100*t.risk_frac.median():.3f}% of price")

    # --- reward-size lever: does trading only WIDE-range setups beat costs? ---
    # cost in R = cost_bps/1e4 * legs_traded / risk_frac ; legs ~ (1 taker entry +
    # 1 maker add if leg2 + 1 exit). Use ~2.5 leg-notionals as a round-trip proxy.
    print("\n--- mid_noadd net-after-cost by reward size (entry->mid % of price) ---")
    t["rw_bp"] = t.reward_frac * 1e4
    print(f"{'reward tier':>16} {'n':>7} {'gross':>7} {'net@2bp':>8} {'net@6bp':>8} {'net@10bp':>9} {'pos_wk@6':>9}")
    edges = [(0, 1e9, "all"), (0, 50, "<0.50%"), (50, 100, "0.5-1%"),
             (100, 200, "1-2%"), (200, 400, "2-4%"), (400, 1e9, ">4%")]
    for lo_bp, hi_bp, lbl in edges:
        s = t[(t.rw_bp >= lo_bp) & (t.rw_bp < hi_bp)]
        if len(s) < 200:
            continue
        g = s.mid_noadd.clip(-10, 10)
        def netcol(cb):
            cr = cb / 1e4 * 2.0 / s.risk_frac  # ~2 leg-notionals (no add) round trip / risk
            return (g - cr).clip(-10, 10)
        n6 = netcol(6); wk = s.assign(x=n6).groupby("week")["x"].mean()
        print(f"{lbl:>16} {len(s):>7} {g.mean():>+7.3f} {netcol(2).mean():>+8.3f} "
              f"{n6.mean():>+8.3f} {netcol(10).mean():>+9.3f} {(wk>0).mean():>9.2f}")


if __name__ == "__main__":
    main()
