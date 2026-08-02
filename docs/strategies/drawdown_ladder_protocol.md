# Blind drawdown ladder — scientific protocol

## Status

```text
strategy_family = drawdown_ladder
stage = 0_raw_recovery_nature
protocol_version = drawdown_ladder_stage0_v1
protocol_freeze_id = drawdown_ladder_stage0_20260802_v1
research_partition = IS only
IS = 2025-06-03 .. 2025-12-31
OOS = 2026, physically unread
```

Stage 0 is an event study, not a backtest and not evidence of a profitable
strategy. It asks whether a resting buy limit hit by an extreme session decline
subsequently recovers, how quickly it recovers, and how much further adverse
excursion occurs first.

## Scientific question

Primary question:

> Conditional on a causally placed, conservatively filled buy limit at depth
> `d`, what is the censor-aware distribution of time until price recovers the
> entry price (gross and after explicit cost buffers) during the next 48 hours?

Secondary questions:

- how recovery probability and latency change with depth;
- whether an equal-notional 3%, 5%, or 10% ladder state recovers its blended
  average price;
- how MFE, MAE, and terminal return change across horizons;
- how much evidence is censored by data gaps, symbol-history termination, or
  the locked IS boundary.

Null hypothesis:

```text
After conservative fill semantics and censoring, recovery behavior does not
improve economically with depth and does not support positive net EV.
```

Stage 0 does not select TP, optimize PnL, fit CatBoost, or filter on future
outcomes.

## Causal session anchor

The existing versioned UTC liquidity-session calendar is used:

```text
ASIA     00:00–08:00 UTC
EU       08:00–13:00 UTC
OVERLAP  13:00–16:00 UTC
US       16:00–21:00 UTC
LATE     21:00–24:00 UTC
```

For every symbol/session:

1. `anchor_price` is the close of the exact minute immediately before the
   session boundary;
2. `feature_cutoff_time = anchor_snapshot_time = session_start`;
3. orders are not eligible during the first session minute;
4. they become active after a frozen one-minute placement delay;
5. unfilled orders expire at the session end;
6. the anchor never moves after the session begins.

The one-minute delay prevents an impossible same-bar assumption at the session
boundary.

## Measurement levels and grid families

The build-once measurement lattice is every integer depth from 3% through 90%.
It is not the live order grid. It allows later analysis without rescanning 346M
minute rows.

Three pre-registered equal-notional ladder families are projected as causal
state rows:

```text
3% step:  3, 6, 9, ... 90
5% step:  5, 10, 15, ... 90
10% step: 10, 20, ... 90
```

A ladder-state row is emitted only when its filled-level set changes. Its
average entry is the harmonic equal-notional average of levels filled as of
that snapshot. Later deeper fills do not alter an earlier state row.

All levels belonging to one symbol/session share `parent_event_id`. Level and
state rows are dependent observations; inferential work must cluster by parent
event and market shock.

## Conservative fill semantics

A minute low merely touching the displayed limit is insufficient. The low must
trade through the limit by the frozen 5 bps buffer.

The fill is observed only at the fill bar close. No high, low, or close from
that same bar enters the recovery outcome. The first admissible future price
observation is the close/extent of the following full minute:

```text
feature_cutoff_time <= snapshot_time < future_start_time
```

Candidate and outcome artifacts are physically separate so later outcomes
cannot influence membership.

## Recovery outcomes

For an individual limit, break-even is measured from its limit price. For an
equal-notional ladder state, it is measured from the causal blended average
entry.

Registered break-even thresholds:

```text
gross: entry price
10 bps: entry × 1.0010
25 bps: entry × 1.0025
50 bps: entry × 1.0050
```

These are outcome coordinates, not physical TP policies.

Registered path horizons:

```text
5m, 15m, 1h, 4h, 12h, 24h, 48h
```

Each outcome records:

- whether break-even was observed;
- minutes until first recovery;
- available future minutes;
- censor reason;
- close return at each complete horizon;
- maximum and minimum return at each complete horizon.

