# Market Regime Layer

This note fixes the next research layer above raw pump shape.

## Why this layer exists

Price/volume shape alone explains only the *stage* of a pump.
To understand why the same anomaly sometimes continues and sometimes collapses,
we also need a rough view of the *driver* of the move:

- cash/spot-like expansion
- leverage build-up
- leverage overheating
- squeeze-like move
- thin-liquidity move

## What is available right now

The project now has a separate market-regime dataset builder:

- `strategy/hourly_asia_pump/market_regime_dataset.py`

Outputs are written to:

- `.output/results_prev_year_5m/market_regime_dataset/`

Current fields:

- stage-like features from the anomaly DB
- historical funding
- historical premium index klines (`4h`)
- optional `open_interest` from current `5m` cache only

Current stage labels:

- `early_accumulation_to_expansion`
- `spot_like_expansion_shape`
- `late_expansion_or_repricing`
- `distribution_or_false_break_shape`
- `thin_liquidity_like`
- `mixed_shape`

Current driver labels:

- `spot_like_or_cash_led`
- `futures_momentum_build`
- `derivatives_overheating`
- `short_squeeze_like`
- `leverage_pressure_unknown_oi`
- `mixed_or_unknown`
- `thin_liquidity_like`

## Hard limitations

- Old-period `OI` must not be backfilled through Binance REST: old dates return invalid start errors.
- `OI` is only usable where the current `5m` parquet already contains `open_interest`.
- Historical depth imbalance is not available in the current pipeline.
- Historical taker buy/sell flow is not available in the hourly pump cache.
- `premiumIndexKlines 4h` is a regime layer, not a precision entry-timing layer.

## What this layer is good for

- separating distribution-like shorts from healthier expansion-like pumps
- checking whether a move looks cash-led or leverage-led
- identifying pump families that should not be mixed together in one rule set

## What this layer is not good for

- exact long entry timing on `1m`
- exact intrabar second-wave triggers
- historical order-book reconstruction

## Practical takeaway

Use the market-regime layer to decide **what kind of pump this is**.
Do not use it as a direct entry trigger.

The current promising use is:

- keep searching long execution inside `warm` online re-acceleration
- use the regime layer to reject leverage-heavy / distribution-like environments
- use the regime layer to refine short families where `distribution_or_false_break_shape`
  and leverage pressure line up
