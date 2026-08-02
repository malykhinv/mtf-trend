# Binance USD-M trend ensemble: calendar-2025 IS protocol

Freeze date: **2026-07-20**. This is an exploratory adaptation of the
close-based Donchian ensemble to long-only Binance USD-M perpetual exposure.
It does not claim that Spot results transfer to futures.

## Locked partition and source

- IS is exactly **2025-01-01 through 2025-12-31**, inclusive.
- OOS begins **2026-01-01** and is not read, aggregated, profiled, or reported
  during this experiment.
- The sole market source is the existing local
  `.output/market/binance_vision/um_futures/enriched_1m` cache.
- The 1-minute rows are filtered to calendar 2025 before daily aggregation.
- Network access, downloading, alternate-provider substitution, synthetic
  backfill, and silent per-symbol fallback are forbidden.
- The local cache starts on 2025-06-03 for BTCUSDT. January through early June
  therefore remain an explicit coverage gap, not an imputed history.

Only complete daily OHLCV bars with at least 1,400 observed minutes enter the
experiment. Open-interest missingness is retained as data and exposed through
coverage features; it is not filled with prices, zeros, or later observations.

## Short-history strategy specification

The original Spot horizons `(5, 10, 20, 30, 60, 90, 150, 250, 360)` cannot be
evaluated on roughly seven months of local 2025 data. For this IS foundation
experiment the independently versioned futures grid is frozen before results:

```text
(3, 5, 7, 10, 14, 20, 30, 60, 90)
```

Point-in-time universe admission requires 90 complete history bars. This is a
short-history hypothesis screen, not evidence for the untouched 150/250/360
day Spot mechanisms. Those longer horizons remain untested.

## Causal data available in IS

- daily OHLCV, quote volume, trade count and taker-buy quote volume aggregated
  from the local minute cache;
- daily open-interest open, close, high, low, mean, observation count and
  minute-level availability;
- data-inferred active episodes based only on bars observed by each date.

Unavailable and therefore absent, not substituted:

- historical funding and premium-index series;
- top-trader account/position ratios, global long/short ratios and
  liquidations;
- announcement-time delisting metadata and futures equivalents of Monitoring
  Tag.

## Frozen feature families

1. `trend_path`: momentum acceleration, path efficiency, log-price slopes and
   R-squared, drawdown, ulcer index, return autocorrelation, higher moments,
   streak and causal Donchian state.
2. `volatility_range`: close, Parkinson, Garman-Klass and Rogers-Satchell
   volatility, jump/bipower variation, volatility-of-volatility, gaps, range
   compression and close location.
3. `liquidity_flow`: volume and trade-count shocks, mean trade notional, taker
   imbalance, signed taker flow, impact and volume-volatility interactions.
   This includes exact 5/15/30/60-minute windows ending one minute before the
   first intraday crossing of the newly entered Donchian level. Quote volume
   and trade count are normalized against the preceding 30 same-clock days by
   log-ratio, robust MAD z-score and empirical percentile; at least 10 baseline
   days are required. The crossing minute itself is excluded.
4. `open_interest_positioning`: OI level/change/z-score, intraday OI range,
   OI-to-volume, price/OI quadrants and interactions, coverage and
   cross-sectional crowding.
5. `cross_section_market`: causal ranks, return/OI dispersion, breadth and
   liquidity concentration.

`carry_basis` code remains schema-gated but has zero eligible features in this
run because the required local source is absent. No symbol identifier, future
label, later lifecycle fact, or 2026-derived statistic may enter the catalog.

## IS analysis sequence

1. Audit actual coverage, continuity, complete-day share, OI availability and
   point-in-time universe membership.
2. Measure 20-day forward return and risk-adjusted target by Donchian
   active-count bucket. Labels must end no later than 2025-12-31.
3. On rows with `trend_signal > 0`, calculate daily cross-sectional Spearman
   IC, top/bottom-quintile target spread, temporal sign stability and coverage.
4. Compare every feature with a frozen within-day shuffled-target negative
   control.
5. Use monthly-block bootstrap and Benjamini-Hochberg FDR at 10%; report low
   block count as insufficient evidence rather than relaxing the test.
6. Report highly redundant pairs (`|rho| >= 0.98`) before any model fitting.
7. Do not fit CatBoost in this pass. First decide which mechanism families are
   defensible from the IS audit, freeze the feature schema and ablations, then
   design a weekly walk-forward protocol without opening 2026.

All outputs from this pass are exploratory IS artifacts. They are not OOS
metrics, live-performance estimates, or permission to optimize PnL.