## Censoring

Rows are never deleted merely because a full 48-hour tail is unavailable.
Possible censor reasons are:

```text
none
path_gap
symbol_history_ended
is_boundary
```

Summary artifacts report both:

- a conservative observed recovery rate, treating unresolved rows as not
  recovered;
- a Kaplan–Meier time-to-recovery estimate.

No non-informative-censoring claim is made. Symbol-history termination is
reported separately because delisting/death is likely informative.

## Artifacts

```text
level_candidates.parquet
level_outcomes.parquet
ladder_state_candidates.parquet
ladder_state_outcomes.parquet
level_recovery_summary.parquet
level_session_recovery_summary.parquet
ladder_state_recovery_summary.parquet
temporal_audit.json
recovery_summary_report.json
manifest.json
```

The raw artifacts preserve build-once/analyze-many. Filters, regime splits,
CatBoost, portfolio constraints, and execution simulations must consume these
artifacts or later causal feature enrichments; they must not rescan and redefine
candidate membership opportunistically.

## Stage gates

### Gate 0A — temporal/data validity

- physical IS predicate proves no 2026 row was read;
- candidate keys are unique;
- candidate/outcome keys match one-to-one;
- feature cutoff does not exceed snapshot;
- every future path begins after snapshot;
- same-session levels share the parent event;
- future-tail mutation cannot change earlier candidate fields.

### Gate 0B — raw phenomenon

Require adequate parent-event counts and a stable depth-response surface. A
single profitable depth, symbol, or week is not evidence.

Frozen Stage-0 result (2026-08-02): the temporal audit passed on 796 perpetual
symbols with 347,866 individual filled levels, 214,992 equal-notional ladder
states, and 106,922 parent symbol/session events. Individual limits commonly
revisited their entry, but blended inventory recovered materially less often and
showed meaningful month dependence. The status is
`IS_DESCRIPTIVE_PHENOMENON_ONLY_NO_EDGE_CLAIM`.

Before Gate 1 feature selection, Stage 0C must run two outcome-blind controls:

- matched non-drawdown observations sampled without using future outcomes;
- the mechanically mirrored rally/short experiment under the same activation,
  trade-through, horizon, censoring, and cost contracts.

These controls test whether the measured path is specific to drawdown rebound
rather than ordinary volatility or a generic limit-order selection effect. All
inference must account for dependence across levels and adjacent sessions using
symbol/time-block clusters. Neither control may read or tune on 2026.

### Gate 1 — causal context and protection mechanisms

Attach only as-of features, including market panic/breadth, BTC support,
pump-contamination, pre-pump base distance, OI change, activity, liquidity,
session-normalized volume, dump geometry, contract age, and leak-safe prior
recovery history. Test each protection by ablation before interactions.

### Gate 2 — weekly walk-forward prediction

CatBoost uses the full admissible feature picture, frozen weekly weights and
calibration, event-exclusive folds, and no 2026 data. Targets concern recovery
nature/timing, not optimized trade PnL.

### Gate 3 — EV and execution

Only after calibrated predictability passes: structural exit policies,
partial fills, fees, funding, slippage, minimum notional, shared capital,
maximum concurrent positions, market-panic throttles, and parent-event risk
budgets.

### Gate 4 — portfolio simulation

Report risk per parent trade at 2%, 3%, 4%, and 5%, initial equity $1,000,
win rate, trade count, drawdown, costs, positive trading days, concurrency,
capital utilization, symbol/week concentration, and the percentage of top
trades whose removal makes total PnL non-positive.

### Gate 5 — frozen OOS

Only after the complete IS protocol, parameters, feature schema, model, and
execution rules are frozen may 2026 be accessed once.

## Reproduction

```powershell
.venv\Scripts\python.exe main.py build-drawdown-ladder-stage0 `
  --source-dir .output/market/binance_vision/um_futures/enriched_1m `
  --out .output/research/drawdown_ladder/stage0_is `
  --workers 6 `
  --max-inflight-symbols 12
```
