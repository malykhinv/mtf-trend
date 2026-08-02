"""Fixed-RR CatBoost edge extraction on dump breakdowns (SHORT), risk >= 1%.

The HOLD short is a coinflip (win~50%, median 0). Fix the payoff geometry so win-rate = EV,
then let CatBoost select the predictable subset. For each breakdown entry we place, per
(risk in {1%,2%}, RR in {1,2,3}), a SHORT with stop=entry*(1+risk), target=entry*(1-RR*risk);
label y=target-first. Causal features at the signal bar. Per (risk,RR): CatBoost GroupKFold-
by-week AUC vs a within-week shuffled null; the confident decile's win% (vs breakeven
1/(1+RR)), meanR, top%toNeg, posWk; plus the base rate and a matched random-short placebo.
Excl the crash week. DEV only. A real edge = confident-decile meanR>0, top%toNeg>=40,
posWk>50, AUC>>null, across >=1 (risk,RR) cell -- ideally a plateau of adjacent cells.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (5, 10)
BREAK_LOOK_MIN = 60
TAKER_MAX = 0.45
BREAK_MARGIN = 0.01
HORIZON_H = 24.0
DEDUP_H = 8.0
SLIP = 0.003
EXCLUDE_WEEK = "2025-W41"
RISKS = (0.02, 0.03, 0.05)
RRS = (1.0, 2.0, 3.0)
COMBOS = [(r, rr) for r in RISKS for rr in RRS]
SIDE = "long"                              # 'long' fades the breakdown (bounce); 'short' rides it


def _cost_R(risk_frac):
    return 2 * SLIP / risk_frac            # round-trip slippage, in R units


def _ema(x, s):
    a = 2.0 / (s + 1.0); o = np.empty_like(x, float); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def _short_R(eb, entry, risk_frac, rr, hi, lo, cl, n, horizon):
    """SHORT fixed-RR: stop=entry*(1+risk), target=entry*(1-rr*risk). y=target-first."""
    stop = entry * (1 + risk_frac); target = entry * (1 - rr * risk_frac)
    we = min(eb + horizon, n - 1)
    for k in range(eb, we + 1):
        if hi[k] >= stop:
            return 0, -1.0
        if lo[k] <= target:
            return 1, float(rr)
    r = (entry - cl[we]) / (entry * risk_frac)             # time-stop, in R
    return (1 if cl[we] <= target else 0), float(r)


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
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    cvd = np.cumsum((2 * taker - 1) * qv); atr = causal_atr(high=hi, low=lo, close=cl, window=64)
    ema = _ema(cl, max(30, round(1200 / tf))); base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    n = len(ts); sym = path.stem
    kbrk = max(2, round(BREAK_LOOK_MIN / tf)); horizon = max(8, round(HORIZON_H * 60 / tf))
    dedup = round(DEDUP_H * 60 / tf); oi_lb = max(1, round(5 / tf))
    roll_lo_k = pd.Series(lo).rolling(kbrk).min().shift(1).to_numpy()
    k1h = max(2, round(60 / tf)); k4h = max(4, round(240 / tf))
    rng_state = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    stop_lim = int(np.searchsorted(ts, DEV_END))
    out = []; last = -10**9
    for j in range(k4h + 1, min(stop_lim, n - 2)):
        rl = roll_lo_k[j]
        if not (np.isfinite(rl) and rl > 0):
            continue
        if not (cl[j] < rl * (1 - BREAK_MARGIN) and cl[j] < op[j] and taker[j] < TAKER_MAX):
            continue
        if j - last < dedup:
            continue
        last = j; eb = j + 1; entry = float(op[eb]); rng = hi[j] - lo[j] + 1e-12
        wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
        row = {"symbol": sym, "tf": tf, "week": wk, "is_crash": int(wk == exclude_week),
               "f_break_depth": (rl - cl[j]) / rl, "f_taker": float(taker[j]),
               "f_taker_turn": float(taker[j] - np.nanmean(taker[max(0, j - 6):j + 1])),
               "f_mom_1h": cl[j] / (cl[max(0, j - k1h)] + 1e-9) - 1.0,
               "f_mom_4h": cl[j] / (cl[max(0, j - k4h)] + 1e-9) - 1.0,
               "f_oi_change": oi[j] / (oi[max(0, j - oi_lb)] + 1e-9) - 1.0,
               "f_oi_slope": oi[j] / (oi[max(0, j - 2 * oi_lb)] + 1e-9) - 1.0,
               "f_cvd_slope": float((cvd[j] - cvd[max(0, j - 6)]) / (abs(cvd[j]) + 1e-9)),
               "f_vol_spike": float(qv[j] / (np.nanmedian(base_qv[max(0, j - 50):j + 1]) + 1e-9)),
               "f_body": float((op[j] - cl[j]) / rng), "f_lwick": float((cl[j] - lo[j]) / rng),
               "f_uwick": float((hi[j] - op[j]) / rng), "f_atr_pct": float(atr[j] / cl[j]),
               "f_price_vs_ema": float(cl[j] / (ema[j] + 1e-12) - 1.0),
               "f_hour": int((ts[j] % 86_400_000) // 3_600_000)}
        entry_s = entry * (1 - SLIP)
        for (rk, rr) in COMBOS:
            y, R = _short_R(eb, entry_s, rk, rr, hi, lo, cl, n, horizon)
            row[f"y_{int(rk*100)}_{int(rr)}"] = y; row[f"R_{int(rk*100)}_{int(rr)}"] = R
        # matched random-short placebo base rates (per combo)
        if dev_hi > 250:
            cb = int(rng_state.integers(k4h + 2, dev_hi)); pe = float(op[cb + 1]) * (1 - SLIP)
            for (rk, rr) in COMBOS:
                py, pR = _short_R(cb + 1, pe, rk, rr, hi, lo, cl, n, horizon)
                row[f"pR_{int(rk*100)}_{int(rr)}"] = pR
        out.append(row)
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
    ap.add_argument("--exclude-week", default=EXCLUDE_WEEK)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"RR-model SHORT tfs={TFS} risks={RISKS} RRs={RRS} break<{BREAK_LOOK_MIN}m-low taker<{TAKER_MAX} "
          f"horizon={HORIZON_H:.0f}h excl={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.exclude_week) for tf in TFS for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)}")
    t = pd.DataFrame(rows)
    t.to_parquet(".output/results/knife_catch/dump_rr_model.parquet")
    feats = [c for c in t.columns if c.startswith("f_")]
    print(f"\nentries={len(t)}  (excl crash below)")
    print(f"  {'combo':<12} {'base_win':>8} {'breakeven':>9} {'placeboR':>8} | {'AUC':>5} {'null':>5} | "
          f"{'top10 win':>9} {'meanR':>7} {'top%toNeg':>10} {'posWk%':>7}")
    for tf in TFS:
        for (rk, rr) in COMBOS:
            yc = f"y_{int(rk*100)}_{int(rr)}"; Rc = f"R_{int(rk*100)}_{int(rr)}"; pc = f"pR_{int(rk*100)}_{int(rr)}"
            d = t[(t.tf == tf) & (t.is_crash == 0)].dropna(subset=feats + [yc])
            if len(d) < 300:
                continue
            X = d[feats].to_numpy(float); y = d[yc].to_numpy(int); wk = d.week.to_numpy()
            med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])

            def run(yv):
                o = np.full(len(yv), np.nan)
                for a_, b_ in GroupKFold(5).split(X, yv, wk):
                    m = CatBoostClassifier(depth=4, iterations=350, learning_rate=0.03, l2_leaf_reg=6,
                                           verbose=False, random_seed=0)
                    m.fit(X[a_], yv[a_]); o[b_] = m.predict_proba(X[b_])[:, 1]
                return o
            oof = run(y)
            rng = np.random.default_rng(0)
            yn = d.groupby("week")[yc].transform(lambda s: rng.permutation(s.values)).to_numpy(int)
            oofn = run(yn)
            mk = np.isfinite(oof) & np.isfinite(oofn)
            auc = roc_auc_score(y[mk], oof[mk]); nauc = roc_auc_score(yn[mk], oofn[mk])
            s = d[mk].assign(p=oof[mk])
            top = s[s.p >= s.p.quantile(0.9)]
            wk2 = top.groupby("week")[Rc].mean()
            base = d[yc].mean(); be = 1 / (1 + rr); pm = d[pc].mean() if pc in d else float("nan")
            print(f"  tf{tf} r{int(rk*100)}%RR{int(rr)}  {base*100:>7.1f}% {be*100:>8.1f}% {pm:>+8.3f} | "
                  f"{auc:>5.3f} {nauc:>5.3f} | {top[yc].mean()*100:>8.1f}% {top[Rc].mean():>+7.3f} "
                  f"{_ttn(top[Rc].to_numpy()):>9.1f}% {(wk2>0).mean()*100:>6.0f}%")
    print("\n(REAL edge: AUC>>null AND top-decile meanR>0 with top%toNeg>=40 & posWk>50, ideally a plateau.)")


if __name__ == "__main__":
    main()
