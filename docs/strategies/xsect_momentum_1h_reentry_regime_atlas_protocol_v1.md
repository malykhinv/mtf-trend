# Cross-Sectional Quality — Re-entry Regime Atlas Protocol v1

**Frozen:** 2026-08-05, before computing the regime-atlas results.

## Question

Why did causal midpoint short re-entry work in 2025 but fail in 2023 and 2024?
Can market-wide state known at the re-entry snapshot distinguish exhaustion from
a second pump wave across all three years?

This is an explanatory hypothesis atlas, not a PnL optimisation and not a final
filter-selection claim. The 2026 OOS interval remains unread.

## Fixed event sample and outcomes

- Events are the already-registered first `midpoint_only` signals from
  `hourly_structure_reentry_audit_v1`.
- Primary outcome: future 24h close return; negative is favourable for a short.
- Secondary outcomes: 48h, 72h, and return to the end of the original mandate.
- Tail outcome: whether the known whole-event pump high is breached in the next
  24h.
- No lower-high stop is used as a primary risk anchor.

## No-tight-swing contract

The next execution experiment may initially protect a re-entry only at the
exact known high of the full anomaly event. A later trail is admissible only
behind a macro swing confirmed by:

- adaptive reversal of at least 4, 6, or 8 times the preceding 24h median
  hourly log range (registered neighbourhood);
- a completed leg lasting at least 12 or 24 hours;
- an exact market high as the physical anchor.

These scale variants are future confirmation candidates, not stop offsets. No
fixed-percent or ATR-multiple physical stop is allowed. The earlier tight
lower-high anchor is retained only as a diagnostic and is not promoted.

## Frozen causal context families

### Bitcoin level and direction

- BTC returns over 6h, 24h, 72h, 7d, 30d, and 90d;
- BTC close relative to causal 7d, 30d, and 90d means;
- fast 7d mean above/below slow 30d mean;
- drawdown from causal 7d, 30d, and 90d highs;
- recent crossings of the 30d mean;
- acceleration of 24h versus 7d and 7d versus 30d returns.

### Bitcoin volatility and activity

- realised 24h, 7d, and 30d volatility;
- 7d/30d volatility ratio;
- BTC quote-volume, trade-count, and range activity z-scores;
- compression and expansion regimes defined only from trailing state.

### ETH and alt/BTC relationship

- ETH returns over 24h, 7d, and 30d;
- ETH minus BTC return over the same horizons;
- divergence between BTC direction and point-in-time universe breadth.

### Point-in-time market breadth

Using the previous fully completed daily bar and that day's causal top-100
liquidity/age universe:

- median 1d, 7d, and 30d return;
- positive-return share over 1d, 7d, and 30d;
- share above causal 7d and 30d means;
- cross-sectional return dispersion;
- breadth acceleration and BTC-minus-breadth divergence.

### Active-short-book synchrony

At the completed 1h snapshot:

- share/count of active shorts in `core_z2` warning;
- share with positive 1h, 3h, and 12h movement;
- median and dispersion of short-book returns;
- median activity z-scores;
- isolated-coin anomaly versus market-wide short squeeze.

### Event geometry and path dependence

- event size from causal swing-low origin to known event high;
- distance from re-entry close to event-high stop;
- re-entry delay, VWAP/log-midpoint ordering, and retracement depth;
- whether the event high is also a prior 7d or 30d high;
- coin return relative to BTC before re-entry;
- taker-buy share and residual activity at re-entry.

### Non-standard but causal context

- UTC session and weekend;
- calm-BTC/violent-alt divergence;
- BTC up with deteriorating breadth;
- BTC down with improving breadth;
- simultaneous warning crowding;
- volatility compression followed by cross-sectional dispersion expansion;
- transition age since BTC crossed its 30d mean.

## Registered hypothesis grid

The runner evaluates:

1. every registered semantic boolean filter individually;
2. every pairwise AND combination of filters from different families;
3. categorical cells crossing BTC 30d state, 7d breadth state, and short-book
   anomaly synchrony;
4. all outcomes by 2023, 2024, and 2025 separately.

Minimum admissible support is 30 events and 15% event coverage in every year.
Results report worst-year mean return, minimum negative-return share, event-high
breach, and whole-event bootstrap uncertainty. Benjamini-Hochberg false-discovery
rates are reported for the broad grid. Passing this atlas is not sufficient for
deployment: any discovered filter must be frozen in a new protocol and retested
without changing its thresholds.

## Required artifacts

- event-level market-context dataset with explicit cutoff times;
- feature dictionary and family membership;
- single-filter and pair-filter grid;
- BTC/breadth/synchrony regime cube;
- yearly outcome and support table;
- bootstrap/FDR summary;
- full rejection list and a ranked robust-candidate table;
- exact input hashes, IS boundary, and source coverage.

