# Cross-Sectional Quality — 1h Structural-Risk Protocol

**Frozen:** 2026-08-04, before the first hourly exit result is computed.

**Scope:** in-sample 2023-01-01 through 2025-12-31 only. The 2026 OOS tail
remains unopened. This experiment does not change the quality score or use an
exit result as a feature.

## Question

Can point-in-time hourly market structure remove the rare catastrophic losses
of the corrected market-neutral quality book without manufacturing returns from
an optimistic fixed-percent stop?

The corrected no-stop sweep remains the baseline artifact:
`.output/results/xsect_momentum/sweep_market_neutral_corrected_v2.parquet`.

## Frozen portfolio plateau

Primary configuration:

```yaml
top_k_per_side: 20
rebalance_days: 7
universe_n: 30
max_weight: 0.10
gross_exposure: 1.0
```

Neighbourhood robustness configurations:

```yaml
top_k_per_side: [15, 20]
rebalance_days: [3, 7, 14]
universe_n: 30
max_weight: 0.10
```

No other score, universe, weighting, cost, or timing parameter is tuned here.

## Structural anchor contract

Hourly bars use UTC open timestamps. At an entry at hour `T`, an hourly bar is
available only when its close time is `<= T`.

A confirmed swing uses:

```yaml
left_hours: 6
right_hours: 6
anchor_lookback_hours: 168
```

A swing high is an hourly high not lower than any high in its six bars to the
left and six bars to the right. A swing low is defined symmetrically. The six
right-hand bars must all have closed before entry, so confirmation is strictly
point-in-time.

For a short, the admissible stop is the most recent confirmed swing high that is
strictly above the entry open. For a long, it is the most recent confirmed swing
low strictly below the entry open. The physical stop is exactly that structural
price: there is no percent or ATR offset.

If a protected side has no admissible anchor, that position is skipped before
weights are normalized. Anchor absence and coverage are reported explicitly.

## Frozen exit variants

1. `no_stop` — hourly accounting control; must reconcile to the corrected daily
   engine before exit results are interpreted.
2. `symmetric_structural` — structural stop on both longs and shorts. This is the
   primary exit policy.
3. `short_structural` — structural stop on shorts only; longs retain the normal
   rebalance exit. This is a diagnostic for the observed right-tail short risk.

Stopped positions remain flat until the next scheduled rebalance. There is no
same-period resurrection.

## Fill and cost contract

For a long stop:

- if the hourly open is at or below the stop, fill at that open;
- otherwise, if the hourly low touches the stop, fill exactly at the stop.

For a short stop:

- if the hourly open is at or above the stop, fill at that open;
- otherwise, if the hourly high touches the stop, fill exactly at the stop.

Thus gaps are never improved back to the stop price. Every stop exit pays the
same fee, spread, slippage, and causal ADV-impact model as an ordinary exit.
Funding is charged on positions that remain active.

## Controls and acceptance

The primary configuration is run against the same three within-date shuffled
score seeds `(11, 29, 47)` under the identical structural-exit policy.

The experiment is admitted only if:

- the hourly `no_stop` control reconciles to the corrected daily result within
  10 basis points of final equity and one basis point of max drawdown;
- primary and neighbouring structural variants form a plateau rather than one
  isolated cell;
- max drawdown is `<= 15%`;
- worst day is no worse than `-8%`;
- bootstrap Q05 Sharpe is positive;
- each of 2023, 2024, and 2025 is positive;
- the primary real score beats all three shuffled-score controls;
- all existing concentration gates, including G1, are reported unchanged.

Failure of an existing gate is not redefined after results. Partial closes,
tail filters, volatility sizing, OI/funding filters, and alternative swing
definitions belong to later separately frozen experiments.

## Required artifacts

- configuration summary for every cell and exit variant;
- daily returns and equity;
- trade ledger with entry, anchor, stop fill, exit reason, and costs;
- anchor coverage and skipped-position counts;
- shuffled controls for the primary;
- input period, hourly file count, and policy version.
