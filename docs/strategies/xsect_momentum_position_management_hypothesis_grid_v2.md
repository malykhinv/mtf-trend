# Cross-Sectional Quality — Position-Management Hypothesis Grid v2

**Frozen:** 2026-08-05, after v1 returned zero strict plateau survivors and
before computing any v2 derived-policy result.

## Registered reason for expansion

The v1 symmetric primitive screen established:

- no primitive survives removal of the best 1% of trades;
- MFE-armed 12h structural trails improve median profit factor and 2025 but
  remain negative in 2023;
- diagnostic full 5-ATR profit-taking is positive in every pooled year and
  materially reduces, but does not remove, concentration;
- long and short legs have opposite year-level behaviour, so applying one
  management rule symmetrically is an unjustified restriction.

V2 therefore tests asymmetry and causal regime qualification without changing
the frozen entries or opening the 2026 OOS period.

## Sources

- All v1 mandate-policy partitions and their frozen source ledgers.
- Original no-stop ledgers for trade identity and rebalance timestamps.
- Causal daily market context evaluated at `rebalance_date`, never entry-exit
  outcomes.

## Grid A: side-specific policies

Every v1 policy is applied separately to:

1. long only, with short held to scheduled exit;
2. short only, with long held to scheduled exit.

Diagnostic fixed-ATR policies remain in the report but cannot be accepted.

## Grid B: independent long/short pairs

Every admissible v1 long policy is crossed with every admissible v1 short
policy. This is a complete factorized cross, not a hand-picked shortlist.

The first pass computes additive PnL, profit factor, year support, and config
support for every pair. Exact pooled top-1 concentration is computed for every
pair passing the other gates, not only the single best pair.

## Grid C: causal regime qualification

Each v1 primitive is applied to one side only when one frozen entry-time state
is true; otherwise that side follows `hold`:

```yaml
btc_above_30d_mean: BTC close > prior 30d mean
btc_below_30d_mean: BTC close <= prior 30d mean
btc_fast_above_slow: prior 7d mean > prior 30d mean
btc_fast_below_slow: prior 7d mean <= prior 30d mean
btc_30d_up: prior 30d return > 0
btc_30d_down: prior 30d return <= 0
btc_drawdown_30d_gt10: BTC <= -10% from prior 30d high
breadth_7d_bull: more than 60% of eligible symbols positive over prior 7d
breadth_7d_bear: less than 40% positive over prior 7d
btc_vol_expansion: prior 7d realised volatility > 1.25x prior 30d
btc_vol_compression: prior 7d realised volatility < 0.75x prior 30d
weekend_entry: entry occurs Saturday or Sunday UTC
```

All rolling values are shifted so the current execution day is absent.

## Grid D: structure-anchored analogue of the 5-ATR diagnostic

For each structural scale `(6, 12, 24)`, select the nearest pre-entry confirmed
favourable swing whose distance is at least `(3, 4, 5)` entry ATRs. Close
`(25%, 50%, 75%, 100%)` at that exact swing. The physical price is structural;
ATR only chooses among already-confirmed market levels.

This family requires a new path pass and is evaluated after Grids A–C.

## Multiplicity and plateau gates

No cell is an approved result. A family advances only if:

1. at least 12/18 configurations are positive;
2. median configuration PF >= 1.05;
3. pooled 2023, 2024, and 2025 PnL are all positive;
4. pooled PnL after removing the best 1% of trades is positive;
5. no configuration supplies more than 25% of positive PnL;
6. at least one adjacent policy in the same family also passes;
7. the family-level false-discovery rate is reported;
8. fixed-ATR diagnostic rows are never admissible.

Any survivor still requires exact hourly portfolio replay with costs, funding,
daily mark-to-market drawdown, leg depletion, 2025-10-10 attribution, and
shuffled-score controls.
