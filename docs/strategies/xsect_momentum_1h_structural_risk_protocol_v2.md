# Cross-Sectional Quality — 1h Structural-Risk Protocol v2

**Frozen:** 2026-08-05, before any v2 wide-universe result is computed.

## Why v1 is invalidated

Protocol v1 used `top_k=20` per side inside `universe_n=30`. The top and bottom
twenty must overlap in a thirty-name universe, so the selector produces partially
cancelled net weights rather than forty distinct positions. The first hourly
engine incorrectly levered those net weights back to full side exposure. Its
reconciliation gate caught the error before any structural-exit result ran.

The four v1 no-stop artifacts are diagnostic only. They are not research results.

## Question and time boundary

Can a confirmed point-in-time 1h structural exit remove catastrophic short-tail
losses from a genuinely broad cross-sectional book?

Only 2023-01-01 through 2025-12-31 is read. Data from 2026 onward remains frozen
OOS. Features and selection are unchanged.

## Frozen wide-universe plateau

Primary:

```yaml
universe_n: 100
top_k_per_side: 20
rebalance_days: 7
max_weight: 0.10
gross_exposure: 1.0
```

Robustness family:

```yaml
universe_n: [75, 100, 200]
top_k_per_side: [15, 20]
rebalance_days: [3, 7, 14]
max_weight: 0.10
```

Every configuration satisfies `2 * top_k <= universe_n`, so long and short
selections cannot overlap. The universe is rebuilt point-in-time from trailing
liquidity and age; it is not a present-day survivor list.

## Structural anchor and execution

The anchor, fill, cost, funding, missing-anchor, and no-resurrection contracts
are unchanged from v1:

```yaml
left_hours: 6
right_hours: 6
anchor_lookback_hours: 168
gap_fill: hourly_open_if_worse_than_stop
missing_anchor: skip_position_then_renormalize_same_side_with_weight_cap
```

Short physical stops use the most recent confirmed pre-entry 1h swing high above
entry. Long stops use the most recent confirmed pre-entry 1h swing low below
entry. No percent or ATR offset is allowed.

Variants:

1. `no_stop` accounting/reconciliation control;
2. `symmetric_structural` primary exit policy;
3. `short_structural` tail-risk diagnostic.

## Required order and controls

1. Run all 18 hourly `no_stop` cells.
2. Each must reconcile to the corrected daily engine within 10 bps final equity
   and 1 bp max drawdown. Any failure stops the experiment.
3. Run both structural variants across all 18 cells.
4. Run the primary `universe=100, k=20, rebalance=7` symmetric policy with
   shuffled score seeds `(11, 29, 47)`.

## Frozen acceptance gates

No gate is relaxed from v1:

- max drawdown `<= 15%`;
- worst day `>= -8%`;
- bootstrap Q05 Sharpe `> 0`;
- positive in 2023, 2024, and 2025;
- primary beats all three structurally identical shuffled controls;
- neighbourhood support, not a single isolated cell;
- existing G1 and all concentration metrics remain reported unchanged.

## Required artifacts

Summary, daily equity/returns, full trade ledgers, structural anchors and
confirmation times, fill reasons/prices/costs, anchor coverage, skipped entries,
forced exits, reconciliation differences, policy version, IS boundary, and
hourly source count are saved for every run.
