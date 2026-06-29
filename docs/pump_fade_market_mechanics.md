# Pump-fade market mechanics and participant inference

This document is explanatory and non-normative. The only canonical Strategy Spec
for the current pump-fade research thread is `docs/pump_fade_archetype_protocol.md`.
If this document appears to conflict with that spec, the canonical spec wins and
this document must be corrected.

## Purpose

The pump-fade feature set should describe the auction and the observable actions of participants, not collect decorative indicators. Every feature should answer one of these questions:

1. How was price moved?
2. Who appears to be demanding immediacy and who appears to be absorbing it?
3. Is risk being opened or closed?
4. Is the move idiosyncratic or part of a market-wide impulse?
5. Is participation broadening, concentrating, exhausting, or changing character?
6. What structural price levels make the hypothesis wrong or complete?

Features are point-in-time observables or explicitly named proxies. A proxy must never be reported as participant identity.

## Structural execution policy

The normative structural execution policy lives in
`docs/pump_fade_archetype_protocol.md`. This explanatory note must not redefine
stop anchors, target anchors, partial-close grids, or selection/evaluation rules.

## Strategy/Core implementation boundary

`BaseResearchStrategy` is the common typed boundary for strategy-owned semantics. Both the fixed-horizon `BaseStrategy` variants and `PumpFadeStrategyDefinition` declare through it:

```text
required versus optional data streams
structural execution policies
the complete custom-feature catalog and identifiability class
```

`PumpFadeStrategyDefinition` additionally freezes the detector, horizon-free close-race label protocol, feature schema and strategy identity as declared by the canonical Strategy Spec. The optimized per-symbol pump builder consumes this definition and writes it into dataset metadata. It is intentionally not forced through the fixed-horizon `BaseStrategy` label API: doing that would silently replace the canonical no-horizon race with a different scientific target.

Core's standard feature-matrix pipeline now calls `generate_custom_features(StrategyFeatureContext)` for fixed-horizon strategy rows and persists the returned mapping plus a custom-feature schema. The canonical pump builder uses its optimized vectorized path but the same declared catalog. In both paths, model code sees only declared causal features; unknown, missing, non-finite or future-dependent values fail the contract.

## Implemented causal mechanics

### Pump geometry and auction path

- pump size and verticality;
- exact maximum one-minute `high/open` and `close/open` returns;
- largest green-candle contribution;
- close-path efficiency: net close displacement divided by total absolute close path;
- event duration, number of new highs and time between them;
- mean and maximum closed-bar pullback between successive running highs;
- distance from current close to base and running high;
- VWAP extension and base-break state;
- turnover and trade-count concentration in the largest minute.

These distinguish one-shot repricing, persistent execution, choppy re-auction and repeated squeeze/reload cycles.

### Candle rejection at economically relevant points

- upper/lower wick fraction;
- body fraction and close location inside the range;
- ignition-candle wick;
- current, mean and maximum event upper wick;
- green-candle fraction;
- range contraction and price deceleration.

A wick is treated as evidence of intrabar rejection, not as proof of a seller. Without order-book data it may also be profit-taking, a temporary liquidity gap or mechanical liquidation flow.

### Participation and trade-size proxies

- absolute 24h quote turnover as an audit/context field;
- event turnover share of trailing 24h turnover;
- event trade count;
- quote turnover per reported trade relative to trailing history;
- trade-count acceleration relative to quote-volume acceleration (`retail_frenzy_proxy`);
- quote-volume acceleration relative to trade-count acceleration (`large_print_proxy`);
- minute-participation regularity (`algorithmic_persistence_proxy`);
- fraction of event minutes maintaining extreme activity.

These are proxies only. Kline-level `quote_volume / trade_count` is an average, not a trade-size distribution. Reliable retail/bot/whale separation requires raw trades or aggTrades to measure size quantiles, repeated-size signatures, inter-arrival times, clustering, aggressor runs and concentration.

### Aggressor flow

- event taker-buy share;
- signed taker imbalance;
- early-versus-recent taker-flow change;
- price/CVD disagreement and failed confirmation of new highs.

Aggressor imbalance indicates who demanded immediate execution. It does not reveal the inventory or intent of the passive counterparty.

### Open interest and positioning states

- OI change over 5m, 15m, 60m and 240m;
- OI change since ignition;
- price-up/OI-up;
- price-up/OI-down;
- price-down/OI-up;
- price-down/OI-down;
- price/OI divergence combined with taker imbalance.

