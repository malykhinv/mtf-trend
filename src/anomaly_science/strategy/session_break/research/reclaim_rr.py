"""RR>=3 filter for the reclaim short: only enter when there is still >=3R of room
to the range mid relative to the stop. Stop = behind the prior-session HIGH, or a
recent STRUCTURAL swing high (both tighter than the poke peak). This removes the
"too late" cases (price already near mid) that killed the naive trade.

Causal & live-executable: RR is computed at the entry bar's open; the outcome is a
first-passage bracket (low<=mid -> win +RR ; high>=stop -> loss -1) over the current
+ next session. GROSS R and breakeven cost reported, sliced by RR band, stop type,
and the OOF MID-score -- does RR>=3 (optionally x good score) finally pay?
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
SWING_LOOKBACK = 6  # bars before entry for the structural swing-high stop
HORIZON_SESS = 6    # cap forward bars ~ a couple sessions of 60m


def run_symbol(path: Path, ev: pd.DataFrame, tf_min: int, stop_kind: str, buf_atr: float) -> pd.DataFrame:
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
        d0 = idx.get(int(r.break_ts))   # the return bar
        if d0 is None or d0 + 2 >= n:
            continue
        ei = d0 + 1
        entry = o[ei]                    # fill next open (causal)
        a = atr[d0]
        if not (a > 0):
            continue
        if stop_kind == "high":
            stop = r.ref_high + buf_atr * a
        elif stop_kind == "struct":
            stop = float(h[max(0, ei - SWING_LOOKBACK):ei].max()) + buf_atr * a
        else:  # poke
            stop = r.poke_high + buf_atr * a
        mid = r.ref_mid
        if not (entry > mid and stop > entry):
            continue
        risk = stop - entry
        rr = (entry - mid) / risk
        # first-passage bracket outcome
        we = min(ei + HORIZON_SESS * 12, n)
        fh = h[ei:we]; fl = lo[ei:we]
        up = np.flatnonzero(fh >= stop); dn = np.flatnonzero(fl <= mid)
        ui = up[0] if len(up) else np.iinfo(np.int64).max
        di = dn[0] if len(dn) else np.iinfo(np.int64).max
        if ui == di == np.iinfo(np.int64).max:
            r_out = (entry - c[we - 1]) / risk          # time exit
        elif ui <= di:
            r_out = -1.0                                 # stopped (pessimistic on tie)
        else:
            r_out = (entry - mid) / risk                 # hit mid = +RR
        rows.append({"symbol": r.symbol, "week": r.week, "score": float(r.score),
                     "label": int(r.label), "rr": rr, "r": r_out,
                     "risk_frac": risk / entry})
    return pd.DataFrame(rows)


def _one(a):
    sym, p, shard, tf, sk, buf = a
    try:
        return run_symbol(Path(p), pd.read_parquet(shard), tf, sk, buf)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--stop", default="high", choices=["high", "struct", "poke"])
    ap.add_argument("--buf-atr", type=float, default=0.1)
    ap.add_argument("--cost", type=float, default=0.0006)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/rr_trades.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    sc = pd.read_parquet(args.scored)
    sc = sc[(sc.break_ts < DEV_END) & sc.score.notna()].drop_duplicates(["symbol", "break_ts"])
    tmp = args.out.parent / "_rr_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in sc.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard), args.tf, args.stop, args.buf_atr))
    print(f"symbols={len(tasks)} stop={args.stop} buf={args.buf_atr}atr")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            f = fut.result()
            if len(f):
                frames.append(f)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(tasks)} trades={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    c = args.cost

    def rep(sub, lbl):
        if len(sub) < 50:
            return f"{lbl:26s} n={len(sub)} (few)"
        nr = (sub.r - c / sub.risk_frac).clip(-10, 10)
        wk = sub.assign(x=nr).groupby("week")["x"].sum()
        be = sub.r.mean() / (1.0 / sub.risk_frac).mean()
        return (f"{lbl:26s} n={len(sub):6d} MID={sub.label.mean():.2f} winRR={(sub.r>0).mean():.2f} "
                f"gross={sub.r.mean():+.3f} net={nr.mean():+.3f} pos_wk={(wk>0).mean():.2f} "
                f"be={be*1e4:.0f}bps risk={100*sub.risk_frac.median():.2f}%")

    print(f"\nwrote {len(t):,} trades | cost={c*1e4:.0f}bps | median RR={t.rr.median():.2f}")
    print(rep(t, "ALL (any RR)"))
    for lo in [1, 2, 3, 4, 5]:
        print(rep(t[t.rr >= lo], f"RR>={lo}"))
    print("--- RR>=3 x score decile ---")
    r3 = t[t.rr >= 3]
    for qs, lbl in [(0.0, "RR>=3 all"), (0.5, "RR>=3 score>50%"), (0.8, "RR>=3 score>80%"), (0.9, "RR>=3 score>90%")]:
        thr = t.score.quantile(qs)
        print(rep(r3[r3.score >= thr], lbl))


if __name__ == "__main__":
    main()
