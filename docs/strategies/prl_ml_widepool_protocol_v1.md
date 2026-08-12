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
  ~0.3% of trades carry all profit. The rank-IC only monetizes as a market-neutral long-short.
- **Horizon-locked:** at 10d/20d holds the long-short goes negative (bear −3.5%). The edge lives
  at the 5d label horizon it was fit on.
- Aggregate edge is thin (remove top ~0.7% of trades → total net ≤ 0).

## 6. Verdict

**IS-POSITIVE, leak-clean, modest, market-neutral.** The wide residual pool yields a real,
additive, regime-stable, diversified long-short edge at the 5d horizon (Sharpe ~0.9 IS), driven
by low-vol + persistence + taker-flow-persistence + avg-trade-size + anti-burst. It survives the
strict leak battery. It is modest and horizon-specific, and does NOT work long-only. This is IS
development (§95: robust + regime + placebo + leak-audit + beats baselines); NOT yet OOS/holdout.

## 7. Next (each a pre-registered trial; OOS still reserved)

1. **PRL-PEER-000** — does peer-residualization (Epps-aware clustering) add incremental IC?
2. Consolidate a **frozen simple transparent score** (the additive blend the linear model found)
   to minimize researcher DOF before freezing, per the project's low-DOF preference.
3. Turnover/hysteresis + capacity/participation stress on the 5d long-short.
4. Only after the full spec is frozen: single OOS 2026-H1 validation, then locked holdout.