Interpretation must remain conditional:

| Observable state | Compatible explanation | Not proven |
|---|---|---|
| Price up, OI up | new gross risk enters; aggressive buyers may meet new passive shorts | “a whale opened a long” |
| Price up, OI down | short covering or bilateral deleveraging | forced short liquidation without liquidation data |
| Price down, OI up | new gross risk enters; aggressive sellers may meet new passive longs | “smart money opened shorts” |
| Price down, OI down | long closing/liquidation or bilateral deleveraging | exact liquidated side/size |

OI samples are joined only after their availability time and never carried across unbounded gaps.

OI is evaluated through a paired ablation, not by adding nullable columns to the old population. Both arms use one shared prepared row population satisfying `oi_available=true` plus finite values for every registered OI model feature; the baseline excludes all OI values, while the second arm adds the registered price/OI state family. The availability flag is a population/audit gate and is forbidden as a model feature. A smoke run checks contracts and runtime only. Incremental evidence requires the full paired run, blind and calendar-block shuffled controls in both arms, followed by group-block paired inference on the performance difference. The paired runner reads/limits the input once, prepares the OI-covered population/split once, and then builds two separate feature matrices so performance work is not duplicated on i5-class hardware. Archetype CatBoost fits use an explicit bounded `catboost_thread_count` registered in config/run metadata; `thread_count=-1` is forbidden for laptop evidence runs because it can oversubscribe CPU and destabilize wall-clock/runtime comparisons.

### Preconditioning and regime

- returns over 15m, 60m, 4h and 24h;
- drawdown from prior 60m/4h high: dump-before-pump context;
- price displacement from causal 60m/240m/1440m exponential means;
- slope of the slow causal mean;
- trade, quote-volume and true-range activity in the last hour relative to the prior four-hour rate;
- minutes since the market last traded above the current anomaly high;
- prior same-symbol anomalies in 24h/48h and only outcomes resolved before the current snapshot.

EMA is used only as a compact regime coordinate, not as a crossover trading signal.

### Market-relative context

Core owns and supplies:

- BTC return correlation over registered windows;
- symbol return minus BTC return;
- cross-sectional return, volume, OI-growth and range percentiles;
- simultaneous anomaly count/share;
- systemic versus idiosyncratic shock regime;
- market shock identifier.

A dedicated broad-altcoin index should be added as a point-in-time universe-weighted return series. It must not use a survivorship-biased fixed basket.

## Important mechanics requiring richer data

### Raw trades / aggTrades

Needed for defensible retail-versus-algorithmic participation inference:

- trade-size quantiles and tail concentration;
- Herfindahl/concentration measures by aggressor side;
- inter-trade arrival distribution and burstiness;
- repeated-size and periodic execution signatures;
- aggressive buy/sell run lengths;
- price impact per unit aggressive notional;
- many-small-trades versus few-large-trades decomposition;
- unique taker count, if a source exposes it.

### Order book and order events

Needed to distinguish initiative from absorption:

- depth consumed per price level;
- replenishment and iceberg-like refill;
- cancel/replace pressure;
- spread and depth collapse before the jump;
- passive absorption at the high;
- order-book imbalance conditioned on distance to structural levels;
- price impact followed by liquidity recovery.

### Explicit liquidation stream

Needed to distinguish forced flow from voluntary urgency:

- long/short liquidation notional;
- liquidation acceleration and clustering;
- liquidation share of traded volume;
- price impact per liquidation dollar;
- continuation after the liquidation impulse ends.

Binance Vision USD-M has no historical `liquidationSnapshot` for the current period. Missing liquidation data must remain missing; it must not be inferred from candles or replaced with zeros.

### Position/account-level data

Only account-level or venue-provided cohort data could directly identify a whale, retail cohort, market maker or position closure. Public OHLCV, taker flow and aggregate OI can classify observable mechanics and competing explanations, not actor identity.

## Scientific use

The complete feature family is a registered search space, not a license to narrate every leaf. CatBoost leaves are candidate online-identifiable phenotypes. A phenotype becomes evidence only after:

```text
rolling-origin recurrence
distinct-coverage selection
later chronological verification
matched blind comparison
week-cluster inference
multiple-testing correction
calendar-block shuffled-label controls
outer-fold execution-policy evaluation
```

Every report must preserve an `unclassified` remainder. The system searches for supported categories; it does not claim that the finite observed feature space contains every real pump nature.
