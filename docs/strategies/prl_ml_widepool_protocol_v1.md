# PRL-ML-000 — wide residual pool ranker + leak audit + trader summary

**Strategy:** `prl_daily_v1` (tier: coarse) · **Spec:** PRL_STRATEGY_SPEC.md v1.2.0 (§11, §38, §39, §56/§71, §45-§49)
**Code:** `prl/research/feature_pool.py`, `catboost_rank.py`, `leakage_audit.py`, `trader_summary.py`
**Tests:** `tests/test_prl_{coarse,quality,trader}.py` (15 pass) · **Status:** IS-positive, leak-clean. **OOS reserved, unread.**

---

## 1. Goal

Squeeze the maximum IS predictive signal from a broad causal residual feature pool via
a nonlinear ranker, then subject any attractive result to the strict leak battery and a
full trader/portfolio summary — before deciding to spend the OOS holdout.

## 2. Feature pool (39 features, all per-date cross-sectional percentiles, §54)

Residual momentum {7,14,28,60}d, residual vol/sharpe, distance-from-residual-extreme;
quality/persistence {14,28,56}d (path_eff, frac_pos, burst); raw momentum/stretch/dist;
activity (quote-vol & trade-count shares + accel, avg trade size, vol_accel); taker flow
(buy ratio, signed-flow z, flow persistence, cumulative imbalance). Target = per-date rank
of the frozen-beta 5d future residual return. Purged/embargoed walk-forward, group = date.

## 3. Ranker result (IS, 5 folds, 100k rows)

```text
CatBoost OOF rank-IC = +0.074 (t=14.1)
Ridge    OOF rank-IC = +0.089 (t=15.9)   <- linear BEATS CatBoost: signal is additive
shuffled OOF rank-IC = +0.000 (t=0.1)    <- clean
delay+1d OOF rank-IC = +0.073            <- not latency-fragile
CatBoost median decile net spread = +1.40%/hold  (top +0.96%, bot -0.73%)
```

Top univariate IC (leak-sniff, none implausibly high): rvol_28 −0.081, rvol_14 −0.069,
dvol_20 −0.063 (**low-vol → higher future residual**), avg_trade_size +0.059, frac_pos_56
+0.058, resid_from_high_28 +0.048, flow_persist_28 +0.042 (**taker flow carries signal**),
burst −0.032. Top CatBoost importances: flow_persist_28, frac_pos_56, dvol_20, burst_56,
rmom_60. The signal is a broad, additive blend — no single feature dominates.

## 4. Leak audit (§56, §71) — PASSED

```text
positive control (inject future label as feature): IC 0.097 -> 0.998  (harness detects leaks)
embargo 0/7/14d: IC 0.098 / 0.097 / 0.096  (span 0.0025, flat -> no overlap inflation)
feature staleness +3d: IC 0.093 (graceful decay, not pinned)
shuffled target: ~0 (from ranker)
```

## 5. Trader summary (open-to-open, costed, regime/concentration/risk)

**Long-short top/bottom decile at the native 5d horizon** (the tradeable form of a residual
rank signal, §44):

```text
winrate 0.51  net median +0.19%  net mean +0.36%/hold
weeks>0 0.64   months>0 0.64                      <- time-stable, breaks the lottery mode
regime net: bull +0.37%  bear +0.25%  sideways +0.47%   <- all-regime positive
concentration: top1 sym 3%, top5 13%, top10 21% of positive PnL   <- diversified
portfolio Sharpe 0.74 (delay1) / 0.98 (delay2), CAGR +7..20% at 2-5% risk/name,
  maxDD -20..45%
```

**Failure modes found (honest):**
- **Long-only** top-decile is NOT tradeable: winrate 0.45, negative median, loses in sideways,
  ~0.3% of trades carry all profit. The rank only monetizes as a market-neutral long-short.
- **Horizon-locked:** at 10d/20d holds the long-short goes negative. The book lives at the 5d
  label horizon it was fit on.
- **$ P&L is year-concentrated + cost-fragile** (the decisive check): the naive decile long-short
  makes **all** its money in **2025** and LOSES in 2023 (−5%) and 2024 (−13%); at 3× costs the
  median trade is already negative.

**Signal vs monetization — the key nuance:** the per-year OOF **rank-IC is positive and
significant every year** (2023 +0.045 t3.4, 2024 +0.060 t8.3, 2025 +0.131 t14.0). So the
predictive relationship is real and year-robust; what is NOT robust is the naive decile book's
**monetization** (§32.3 IC ≠ R). The likely culprit is the dominant low-vol tilt: low-vol wins
in chop/bear (2025) and loses in the momentum bull (2024). This is a portfolio-construction
problem (§45), not a fake signal.

## 6. Verdict

**IS: predictive signal REAL & year-robust; naive tradeable book NOT robust (2025-concentrated,
cost-fragile).** The wide residual pool has a genuine additive rank-IC (~0.09, positive every
year, leak-clean), driven by low-vol + persistence + taker-flow-persistence + avg-trade-size +
anti-burst. But the naive decile long-short does not convert it to robust P&L: profit is
concentrated in 2025, negative in 2023-2024, cost-fragile, long-only-dead, horizon-locked. This
is a §32.3 predictive-vs-tradeable gap. NOT a candidate for OOS until the monetization is made
year-robust. OOS remains reserved.

## 7. Next (each a pre-registered trial; OOS still reserved)

1. **Fix monetization first (§45 portfolio construction)** — the signal is real; the naive decile
   book is not. Try: volatility-neutralize the score (remove the low-vol tilt so it is not just a
   low-vol bet), rank-weighted vs decile long-short, per-cluster/sector-neutral construction,
   IC-consistent sizing. Target: positive $ in each of 2023/2024/2025, not only 2025.
2. Consolidate a **frozen transparent additive score** (low researcher DOF) reproducing the linear
   blend, once a year-robust book exists.
3. **PRL-PEER-000** — does peer-residualization (Epps-aware clustering) add incremental IC?
4. Turnover/hysteresis + capacity/participation stress; cost-stress must keep the median positive.
5. Only after a year-robust, cost-robust book is frozen: single OOS 2026-H1, then locked holdout.
