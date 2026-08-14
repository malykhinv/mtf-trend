# Cross-Sectional Quality — Position-Management Screen v1

**Frozen:** 2026-08-05, before computing any screen result.

## Purpose

Determine whether a broad, neighbouring plateau of causal position-management
policies improves the frozen cross-sectional book without depending on a
single configuration, year, or extreme winner.

This is a two-stage experiment. Stage 1 is a fast mandate-level screen over the
existing reconciled no-stop ledgers. Stage 2 may replay only plateau survivors
through the exact hourly portfolio engine. A Stage-1 winner is not an approved
strategy.

## Frozen sources and boundary

- The 18 reconciled real no-stop ledgers from structural experiment v2:
  `universe_n ∈ {75,100,200}`, `k ∈ {15,20}`, `rebalance_days ∈ {3,7,14}`.
- Binance USD-M 1h bars strictly before `2026-01-01`.
- Original daily selection, position weights, entry times, scheduled exits,
  costs, and funding remain frozen.
- Every structural level must be confirmed no later than the decision time.
- Data from 2026 onward remains frozen OOS.

## Structural scales

Fixed-neighbour swings are screened only as a scale family, not tuned singly:

```yaml
left_hours:  [6, 12, 24]
right_hours: [6, 12, 24]
lookback_hours: 336
```

Physical stop and target prices are exact confirmed swing prices. ATR may arm
a policy or define a diagnostic touch, but it may not create an admissible
production TP/SL price.

## Stage-1 primitive families

Each primitive is evaluated independently before any hybrid is formed.

1. `hold_control` — scheduled exit only.
2. `time_exit` — full exit after 24, 48, 72, 120, or 168 hours.
3. `structural_stop` — close 25%, 50%, 75%, or 100% at the pre-entry adverse
   swing; any remainder stays to scheduled exit.
4. `structural_target` — close 25%, 50%, 75%, or 100% at the pre-entry
   favourable swing; any remainder stays to scheduled exit.
5. `structural_bracket` — full adverse structural stop plus 25%, 50%, 75%, or
   100% at the favourable structural target. If both are touched in one hourly
   bar, the adverse fill is assumed first.
6. `mfe_armed_structural_trail` — after completed-bar MFE reaches 1, 2, 3, or
   5 causal entry ATRs, close 25%, 50%, or 100% only when a subsequently
   confirmed favourable-side swing is broken. The exit price is the exact
   swing, never the ATR threshold.
7. `favourable_add` — start with 50% or 75%; add the reserved fraction after a
   completed-bar favourable move of 1 or 2 entry ATRs; execute next-hour open.
8. `adverse_add` — same bounded starter fractions, but add after a completed-bar
   adverse move of 1 or 2 ATRs; execute next-hour open. This family is a risk
   diagnostic and cannot pass unless every year and concentration gate passes.
9. `fixed_atr_take_profit_diagnostic` — 1 through 5 ATR, closing 25%, 50%, 75%,
   or 100%. These rows are explicitly non-admissible for production and exist
   only to answer whether terminal giveback is economically important.

Entry ATR is the mean true range of the 14 completed bars ending one hour
before entry. Touch fills are gap-pessimistic. Time and armed decisions execute
no earlier than the following hourly open.

## Stage-1 accounting

- Returns are computed per frozen mandate and multiplied by its original entry
  notional.
- Original costs are retained conservatively even when a partial exit shortens
  holding time.
- Adds pay an additional proportional round-trip cost estimated from the
  frozen ledger.
- Stage 1 does not claim exact portfolio drawdown because independent mandate
  replay does not resize later entries after earlier exits.

## Plateau gates

A policy family has neighbourhood support only if:

1. at least 12 of 18 configurations have positive net PnL;
2. median configuration profit factor is at least 1.05;
3. worst-year pooled PnL is positive in 2023, 2024, and 2025;
4. pooled PnL remains positive after removing the best 1% of trades;
5. no single configuration contributes more than 25% of pooled positive PnL;
6. the profitable result is supported by at least two adjacent parameter
   values inside the same policy family;
7. non-admissible ATR-target diagnostics can never be labelled candidates.

## Stage 2

Only plateau-supported primitives may form minimal two-component hybrids.
Exact portfolio replay then adds separately:

- shrink the opposite long leg after short exits;
- replace an exited short with the next frozen-score eligible reserve;
- regime-qualified re-entry variants already registered in prior audits;
- at most one favourable or adverse add per mandate.

Stage 2 must report exact daily equity, drawdown, tail days, turnover, funding,
leg gross/net exposure, 2025-10-10 attribution, and all existing G1–G8 gates.

## Required artifacts

- immutable protocol hash and source ledger hashes;
- one row per mandate/policy result;
- policy/config/year summaries;
- family plateau table and rejection reasons;
- touch/giveback diagnostics;
- exact Stage-2 daily curves and ledgers for any survivors.
