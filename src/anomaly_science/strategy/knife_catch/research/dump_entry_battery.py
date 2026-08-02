"""Simulate a BATTERY of diverse entry-strengthening variants for the knife-catch and rank
them by robust, drift-controlled expectancy (excl crash week).

Each variant is a stronger causal confirmation at the reversal bar (2 green candles, close
above the break, aggression + rising OI, strong close, volume-climax rejection, EMA reclaim,
CVD up, ...). For every dump (drop>MIN_DROP) each variant enters at the FIRST bar its
condition holds; outcome = WIN (buy-back to 50%-retrace) vs LOSS (new low), also in R
(stop=bottom, target=50%). Reported per variant (excl crash): n, win%, meanR, EDGE over a
matched random-long placebo, top%toNeg (target>=40), posWk, and the biggest-week share.
DEV only. Slippage-aware R.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (10, 15)
MIN_DROP = 0.20
SCAN_H = 8.0
HORIZON_H = 72.0
RETRACE = 0.50
STOP_BUF = 0.004
SLIP = 0.003

VARIANTS = (
    "flow", "break", "aggression",
    "break_2green", "break_margin", "break_flow",
    "agg_oiup", "agg_oiup_strong", "agg_closetop",
    "flow_volx", "flow_lwick", "flow_cvdup",
    "strongclose_oiup", "green2_taker", "taker_strong",
    "climax_wick", "reclaim_ema", "brk_oiup_strong",
    "agg_cvdup", "flow_strong",
)


def _ema(x, s):
    a = 2.0 / (s + 1.0); o = np.empty_like(x, float); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def _race(eb, entry0, stop, target, hi, lo, cl, n, horizon):
    entry = entry0 * (1 + SLIP); risk = entry - stop
    if risk <= 0:
        return None
    we = min(eb + horizon, n - 1)
    for k in range(eb, we + 1):
        if lo[k] <= stop:
            return (stop * (1 - SLIP) - entry) / risk
        if hi[k] >= target:
            return (target * (1 - SLIP) - entry) / risk
    return (cl[we] * (1 - SLIP) - entry) / risk


def build_symbol(path, tf, min_drop, exclude_week):
    try:
        dumps = [d for d in _scan_symbol(path, tf) if d.culmination_ms < DEV_END and d.drop_pct > min_drop]
    except (OSError, ValueError, KeyError):
        return []
    if not dumps:
        return []
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf)
    ts = df["timestamp"].to_numpy(np.int64); op = df["open"].to_numpy(float)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    cvd = np.cumsum((2 * taker - 1) * qv)
    base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    ema20 = _ema(cl, 20)
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / tf)); horizon = max(8, round(HORIZON_H * 60 / tf))
    oi_lb = max(1, round(5 / tf)); kbrk = max(2, round(30 / tf))
    rng_state = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        T = float(d.pump_start_price); run_min = float(lo[t]); tk_sum = 0.0; nb = 0
        fired = {}
        for j in range(t + 1, min(b + scan, n - 2)):
            run_min = min(run_min, lo[j]); tk_sum += taker[j]; nb += 1
            if (T - run_min) / T < min_drop:
                continue
            rng = hi[j] - lo[j] + 1e-12
            green = cl[j] > op[j]; green2 = green and cl[j - 1] > op[j - 1]
            tk = taker[j]; agg = (tk - tk_sum / max(nb, 1)) > 0.15
            oiup = oi[j] >= oi[max(0, j - oi_lb)]; oiup_s = oi[j] > oi[max(0, j - oi_lb)] * 1.001
            lwick = (min(op[j], cl[j]) - lo[j]) / rng; closepos = (cl[j] - lo[j]) / rng
            brk_lvl = float(hi[max(0, j - kbrk):j].max()); brk = cl[j] > brk_lvl
            volx = qv[j] / (np.nanmedian(base_qv[max(t, j - 50):j + 1]) + 1e-9)
            cvd_up = cvd[j] > cvd[max(t, j - 3)]; flow = green and tk > 0.5 and oiup
            cond = {
                "flow": flow, "break": brk and green, "aggression": agg and green,
                "break_2green": brk and green2, "break_margin": cl[j] > brk_lvl * 1.002 and green,
                "break_flow": brk and tk > 0.5 and oiup,
                "agg_oiup": agg and oiup, "agg_oiup_strong": agg and oiup_s,
                "agg_closetop": agg and closepos > 0.66,
                "flow_volx": flow and volx > 2, "flow_lwick": flow and lwick > 0.4,
                "flow_cvdup": flow and cvd_up,
                "strongclose_oiup": green and closepos > 0.66 and oiup,
                "green2_taker": green2 and tk > 0.5, "taker_strong": tk > 0.6 and green,
                "climax_wick": lwick > 0.5 and green and volx > 2,
                "reclaim_ema": cl[j] > ema20[j] and green, "brk_oiup_strong": brk and oiup_s,
                "agg_cvdup": agg and cvd_up,
                "flow_strong": green and tk > 0.6 and oiup_s and closepos > 0.6,
            }
            for v in VARIANTS:
                if v not in fired and cond[v]:
                    fired[v] = (j + 1, run_min)
            if len(fired) == len(VARIANTS):
                break
        for v, (eb, bottom) in fired.items():
            if eb >= n - 2:
                continue
            entry0 = float(op[eb]); stop = bottom * (1 - STOP_BUF); target = bottom + RETRACE * (T - bottom)
            if entry0 <= stop or target <= entry0:
                continue
            R = _race(eb, entry0, stop, target, hi, lo, cl, n, horizon)
            if R is None:
                continue
            wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
            out.append({"symbol": sym, "variant": v, "week": wk,
                        "is_crash": int(wk == exclude_week), "R": float(R), "kind": "real"})
            rf = (entry0 * (1 + SLIP) - stop) / (entry0 * (1 + SLIP)); rw = (target - entry0 * (1 + SLIP)) / (entry0 * (1 + SLIP))
            if dev_hi > 250 and 0 < rf < 0.95 and rw > 0:
                cb = int(rng_state.integers(200, dev_hi)); pe = float(op[cb + 1])
                pR = _race(cb + 1, pe, pe * (1 - rf), pe * (1 + rw), hi, lo, cl, n, horizon)
                if pR is not None:
                    out.append({"symbol": sym, "variant": v,
                                "week": pd.Timestamp(int(ts[cb + 1]), unit="ms", tz="UTC").strftime("%G-W%V"),
                                "is_crash": 0, "R": float(pR), "kind": "placebo"})
    return out


def _one(a):
    f, tf, md, xw = a
    try:
        return build_symbol(Path(f), tf, md, xw)
    except Exception:  # noqa: BLE001
        return []


def _top_pct_to_neg(R):
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
    ap.add_argument("--min-drop", type=float, default=MIN_DROP)
    ap.add_argument("--exclude-week", default="2025-W41")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"entry battery tfs={TFS} variants={len(VARIANTS)} drop>{args.min_drop:.0%} crash={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.min_drop, args.exclude_week) for tf in TFS for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)}")
    t = pd.DataFrame(rows)
    t.to_parquet(".output/results/knife_catch/dump_entry_battery.parquet")
    real = t[t.kind == "real"]; plc = t[t.kind == "placebo"]
    print(f"\nEntry variants ranked by EDGE (excl crash week {args.exclude_week}):")
    print(f"  {'variant':<18} {'n':>6} {'win%':>6} {'meanR':>7} {'placebo':>8} {'EDGE':>7} {'top%toNeg':>10} {'posWk%':>7} {'topWk%':>7}")
    res = []
    for v in VARIANTS:
        g = real[(real.variant == v) & (real.is_crash == 0)]; p = plc[plc.variant == v]
        if len(g) < 40:
            continue
        wk = g.groupby("week").R.mean(); pm = p.R.mean() if len(p) >= 30 else float("nan")
        tw = g.groupby("week").size(); topwk = tw.max() / len(g) * 100
        res.append((v, len(g), (g.R > 0).mean(), g.R.mean(), pm, g.R.mean() - pm,
                    _top_pct_to_neg(g.R.to_numpy()), (wk > 0).mean(), topwk))
    for v, n_, win, mr, pm, edge, ttn, pw, topwk in sorted(res, key=lambda x: -x[5]):
        print(f"  {v:<18} {n_:>6} {win*100:>5.1f}% {mr:>+7.3f} {pm:>+8.3f} {edge:>+7.3f} "
              f"{ttn:>9.1f}% {pw*100:>6.0f}% {topwk:>6.0f}%")
    print("\n(want: EDGE>0, top%toNeg>=40, posWk>50, topWk% low (distributed). Multiple such rows = plateaus.)")


if __name__ == "__main__":
    main()
