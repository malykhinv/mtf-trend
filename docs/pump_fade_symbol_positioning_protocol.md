# Pump-fade event-scoped symbol positioning protocol

## Hypothesis

Positioning metrics for the pumped symbol itself may add information unavailable
in BTC/ETH reference context. The registered mechanism is limited to:

- current top-trader account and position crowding;
- global account crowding;
- taker long/short volume balance;
- 15m and 60m causal changes;
- change from the latest metric available at event ignition;
- top-trader/taker divergence from the global account ratio.

The protocol does not identify actors and does not interpret aggregate ratios as
proof of short covering, liquidation, or informed trading.

## Event-scoped acquisition

Generic Core derives the exact symbol/date scope from registered nature and
state-lattice rows. For every row it retains enough history for the longest
registered change plus the maximum as-of age. Daily Binance metrics archives are
downloaded into atomically written per-symbol artifacts.

Interrupted acquisition may resume only when the stored per-symbol scope exactly
matches the frozen scope. Symbols containing a failed day are rebuilt. HTTP 404,
network failure, parse failure, and invalid ratio rows remain distinct statuses.

## Data-quality contract

```text
available_time_ms = source_time_ms + 5 minutes
available_time_ms <= snapshot_time_ms
ignition_available_time_ms <= ignition_time_ms
as-of maximum age = 10 minutes
ratios must be finite and strictly positive
```

Non-positive ratios are mathematically undefined for log coordinates. Only those
rows are removed, and their count is written to coverage/metadata. They are not
converted to zero, clipped, imputed, or treated as an edge.

## Registered features

For each of account top traders, position top traders, global accounts, and taker
volume:

```text
log ratio
15m log change
60m log change
log change since ignition
```

Three additional coordinates are registered:

```text
top-trader account log ratio - global account log ratio
top-trader position log ratio - global account log ratio
taker log ratio - global account log ratio
```

Total model coordinates: 19. No additional lag, ratio, symbol subset, or threshold
may be selected after viewing OOS results.

## Probability experiment

The augmented and baseline arms use the same complete-case rows, recurrence-chain
split isolation, weekly model freezes, calibration protocol, and T0/ordinal 1/
ordinal 2 variants. Alpha is divided across the three variants. Evidence requires
both incremental paired gates and all absolute prediction/calibration gates.

The completed development experiment was negative. Small positive point estimates
at T0 and ordinal 2 were below minimum effects, their clustered intervals crossed
zero, and no calibrated probability reached 0.70. This family is not admitted to
the production model.
