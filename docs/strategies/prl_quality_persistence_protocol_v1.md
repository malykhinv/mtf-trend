# PRL-QUALITY-000 — residual path-quality / persistence (protocol + result)

**Strategy:** `prl_daily_v1` (tier: coarse) — Persistent Relative Leadership family
**Spec:** PRL_STRATEGY_SPEC.md v1.2.0 (§14 persistence, §15 path quality, H2)
**Code:** `src/anomaly_science/strategy/prl/research/quality.py`, `run_quality.py`
**Tests:** `tests/test_prl_quality.py` · **Run:** `python -m anomaly_science.strategy.prl.research.run_quality`
**Status:** IS positive (2023-01 → 2025-12). **OOS (≥ 2026-01-01) reserved, unread.**

---

## 1. Question

[[prl_coarse_existence_protocol_v1]] found flat residual-momentum RANK has ~0 IC, yet the
top decile's MEAN was inflated by a few explosive names. H2: does a **quality axis** of the
residual path separate persistent, smoothly-built leaders (which continue) from one-shot
bursts (which reverse)? I.e. are the fat-tail winners just low-quality burst names?

## 2. Features (causal residual `eps`, trailing q_lb=28d, shift skip)

```text
path_eff   = |sum eps| / sum|eps|            smooth trend vs chop
frac_pos   = share of days with eps > 0      persistence
burst      = max |eps day| / sum|eps|        one-shot signature
recent_shr = last-quarter net / |net|        acceleration
composite  = mean rank(path_eff, frac_pos, -burst)   high = persistent, smooth, non-burst
```
Target = the same frozen-beta future residual return (5d) as COARSE-000. Same OOS embargo.

## 3. Result — IS (663 symbols, 999 IC days)

Standalone rank-IC vs future residual:

| feature | IC (t) | boot 90% CI | placebo | wk>0 |
|---|---|---|---|---|
| **frac_pos** | **+0.0424 (+8.96)** | [+0.029, +0.058] | +0.0003 | 0.65 |
| **burst** | **−0.0320 (−8.16)** | [−0.045, −0.019] | −0.0000 | 0.38 |
| **composite** | **+0.0358 (+7.91)** | [+0.021, +0.052] | +0.0012 | 0.65 |
| path_eff | −0.003 (−0.67) | [−0.019, +0.011] | — | 0.53 |
| recent_shr | −0.000 (−0.02) | [−0.019, +0.019] | — | 0.52 |

Persistence predicts continuation; one-shot burst predicts **reversal** (the COARSE fat-tail
winners). Both clear the MDE (0.032), bootstrap CI and placebo. IC ≫ the flat-momentum null.

**Regime robustness (§67) — the decisive test, PASSED:** sign stable across all regimes,
and *strongest in sideways* (not bull), which rules out a bull-only beta-leak:

```text
frac_pos   bull +0.042  bear +0.027  sideways +0.062
burst      bull -0.025  bear -0.038  sideways -0.039
composite  bull +0.031  bear +0.027  sideways +0.055
```

**H2 conditional double-sort** (within momentum leaders, hi- vs lo-quality, MEDIAN future
residual): spread **+0.60%/hold (t=2.63)**, net +0.32%, positive in 62% of rebalances — **PASS**.

**Nearby-spec robustness (§68):**
- window q_lb 14 / 28 / 56 → composite IC +0.022 / +0.036 / +0.048 (monotone, sign-stable;
  H2 double-sort passes at 28 & 56, fails at 14 — the conditional form needs a longer window).
- **beta ≡ 1** (no per-symbol beta estimated; residual = ret − market): composite IC **+0.046
  (t=9.19), even stronger** — definitively refutes a beta-misestimation artifact.

## 4. Verdict

**PRL-QUALITY-000 = IS-POSITIVE, robust.** Residual-path quality — persistence (+) and
one-shot burst (−) — is a genuine, regime-stable, placebo-clean, window- and beta-robust
cross-sectional predictor of future residual return, where flat momentum magnitude was not.
Mechanistically plausible (consistency premium + short-term reversal of jumps).

**Caveats:** magnitude is modest (IC ~0.04; H2 net +0.32%/5d hold, 62% pos-reb) — a real
predictive relationship, tradeability not yet established (§32.3). This is IS development
(§95 level: robust relationship + regime + placebo + nearby-spec + beats baseline). NOT yet:
locked-holdout / OOS. **OOS remains reserved and unread.**

## 5. Next (each a separate pre-registered trial)

1. **Freeze** the quality spec (features, q_lb, composite, universe, costs) → **OOS 2026-H1**
   validation of sign + magnitude, then locked holdout (§62).
2. `PRL-PEER-000`: does peer-residualization (Epps-aware clustering) add to market-only?
3. Only after OOS confirmation: fine-tier (1m) executability + a real net-of-cost portfolio,
   capacity, tail/crash diagnostics (§45–§49).
