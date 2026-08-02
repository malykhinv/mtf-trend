# BRK/CAP foundation protocol

Version: `brkcap_foundations_v1_2026-07-12`

Scope is deliberately before prediction, EV, and simulation: establish whether
machine candidates contain the price structures they claim to contain.

## Calibration and blind split

- Calibration evidence: explicitly edited review cases whose symbols begin with
  `A` or `B`.
- Blind confirmation set: `C`–`Z`. Its charts and outcomes are not used to alter
  this version of the rules.
- Review text is reported but never parsed to make a decision. Machine marks are
  judged from OHLC geometry; edited status is established by provenance and an
  exact original-vs-review comparison.

## Frozen definitions

Sleep is the contiguous 120-bar window immediately before pump start, with at
least 24 observed bars. Its full high-low range may not exceed 10%. A proposed
pump start more than 12% below the sleep median is a dump/rebound and is rejected
unconditionally. The pump must advance at least 10%, and its maximum close
drawdown may consume no more than 35% of the start-to-culmination price advance.

Family geometry is disjoint:

- `breakout`: the resistance level starts at the pump culmination and has the
  same price, within 0.5% market-noise tolerance.
- `cap`: the resistance level starts strictly after the culmination/pullback and
  is at least 0.5% below the culmination.

A resistance segment ends at the first later candle whose close is above it.
Later touches cannot revive a level that has already been broken.

These constants are definition-level priors frozen before any C–Z review or any
forward-return inspection. A later change requires a new policy version and a
fresh blind split; it must not overwrite this audit.
