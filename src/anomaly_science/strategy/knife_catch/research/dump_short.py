"""EARLY SHORT the dump (ride the continuation down), the mirror of the failed buy-back.

Eyeball + stats say single-coin dumps mostly make a NEW LOW, not a buy-back. So SHORT the
dump early: detect a calm SLEEP base, a downward BREAK out of it with seller aggression, enter
short next-open, stop just above the sleep (a reclaim invalidates), and ride down. Structure
favours the short: small stop (to the sleep top), big target (continuation). Survivorship helps
(dead coins that went to zero are absent -> edge UNDERstated). We still exclude the crash week,
compare to a matched random-short placebo, and demand top%toNeg>=40 & week-stability.

Entry (all causal, at bar j): sleep = [j-SLEEP, j-1] tight (range<=MAX_SLEEP_RANGE);
break = close[j] < sleep_lo*(1-BREAK_MARGIN), red, taker-buy<TAKER_MAX (sellers aggressive).
Exit families: FIX m (target=entry-m*risk), TRAIL p (trail down from run_min). Slippage-aware.
DEV only.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (5, 10)
BREAK_LOOK_MIN = 60         # break below the recent 1h low = a fresh downside break (ANY prior structure)
STOP_LOOK_MIN = 120         # stop above the recent 2h high
BREAK_MARGIN = 0.01         # close must be >=1% below the recent low to confirm a break
TAKER_MAX = 0.45            # seller aggression (taker-buy share low)
STOP_BUF = 0.004
HORIZON_H = 72.0
DEDUP_H = 12.0
SLIP = 0.003
EXCLUDE_WEEK = "2025-W41"
FIX_MS = (2.0, 5.0)
TRAIL_PS = (0.20,)
WIDES = (0.30, 0.50)                     # wide fixed stops = entry*(1+W)
NOMINAL = 0.10                            # HOLD raw short return scaled to a 10% risk unit
FAMILIES = ([f"FIX_{m}" for m in FIX_MS] + [f"TRAIL_{int(p*100)}" for p in TRAIL_PS]
            + ["HOLD"] + [f"WIDE{int(w*100)}" for w in WIDES])


def _exits_short(eb, entry_raw, stop, hi, lo, cl, n, we, slip):
    """SHORT: sell at entry_raw*(1-slip); cover at level*(1+slip). Tight-stop families use the
    recent-high stop; HOLD has NO stop (exit at horizon close); WIDE uses stop=entry*(1+W)."""
    entry = entry_raw * (1 - slip); risk = stop - entry
    if risk <= 0:
        return None
    r = {}
    for m in FIX_MS:
        target = entry - m * risk; out = None
        for j in range(eb, we + 1):
            if hi[j] >= stop:
                out = (entry - stop * (1 + slip)) / risk; break
            if lo[j] <= target:
                out = (entry - target * (1 + slip)) / risk; break
        r[f"FIX_{m}"] = float(out if out is not None else (entry - cl[we] * (1 + slip)) / risk)
    for p in TRAIL_PS:
        run_min = entry; out = None
        for j in range(eb, we + 1):
            run_min = min(run_min, lo[j]); eff = min(stop, run_min * (1 + p))
            if hi[j] >= eff:
                out = (entry - eff * (1 + slip)) / risk; break
        r[f"TRAIL_{int(p*100)}"] = float(out if out is not None else (entry - cl[we] * (1 + slip)) / risk)
    # HOLD: no stop, exit at horizon close -> raw short return / NOMINAL (isolates DIRECTION)
    r["HOLD"] = float((entry - cl[we] * (1 + slip)) / (NOMINAL * entry))
    # WIDE fixed stops: stop = entry*(1+W), trail 30% underneath
    for w in WIDES:
        wstop = entry * (1 + w); wrisk = wstop - entry; run_min = entry; out = None
        for j in range(eb, we + 1):
            run_min = min(run_min, lo[j]); eff = min(wstop, run_min * (1 + 0.30))
            if hi[j] >= eff:
                out = (entry - eff * (1 + slip)) / wrisk; break
        r[f"WIDE{int(w*100)}"] = float(out if out is not None else (entry - cl[we] * (1 + slip)) / wrisk)
    return r


def build_symbol(path, tf, exclude_week):
    try:
        raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                             "quote_volume", "trade_count", "taker_buy_quote_volume",
                                             "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    except (OSError, ValueError, KeyError):
        return []
    df = _resample(raw, tf)
    ts = df["timestamp"].to_numpy(np.int64); op = df["open"].to_numpy(float)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    n = len(ts); sym = path.stem
    kbrk = max(2, round(BREAK_LOOK_MIN / tf)); kstop = max(2, round(STOP_LOOK_MIN / tf))
    horizon = max(8, round(HORIZON_H * 60 / tf)); dedup = round(DEDUP_H * 60 / tf)
    # break below the recent low (fresh downside break, ANY prior structure incl pump->dump);
    # stop above the recent high.
    roll_lo_k = pd.Series(lo).rolling(kbrk).min().shift(1).to_numpy()
    roll_hi_s = pd.Series(hi).rolling(kstop).max().shift(1).to_numpy()
    rng_state = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    out = []; last = -10**9
    stop_lim = int(np.searchsorted(ts, DEV_END))
    for j in range(kstop + 1, min(stop_lim, n - 2)):
        rl, rh = roll_lo_k[j], roll_hi_s[j]
        if not (np.isfinite(rl) and np.isfinite(rh) and rl > 0):
            continue
        if cl[j] < rl * (1 - BREAK_MARGIN) and cl[j] < op[j] and taker[j] < TAKER_MAX:
            if j - last < dedup:
                continue
            last = j
            eb = j + 1; entry0 = float(op[eb]); stop = rh * (1 + STOP_BUF)
            if stop <= entry0:
                continue
            we = min(eb + horizon, n - 1)
            r = _exits_short(eb, entry0, stop, hi, lo, cl, n, we, SLIP)
            if r is None:
                continue
            wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
            iscrash = int(wk == exclude_week)
            for fam, R in r.items():
                out.append({"symbol": sym, "tf": tf, "family": fam, "week": wk, "is_crash": iscrash,
                            "R": R, "kind": "real"})
            # matched random-short placebo (same risk% and reward geometry via same families)
            rf = (stop - entry0 * (1 - SLIP)) / (entry0 * (1 - SLIP))
            if dev_hi > 250 and 0 < rf < 0.5:
                cb = int(rng_state.integers(kstop + 2, dev_hi)); pe = float(op[cb + 1]); pstop = pe * (1 + rf)
                pr = _exits_short(cb + 1, pe, pstop, hi, lo, cl, n, min(cb + 1 + horizon, n - 1), SLIP)
                if pr is not None:
                    pwk = pd.Timestamp(int(ts[cb + 1]), unit="ms", tz="UTC").strftime("%G-W%V")
                    for fam, R in pr.items():
                        out.append({"symbol": sym, "tf": tf, "family": fam, "week": pwk, "is_crash": 0,
                                    "R": R, "kind": "placebo"})
    return out


def _one(a):
    f, tf, xw = a
    try:
        return build_symbol(Path(f), tf, xw)
    except Exception:  # noqa: BLE001
        return []


def _ttn(R):
    R = np.asarray(R, float)
    if R.sum() <= 0:
        return 0.0
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    return k / len(R) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, action="append", default=None)
    ap.add_argument("--exclude-week", default=EXCLUDE_WEEK)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    tfs = tuple(args.tf) if args.tf else TFS
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"EARLY-SHORT dump tfs={tfs} break<{BREAK_LOOK_MIN}m-low margin>{BREAK_MARGIN:.0%} taker<{TAKER_MAX} "
          f"stop>{STOP_LOOK_MIN}m-high slip={SLIP:.1%} excl={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.exclude_week) for tf in tfs for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)}")
    t = pd.DataFrame(rows)
    t.to_parquet(".output/results/knife_catch/dump_short.parquet")
    real = t[t.kind == "real"]; plc = t[t.kind == "placebo"]
    for tf in tfs:
        rr = real[(real.tf == tf) & (real.is_crash == 0)]; pp = plc[plc.tf == tf]
        if not len(rr):
            continue
        nb = rr[rr.family == FAMILIES[0]]
        print(f"\n=== TF={tf}m  early-short  (excl {args.exclude_week})  n_trades={len(nb)} ===")
        print(f"  {'family':<10} {'meanR':>7} {'medR':>7} {'win%':>6} {'placebo':>8} {'EDGE':>7} {'top%toNeg':>10} {'posWk%':>7}")
        for fam in FAMILIES:
            g = rr[rr.family == fam]; p = pp[pp.family == fam]
            if len(g) < 40:
                continue
            wk = g.groupby("week").R.mean(); pm = p.R.mean() if len(p) >= 30 else float("nan")
            print(f"  {fam:<10} {g.R.mean():>+7.3f} {g.R.median():>+7.3f} {(g.R>0).mean()*100:>5.1f}% "
                  f"{pm:>+8.3f} {g.R.mean()-pm:>+7.3f} {_ttn(g.R.to_numpy()):>9.1f}% {(wk>0).mean()*100:>6.0f}%")
    print("\n(want EDGE>0, top%toNeg>=40, posWk>50 across a plateau -- and remember survivorship UNDERstates a short.)")


if __name__ == "__main__":
    main()
