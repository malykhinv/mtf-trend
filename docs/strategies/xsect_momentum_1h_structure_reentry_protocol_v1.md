# Cross-Sectional Quality — 1h Structure-Aware Re-entry Protocol v1

**Frozen:** 2026-08-05, before computing any v1 structure/re-entry result.

## Question

After an active short is defensively exited on an adverse long anomaly, can a
causal return into the observed anomaly value zone, together with fading buy
aggression and restored bearish market structure, identify a safe and useful
short re-entry?

This is a prediction/timing audit. It does not yet alter portfolio PnL.

## Frozen source and boundary

- Position mandates: frozen v2 primary no-stop ledger,
  `universe=100, k=20 per side, rebalance=7`.
- Warning event: first `core_z2` adverse-long signal inside an active short.
- At most one warning/re-entry episode per trade mandate.
- 1h Binance USD-M bars through 2025-12-31 only; 2026 remains frozen OOS.
- All features and structural confirmations are available no later than
  `snapshot_time`; all prediction labels start strictly later.
- Re-entry, if later simulated, executes at the next hour open after a completed
  re-entry signal and creates a new trade segment with fresh costs.

## Causal visible-structure state

The v2 fixed-neighbour pivot is not reused. Structure is represented by a
directional-change state machine:

1. the live leg tracks its exact running high or low;
2. its reversal threshold is twice the median log high/low range of the
   preceding 24 completed 1h bars, excluding the current bar;
3. an up leg confirms its exact running high as a swing high only after a close
   retraces by the causal threshold;
4. a down leg confirms its exact running low symmetrically;
5. a confirmed high below the previous confirmed high is a lower high;
6. a confirmed low below the previous confirmed low and a lower high define
   restored bearish structure.

The adaptive threshold only confirms whether a market swing is visible. Any
eventual physical stop remains anchored to the exact confirmed market high; no
percent or ATR-multiple stop price is created.

## Frozen anomaly value zone

At the first `core_z2` warning:

- impulse origin is the most recent confirmed swing low known at warning time;
- running event high is updated only from bars already completed;
- log midpoint is `sqrt(impulse_origin * running_event_high)`;
- event VWAP uses the typical price and quote volume of completed bars from the
  warning bar through the current bar;
- the value zone is the interval between log midpoint and event VWAP.

An episode without a causal pre-warning swing-low origin is skipped. There is no
fallback to a future low or arbitrary percentage.

## Registered re-entry variants

Signals are evaluated no earlier than two hours after the warning and only while
the original short mandate remains active.

1. `midpoint_only`
   - completed close is below the lower edge of the current value zone.

2. `midpoint_flow`
   - `midpoint_only`;
   - trailing-3h taker-buy quote share is at most 50%;
   - current quote-volume and trade-count z-scores are both below 2.

3. `midpoint_structure`
   - `midpoint_only`;
   - a new confirmed lower high formed after the warning;
   - restored bearish structure is active;
   - the lower high is below the known running event high.

4. `combined` (primary)
   - all `midpoint_flow` and `midpoint_structure` conditions.

The first eligible signal per variant and episode is retained. A new event high
does not use future data; it simply updates the still-open episode state.

## Outcomes

Primary horizon: 24 hours. Neighbourhood: 12 hours.

From the re-entry signal close:

- future close return (negative is favourable for the short);
- maximum future high return (short MAE);
- minimum future low return (short MFE);
- whether the exact confirmed lower-high stop anchor is breached before the
  horizon ends;
- signal delay from warning and remaining mandate time.

## Frozen prediction gates for `combined`

Separately in 2023, 2024, and 2025:

1. at least 30 eligible re-entry events;
2. at least 20% of valid warning episodes receive a re-entry signal;
3. at least 55% of 24h future close returns are negative;
4. mean 24h future close return is negative;
5. the 95% upper bootstrap bound of the mean 24h future close return is below
   zero, using 2,000 resamples of whole re-entry events;
6. mean 24h return is lower than `midpoint_only` in the same year;
7. no more than 25% of signals breach their exact confirmed structural stop
   anchor during the next 24h.

All variants and both horizons are reported even on failure. Thresholds are not
retuned after seeing the audit. Failure leads to a new registered version or a
different protection mechanism, not an unregistered PnL search.

## Required artifacts

- causal structure-enriched active-short snapshots;
- warning episode ledger and exact origins/highs/value zones;
- first re-entry signal ledger for all variants;
- year/variant/horizon prediction metrics and bootstrap bounds;
- structure confirmation and stop-anchor times/prices;
- rejection-reason counts, source identity, detector/structure specs, IS
  boundary, and gate results.

