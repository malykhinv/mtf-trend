# PRL-COARSE-000 — coarse-tier existence gate (protocol + result)

**Strategy:** `prl_daily_v1` (tier: coarse) — Persistent Relative Leadership family
**Spec:** `C:/Users/Ascf/Documents/Trading/Relative leadership/PRL_STRATEGY_SPEC.md` v1.2.0 (§5.2.1, §32.1.1)
**Code:** `src/anomaly_science/strategy/prl/research/` · **Tests:** `tests/test_prl_coarse.py`
**Run:** `python -m anomaly_science.strategy.prl.research.run_coarse`
**Status:** IS measured (2023-01 → 2025-12). **OOS (≥ 2026-01-01) reserved, unread.**

---

## 1. Question (pre-registered)

Does multi-horizon **market-residual** momentum rank predict the **frozen-beta future
residual return** across the 2023–2026 multi-regime daily panel, after removing broad
market beta — with a stable OOS sign, above the pre-registered MDE, surviving a robust
(median) net-of-cost decile spread?

This is the §5.2 **coarse existence gate**: it must PASS before any fine-tier (1m)
execution work is justified. Strong negative prior going in: Han (SSRN 4675565) and the
prior [[xsect-momentum]] study both find crypto cross-sectional momentum ≈ absent.

## 2. Frozen specification (`PRLCoarsePolicy` primary)

```text
signal        : mean per-date percentile of residual cum-return over {7,14,28}d (skip 1)
target        : future market-residual return, horizon 5d, beta FROZEN as-of-t (§9.1)
market factor : trimmed-mean(10%) of eligible universe (robust ⇒ LOO objective, §7.1)
beta          : rolling 60d cov/var, clip[-2,4], shrink 0.3→1 (§7.2, §9.4)
universe      : top-100 trailing-median $vol, age≥60d, stable/wrapped excluded (§6)
primary metric: mean cross-sectional Spearman IC, dependence-aware block bootstrap (§32.2)
power/MDE     : non-overlapping reb-date IC, 80% power, α=0.05 one-sided (§32.4)
econ co-primary: MEDIAN top-minus-bottom decile net spread (§32.1.2, §65.1)
```

Leak discipline: signals use only rows ≤ t; the future label freezes beta at t; forward
labels near the IS boundary truncate to NaN (`min_periods == horizon`) so the reserved
OOS tail is never read (§6.3.2 embargo). Anti-leak invariants unit-tested.

## 3. Result — IS (663 symbols, median 100/day, 999 IC days)

| spec | IC mean (t) | boot 90% CI | MEDIAN decile net (t) | verdict |
|---|---|---|---|---|
| **fwd=5 (PRIMARY)** | −0.0008 (−0.14) | [−0.018, +0.021] | +0.12% (0.91) | **not-confirmed** |
| fwd=1 | −0.020 (−3.39) | [−0.030, −0.010] | −0.47% (−2.10) | 1-day reversal |
| fwd=10 | +0.020 (+3.51) | [−0.001, +0.044] | +0.93% (1.54) | not-confirmed (CI touches 0) |
| universe=50 | −0.007 (−1.13) | [−0.026, +0.017] | −0.03% (0.47) | not-confirmed |
| factor=median | −0.001 (−0.10) | [−0.019, +0.021] | +0.19% (1.06) | not-confirmed |

Primary (fwd=5): IC ≈ 0, **placebo (+0.0016) indistinguishable from real**, bootstrap CI
straddles 0, observed |IC| ≪ **MDE 0.032** ⇒ a *meaningful* null (any effect is below a
tradeable magnitude), not merely underpowered.

**Regime sign is unstable** (§67): bear −0.016 / bull +0.001 / sideways +0.014.

**Fat-tail illusion made explicit** (§31, §49): every positive-looking spread is
mean-only. At fwd=5, MEAN decile +1.70% (t=3.35) vs **MEDIAN +0.40% (t=0.91)**, median>0
in only 47% of holds — a few explosive top-decile names carry the mean; the typical name
has no edge. The robust median gate correctly refuses it.

**Sign flips with horizon** (1d reversal → 10d faint continuation, both failing gates):
fragility, not a stable estimand. fwd=10 is a *secondary* horizon and by §62.2 cannot
rescue the failed primary; after 5 trials its t=3.51 is unremarkable under multiplicity.

## 4. Verdict

**PRL-COARSE-000 = NOT CONFIRMED.** No stable cross-sectional residual-momentum edge on
2023–2026: rank-IC ≈ placebo and below MDE at the primary horizon, sign unstable across
horizon and regime, no strengthening on the liquid subset (§65.2 fails), and all positive
mean spreads are fat-tail artifacts. This confirms the strong negative prior cheaply,
**before** any 1m/fine-tier spend.

Per the tier bridge (§5.2.3), the fine tier is **not** justified to proceed on residual
*momentum* alone. Next honest ladder steps are separate hypotheses, each its own trial:
`PRL-QUALITY-000` (persistence/path quality may condition the flat momentum), and
`PRL-PEER-000` (does peer-residualization add what market-only cannot). ML remains gated
behind a simple confirmed effect (§38).

## 5. Lessons reinforced

- Rank-IC + **median** decile are the honest primary; a mean top-bottom spread is a
  fat-tail trap ([[xsect-momentum]], [[knife-catch]] one-event pattern).
- Pre-registered MDE turns "IC≈0" into an *interpretable* null vs an underpowered shrug.
- Horizon/regime sign-instability is a first-class falsifier, not a spec to tune away.
