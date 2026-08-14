# Cross-Sectional Quality — 1h Adverse-Long Hazard Protocol v1

**Frozen:** 2026-08-05, before computing any adverse-long audit result.

## Question

While a coin is held short, can a detector using only completed 1h bars identify
an emerging long anomaly early enough to protect the book from the subsequent
right-tail move?

This is a prediction-and-timing audit, not a PnL optimisation. A detector must
pass this audit before it is allowed to close a simulated position.

## Data and time contract

- Source bars: Binance USD-M 1h OHLCV, quote volume, trade count, and taker-buy
  quote volume.
- Position sample: the frozen v2 primary no-stop ledger,
  `universe=100, k=20 per side, rebalance=7`.
- Research interval: 2023-01-01 through 2025-12-31 only.
- 2026 and later remain frozen OOS.
- A bar opening at `t` is available at `t + 1h`.
- `feature_cutoff_time == snapshot_time`.
- The first labelled future bar becomes available at `snapshot_time + 1h`, so
  `future_start_time > snapshot_time`.
- All rolling baselines exclude the current bar. Missing hours invalidate the
  affected lookback or future window; they are not bridged.

Only snapshots belonging to an active short and having the complete registered
future horizon before the scheduled rebalance exit are eligible.

## Frozen detector anatomy

The detector is a strategy-owned 1h adaptation of the existing broad-anomaly
anatomy. It does not call the 1m detector with misleading time units.

Positive price evidence is any of:

1. current 1h close/open return at least 1.5%;
2. trailing 3h close return at least 2.5%;
3. trailing 12h close return at least 5%, with at least 8 positive hourly
   candles;
4. close at least 0.2% above the prior completed 24h high while the 3h return is
   positive.

The close must lie at or above 55% of the current candle range. Activity
confirmation is any of quote-volume, trade-count, or range z-score exceeding a
registered threshold, where the mean and standard deviation use the preceding
60 completed bars and exclude the current bar.

Registered variants:

- `price_only`: price evidence plus close-location condition; diagnostic upper
  bound on recall;
- `core_z2`, `core_z3`, `core_z4`: price evidence plus activity threshold 2, 3,
  or 4 respectively;
- `core_z3_buyflow`: `core_z3` plus trailing-3h taker-buy quote share at least
  52%.

`core_z3` is primary. The other variants are a pre-registered strictness
neighbourhood, not a threshold search by PnL.

## Labels and horizons

Primary horizon: 12 hours. Neighbourhood: 6 and 24 hours.

For each snapshot:

- future maximum adverse excursion for a short: maximum future hourly high
  divided by current completed close minus one;
- future close return at the end of the horizon;
- hour offset to the future maximum high.

The top adverse decile is defined separately inside each calendar year from the
continuous future-MAE label. It is used only as an outcome label.

At trade level, a catastrophic short is in the worst 5% of maximum adverse
excursion within its calendar entry year. Detector coverage must occur before
the trade's maximum adverse high.

## Frozen prediction gates

The primary `core_z3` detector passes only if, separately in 2023, 2024, and
2025:

1. its eligible-snapshot trigger rate is at most 10%;
2. mean 12h future MAE on triggered snapshots is at least 1.5 times the
   unconditional mean;
3. it recalls at least 35% of top-decile 12h adverse snapshots;
4. it covers at least 50% of catastrophic short trades before their adverse
   peak;
5. median warning lead is at least 2 hours for covered catastrophic trades;
6. its real mean-MAE lift exceeds the 95th percentile of 500 within-symbol,
   within-year circular trigger shifts.

All component metrics and all horizons are reported even if the primary fails.
Failure does not authorise threshold tuning. The next step would be a new
registered detector version, potentially weekly walk-forward, using the audit
evidence.

## Required artifacts

- complete eligible snapshot/label table with explicit time columns;
- variant/year/horizon metric summary;
- trade-level catastrophe and warning-lead table;
- component prevalence and continuous feature IC table;
- shuffle-null distribution summary;
- exact detector specification, source file count, position-ledger identity,
  IS boundary, and gate results.

