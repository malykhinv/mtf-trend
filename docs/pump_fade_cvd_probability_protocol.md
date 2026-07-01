# Pump-fade CVD path probability protocol

Status: frozen before the full lifecycle rebuild and outcome evaluation.

## Question

Does the closed-minute taker-flow trajectory available at a pump-fade decision
snapshot add out-of-sample probability information beyond the registered base
model at the nature anchor, first re-high, or second re-high?

This is a probability experiment. It makes no EV, execution, or actor-identity
claim. Binance taker-buy quote volume is an observable aggressor-flow proxy, not
proof of informed buying, liquidation flow, or a particular participant class.

## Causal contract

Every feature uses only candles from ignition through the closed candle whose
close time is `snapshot_time_ms`. Signed quote flow per minute is
`2 * taker_buy_quote_volume - quote_volume`; CVD is its cumulative sum.
No post-snapshot candle or finalized event peak enters a feature.

The registered family is `pump_fade_cvd_path_v1` and contains exactly the 11
features listed in `research/pump_fade_cvd_probability.json`. Missing or invalid
taker-flow source rows produce NaN features and `cvd_available=false`. A missing
preceding five-minute window leaves `cvd_acceleration_5m` undefined rather than
injecting a sentinel.

## Evaluation

- Variants: nature/T0, registered state ordinal 1, registered state ordinal 2.
- Split: frozen weekly walk-forward, recurrence-chain isolation, frozen weekly
  CatBoost weights and calibrator.
- Comparison: baseline and augmented models use exactly the same complete-case
  rows. The augmented model adds only the registered CVD family.
- Family-wise alpha: 0.05 across the three variants.
- Incremental minima: AUC +0.01, log-loss improvement +0.002, Brier improvement
  +0.001, together with all absolute prediction gates.
- Reliability at `p >= 0.70` remains governed by the base probability protocol.

No feature selection, threshold change, or reformulation is allowed after
viewing full-period outcomes under this freeze ID. A changed feature family is a
new protocol and a new experiment.
