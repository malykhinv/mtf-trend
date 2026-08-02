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

#### Frozen Stage 0C mirrored-control contract

Protocol freeze: `mirrored_rally_stage0_20260802_v1`.

The mirror changes direction only. At every identical versioned session anchor:

- short limits are placed at `anchor * (1 + depth)`;
- the first session minute remains ineligible;
- a fill requires the one-minute high to trade 5 bps beyond the limit;
- registered depths, 3/5/10% grid families, equal-notional sizing, horizon,
  cost buffers, censoring, symbol universe, and IS boundary are unchanged;
- recovery occurs when a strictly future low reaches the short entry or the
  cost-adjusted price below it;
- future returns and extrema are signed from the short position's perspective.

The primary mirror comparison is made separately for every exact registered
`grid_step_pct x deepest_filled_level_pct` stratum. It reports the long-minus-
mirror difference in conservative 25 bps recovery probability and mean signed
48-hour return. Dependence is handled through symbol x ISO-week clusters;
calendar-month and symbol stability are mandatory. Strata are not pooled and a
single favorable depth cannot pass the gate. The mirror is a directional
negative control, not a claim that long and short event populations are paired.

The mirror code and tests must be frozen before its full-IS result is opened.
After opening, only bug fixes that invalidate and rerun the entire mirror are
allowed; neighboring thresholds or depths may not be added.

#### Frozen Stage 0C prior non-drawdown matched-control contract

Protocol freeze: `prior_non_drawdown_control_20260802_v1`.

For every filled long ladder state, the control search is restricted to the
same symbol, UTC session, and exact elapsed session minute. A control must:

- precede the signal by at least the complete 48-hour response horizon;
- lie within the prior 30 calendar days;
- have no 3% drawdown/trade-through from its own frozen session anchor;
- use an entry at the completed control bar close;
- have a future path beginning only with the next complete minute.

Nearest-neighbor selection uses only causal 60-minute realized volatility,
60-minute quote volume, and absolute 60-minute return. Fixed calipers are a
maximum 4x volatility ratio, 10x quote-volume ratio, and three percentage-point
absolute-return difference. The fixed match score weights are 1.0, 0.5, and
0.5 respectively. Ties resolve by the earlier control timestamp. Future return,
high, low, recovery, or censoring never participates in membership or scoring.

Controls that have no prior pool or fail a caliper remain unmatched; there is no
fallback and coverage is reported by symbol and exact grid state. The primary
paired strata remain 3→6%, 5→10%, and 10→20%. Paired recovery and signed-return
differences are evaluated with symbol×ISO-week and symbol cluster resampling,
Holm correction across the three frozen primary states, match-quality/reuse
diagnostics, and calendar-month stability. A low-coverage primary stratum cannot
support an edge claim.

Missing or non-finite raw `quote_volume` is never imputed. Every trailing
60-minute feature window containing such a row is unavailable, so affected
signal/control snapshots cannot enter a match; raw missing-row counts are
written to the build manifest and symbol coverage artifact. Negative finite
quote volume is a hard data error.

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
