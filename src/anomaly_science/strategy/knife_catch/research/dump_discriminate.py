"""WHAT separates a WIN (buy-back to 50%-retrace) from a LOSS (new low) on the knife-catch
signals, and does WAITING for confirmation (enter later, higher conviction) help?

Entry: break_long / aggression on 10-15m after a >= MIN_DROP fall (excl crash week).
Label: WIN = price reaches bottom + 0.5*(T-bottom) before a new low; LOSS = new low first.
Part A -- DISCRIMINATION: causal features at the signal bar -> CatBoost win-vs-loss with
GroupKFold-by-week, a within-week shuffled null, top/bottom-decile win rates, and univariate
separation (which features push WIN vs LOSS).
Part B -- CONFIRMATION delay: instead of entering at the signal, wait until price has already
retraced CONF*(T-bottom) (bounce underway) with no new low first; sweep CONF and read win%/R.
DEV only. Slippage-free label (win/loss is level-based); R uses stop=bottom, target=50%.
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
from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.knife_catch.research.dump_signals import _first_entry
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (10, 15)
REASONS = ("break_long", "aggression")
MIN_DROP = 0.20
SCAN_H = 8.0
HORIZON_H = 72.0
RETRACE = 0.50
CONFS = (0.0, 0.10, 0.20, 0.30, 0.40)


def _ema(x, s):
    a = 2.0 / (s + 1.0); o = np.empty_like(x, float); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def _winloss(start_bar, bottom, target, hi, lo, n, horizon):
    we = min(start_bar + horizon, n - 1)
    for k in range(start_bar, we + 1):
        if lo[k] < bottom:
            return 0, k
        if hi[k] >= target:
            return 1, k
    return -1, we


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
    cvd = np.cumsum((2 * taker - 1) * qv); atr = causal_atr(high=hi, low=lo, close=cl, window=64)
    ema240 = _ema(cl, max(30, round(1200 / tf))); base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    rng_bar = hi - lo
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / tf)); horizon = max(8, round(HORIZON_H * 60 / tf)); oi_lb = max(1, round(5 / tf))
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        T = float(d.pump_start_price)
        for reason in REASONS:
            eb, bottom = _first_entry(reason, t, b, hi, lo, cl, op, taker, oi, qv, base_qv,
                                      min_drop, scan, oi_lb, tf, n)
            if eb is None or eb >= n - 2:
                continue
            j = eb - 1
            wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
            is_crash = int(wk == exclude_week)
            target = bottom + RETRACE * (T - bottom)
            # Part B: confirmation-delay outcomes (enter after price retraces CONF toward target)
            conf_y = {}
            for conf in CONFS:
                lvl = bottom + conf * (T - bottom)
                start = eb if conf == 0 else None
                if conf > 0:                                  # find first bar (after signal) reaching lvl, no new low first
                    for k in range(eb, min(eb + horizon, n - 1)):
                        if lo[k] < bottom:
                            break
                        if hi[k] >= lvl:
                            start = k + 1; break
                if start is None or start >= n - 1:
                    conf_y[conf] = np.nan; continue
                y, _ = _winloss(start, bottom, target, hi, lo, n, horizon)
                conf_y[conf] = y if y >= 0 else np.nan
            # Part A: features at the signal bar j (causal) + immediate win/loss + R
            y0, xb = _winloss(eb, bottom, target, hi, lo, n, horizon)
            if y0 < 0:
                continue
            entry_s = op[eb] * (1 + 0.003); stop_s = bottom * (1 - 0.004); risk_s = entry_s - stop_s
            if risk_s <= 0:
                continue
            R = (target * (1 - 0.003) - entry_s) / risk_s if y0 == 1 else (stop_s * (1 - 0.003) - entry_s) / risk_s
            back = max(t, j - 6); sw_lo = float(lo[max(0, t - 40):t + 1].min())
            row = {
                "symbol": sym, "tf": tf, "reason": reason, "week": wk, "is_crash": is_crash, "y": int(y0),
                "R": float(R), "entry_ts": int(ts[eb]), "exit_ts": int(ts[xb]),
                "f_drop": (T - bottom) / T, "f_bars_t_to_sig": j - t,
                "f_ret_since_bottom": cl[j] / bottom - 1.0,
                "f_rr_geom": (target - op[eb]) / (op[eb] - bottom + 1e-12),
                "f_taker_sig": float(taker[j]),
                "f_taker_turn": float(taker[j] - np.nanmean(taker[t:j + 1])),
                "f_cvd_recover": float((cvd[j] - np.min(cvd[t:j + 1])) / (abs(cvd[t]) + 1e-9)),
                "f_oi_change_dump": oi[j] / (oi[t] + 1e-9) - 1.0,
                "f_oi_slope": oi[j] / (oi[max(0, j - 2 * oi_lb)] + 1e-9) - 1.0,
                "f_lwick_sig": float((min(op[j], cl[j]) - lo[j]) / (rng_bar[j] + 1e-12)),
                "f_body_sig": float(abs(cl[j] - op[j]) / (rng_bar[j] + 1e-12)),
                "f_closepos_sig": float((cl[j] - lo[j]) / (rng_bar[j] + 1e-12)),
                "f_vol_sig": float(qv[j] / (np.nanmedian(base_qv[back:j + 1]) + 1e-9)),
                "f_price_vs_ema": float(cl[j] / (ema240[j] + 1e-12) - 1.0),
                "f_dist_below_swinglow_atr": float((sw_lo - bottom) / (atr[t] + 1e-12)),
                "f_bounce_vel": float(cl[j] / (cl[max(t, j - 3)] + 1e-9) - 1.0),
                "f_green_since_bottom": float((cl[back:j + 1] > op[back:j + 1]).mean()),
                "f_atr_pct": float(atr[t] / T),
            }
            for conf in CONFS:
                row[f"cy_{int(conf*100)}"] = conf_y[conf]
            out.append(row)
    return out


def _one(a):
    f, tf, md, xw = a
    try:
        return build_symbol(Path(f), tf, md, xw)
    except Exception:  # noqa: BLE001
        return []


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
    print(f"discriminate tfs={TFS} reasons={REASONS} drop>{args.min_drop:.0%} crash={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.min_drop, args.exclude_week) for tf in TFS for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)}")
    t = pd.DataFrame(rows)
    t.to_parquet(".output/results/knife_catch/dump_discriminate.parquet")
    d = t[t.is_crash == 0].copy()
    print(f"\nn(excl crash)={len(d)}  base WIN rate={d.y.mean():.3f}")

    # Part B: confirmation-delay sweep
    print("\n=== CONFIRMATION delay (enter after price retraces CONF toward target) ===")
    print(f"  {'CONF':>5} {'n':>6} {'win%':>6} {'posWk%':>7}")
    for conf in CONFS:
        col = f"cy_{int(conf*100)}"; g = d[np.isfinite(d[col])]
        wk = g.groupby("week")[col].mean()
        print(f"  {conf*100:>4.0f}% {len(g):>6} {g[col].mean()*100:>5.1f}% {(wk>0.5).mean()*100:>6.0f}%")

    # Part A: discrimination
    feats = [c for c in d.columns if c.startswith("f_")]
    d2 = d.dropna(subset=feats)
    X = d2[feats].to_numpy(float); y = d2.y.to_numpy(int); wk = d2.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])

    def run(yv):
        o = np.full(len(yv), np.nan)
        for a_, b_ in GroupKFold(5).split(X, yv, wk):
            m = CatBoostClassifier(depth=4, iterations=400, learning_rate=0.03, l2_leaf_reg=6,
                                   verbose=False, random_seed=0)
            m.fit(X[a_], yv[a_]); o[b_] = m.predict_proba(X[b_])[:, 1]
        return o
    oof = run(y)
    rng = np.random.default_rng(0)
    yn = d2.groupby("week").y.transform(lambda s: rng.permutation(s.values)).to_numpy(int)
    oofn = run(yn)
    mk = np.isfinite(oof) & np.isfinite(oofn)
    print(f"\n=== WIN-vs-LOSS discrimination: week-CV AUC={roc_auc_score(y[mk], oof[mk]):.3f} "
          f"vs shuffled null={roc_auc_score(yn[mk], oofn[mk]):.3f}  (base win {y.mean():.3f}) ===")
    s = d2[mk].assign(p=oof[mk])
    for q, lbl in [(0.9, "top10% conf"), (0.8, "top20%"), (0.7, "top30%"), (0.3, "bot30%"), (0.1, "bot10%")]:
        sel = s[s.p >= s.p.quantile(q)] if q >= 0.5 else s[s.p <= s.p.quantile(q)]
        wk2 = sel.groupby("week").y.mean()
        print(f"  {lbl:<12} n={len(sel):>5} win={sel.y.mean():.3f} posWk={(wk2>0.5).mean()*100:.0f}%")
    imp = pd.Series(CatBoostClassifier(depth=4, iterations=400, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(np.nan_to_num(d2[feats].to_numpy(float)), y).get_feature_importance(),
                    index=feats).sort_values(ascending=False)
    print("  top features:", ", ".join(f"{k[2:]}={v:.1f}" for k, v in imp.head(8).items()))

    # DECISIVE: does model selection convert to robust R? (R already excludes the tautology-free
    # question -- we just rank by p and read realized R). top%toNeg>=40 across a plateau = real.
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
    print("\n=== model-selected entries -> realized R (stop=bottom, target=50%, slip 0.3%) ===")
    print(f"  {'select':<10} {'n':>5} {'meanR':>7} {'win%':>6} {'top%toNeg':>10} {'posWk%':>7}")
    for q, lbl in [(0.0, "ALL"), (0.5, "top50%"), (0.7, "top30%"), (0.8, "top20%"), (0.9, "top10%")]:
        sel = s[s.p >= s.p.quantile(q)]
        wk2 = sel.groupby("week").R.mean()
        print(f"  {lbl:<10} {len(sel):>5} {sel.R.mean():>+7.3f} {(sel.R>0).mean()*100:>5.1f}% "
              f"{_ttn(sel.R.to_numpy()):>9.1f}% {(wk2>0).mean()*100:>6.0f}%")
    print("\n(if even the model's top decile has top%toNeg<40 or meanR~0, the win-vs-loss AUC does NOT "
          "convert to a robust R edge -- high win-rate on a low-reward label.)")
    # univariate direction: mean feature in WIN vs LOSS (standardized diff)
    print("\n  univariate WIN-vs-LOSS (std-diff, + => higher in wins):")
    diffs = {}
    for c in feats:
        w = d2[d2.y == 1][c]; l = d2[d2.y == 0][c]; sd = d2[c].std() + 1e-9
        diffs[c] = (w.mean() - l.mean()) / sd
    for c, v in sorted(diffs.items(), key=lambda x: -abs(x[1]))[:8]:
        print(f"    {c[2:]:<22} {v:+.2f}")
    print("\n(edge only if: AUC>>null & week-stable confident decile; and/or a CONF level lifts win% clearly.)")


if __name__ == "__main__":
    main()
