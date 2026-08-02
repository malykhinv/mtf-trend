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

Frozen Stage 0C result (2026-08-02): the mirrored short control rejected a
long-specific recovery effect, and the eligible prior-control 3→6% state was
materially worse than ordinary matched market time in both 25 bps recovery and
signed 48-hour return. The 5→10% and 10→20% primary states failed matched-control
coverage and remain unidentified rather than being promoted from sparse data.
The formal status is `IS_GATE_0C_REJECTS_NAIVE_LONG_EDGE`. This closes the naive
unconditional ladder hypothesis. Further work may only ask whether causal
features predict recovery and tail failure within the anomaly population; it
may not optimize PnL or claim that raw mean reversion is an edge.

## Frozen Stage 1 protocol: causal recovery prediction

```text
protocol freeze: drawdown_ladder_recovery_prediction_20260802_v1
development history: 2025-06-03 through each weekly freeze
internal walk-forward evaluation: 2025-08-04 through 2025-12-31
pristine OOS: 2026, physically unread
population: complete equal-notional long ladder states from frozen Stage 0
positive class: 25 bps cost-adjusted recovery within 48 hours
```

The Stage 1 question is deliberately narrower than profitability:

> At the moment a ladder state becomes observable, can its eventual recovery
> versus non-recovery be predicted from information already available then?

The null hypothesis is that the full causal context has no useful calibrated
weekly walk-forward predictability beyond the state geometry and time of the
fall. Rejecting this null does not establish positive EV.

Every row obeys:

```text
feature_cutoff_time <= snapshot_time < future_start_time
label_resolution_time > snapshot_time
training label_resolution_time < weekly model freeze time
```

The full model uses the complete frozen causal catalog, not a post-hoc shortlist:
ladder/fill geometry, multi-horizon price path and volatility, the complete EMA
fan, quote and trade activity, taker flow, OI, liquidations, BTC state,
coin/BTC correlation and residual return, cross-sectional market breadth,
session context, contract age/data quality, concurrency, and only those prior
coin/state outcomes that had strictly resolved before the current snapshot.
Missing OI, liquidation, or activity data remains explicitly missing and is
paired with availability information; it is never silently imputed as a market
state.

Two model arms are frozen before opening the Stage 1 result:

- `structural_baseline`: 20 geometry, fill-bar, session/time, contract-age, and
  history-quality fields;
- `full_causal`: every admissible model feature in
  `drawdown_ladder_causal_features_v1`.

Both arms use identical rows, weekly freezes, parent-event-exclusive temporal
splits, inverse-parent-event weights, CatBoost settings, and beta-shrunk
isotonic calibration. A candidate row is the prediction key; all rows from the
same symbol/session parent event remain together for splitting and weighting.
The raw symbol identifier is used for diagnostics, not as a model feature;
causal prior-reaction fields represent learnable coin history without arbitrary
symbol memorization.

Absolute full-model gates are frozen as follows:

```text
internal OOS rows >= 20,000
skipped weekly fraction = 0
AUC >= 0.60
log-loss improvement over frozen weekly prevalence >= 0.005
Brier improvement over frozen weekly prevalence >= 0.002
ECE <= 0.05
within-week label-permutation p <= 0.05 for AUC and log-loss gain
probability >= 0.92 cohort: >= 1,000 rows, observed recovery >= 0.92,
  Wilson lower 95% >= 0.90, absolute calibration gap <= 0.05
```

The primary anti-triviality test is the paired full-minus-structural comparison
on exactly identical walk-forward predictions. Required incremental effects:

```text
AUC delta >= 0.01
log-loss improvement >= 0.002
Brier improvement >= 0.001
ISO-week bootstrap 95% lower bound > 0 for every metric
ISO-week sign-flip familywise p <= 0.05/3 for every metric
```

All gates must pass. Failure of any gate leaves the causal-separation hypothesis
unproven and forbids PnL optimization. Results must additionally be broken down
by exact grid state, month, session, symbol, and causal feature family. Coin and
context win/loss traits are explanatory diagnostics only until reproduced in
later frozen weeks; low-support coins may not be promoted as filters.

Stage 1 writes the full feature catalog, missingness, temporal audit, both model
configs, and the paired-comparison config before fitting. No threshold, feature,
state, session, or probability cutoff may be retuned after the result is seen.

### Frozen Stage 1 result

Status: `IS_CAUSAL_CONTEXT_INCREMENTAL_HYPOTHESIS_REJECTED`.

```text
feature rows: 214,992
complete labels: 213,528
internal weekly OOS rows: 168,102
parent events: 106,922
source perpetual symbols: 796; symbols with filled states: 609
model features: 263 declared; 15 liquidation derivatives unavailable/all-missing
weekly models: 22/22 frozen and scored
temporal audit: PASS
OOS 2026 rows read: 0
```

The full causal arm passed every absolute prediction gate: AUC 0.712, log-loss
gain 0.0314, Brier gain 0.00283, ECE 0.0215, and both within-week permutation
p-values 0.001. Its probability-at-least-0.92 cohort contained 127,201 rows and
recovered 95.67%, with a Wilson lower bound of 95.55%.

The pre-registered structural control was better: AUC 0.744, log loss 0.2255
versus 0.2353, and Brier 0.05950 versus 0.06007. Frozen paired full-minus-
structural inference was:

```text
AUC delta: -0.03188; ISO-week bootstrap 95% CI -0.04942 .. -0.00262
log-loss improvement: -0.00989; 95% CI -0.01961 .. -0.00124
Brier improvement: -0.000566; 95% CI -0.001831 .. +0.000426
all 9 incremental gates: FAIL
```

Therefore recovery is predictable from the shape, depth, and timing of the fall,
but the broad EMA/activity/OI/BTC/breadth/event-memory context does not add
stable weekly information and degrades the frozen prediction. Absolute success
of the full arm cannot override failure against the negative control.

One implementation audit found that the frozen high-probability gate was 0.92
while the display reliability list ended at 0.90. The original gate lookup would
therefore fail mechanically. Core was corrected to always include every gate
threshold; immutable amendment artifacts reused identical frozen predictions,
metrics, null tests, and input hashes. No model or gate value changed.

Post-gate diagnostics are explanatory only. Failures were diffuse across 594 of
605 evaluated symbols; the largest symbol contributed 1.24% of all failures,
the top ten 7.86%, and the top fifty 23.52%. Among 450 symbols with at least 100
states and at least ten outcomes in each class, the full model improved AUC for
18.9%, log loss for 22.7%, and Brier for 37.6%. It was worse in four of five
months and four of five UTC sessions. Losses were associated with deeper falls,
more filled levels, later session progress, and greater distance below blended
entry. These are viewed-IS associations and may not be converted into filters.

This result closes the broad-context rescue hypothesis. Gate 3 EV, execution,
TP/SL, leverage, and portfolio simulation remain unauthorized for this version.

### Frozen post-selection structural-protection EV experiment

The structural arm passed absolute weekly prediction gates and beat the full
causal arm. That observation motivates a new, separately versioned experiment;
it does not retroactively turn Stage 1 into trading evidence. The experiment is
explicitly post-selection IS evidence and requires forward confirmation even if
all its gates pass.

The decision is made only after a stressed ladder fill bar closes. On the
already frozen weekly out-of-fold structural probability:

```text
probability >= 0.92: hold the blended inventory to the 48h terminal mark
probability < 0.92: exit at the snapshot close
baseline: hold every state to the same 48h terminal mark
```

No probability, return, state, cost, or exit threshold may be searched after
the EV rows are opened. The 0.92 cutoff comes from the previously frozen Stage
1 reliability gate. The exact states are the three previously frozen stressed
states: 3% grid through 6% as primary, with 5% through 10% and 10% through 20%
reported as secondary sensitivity states. The primary cost buffer is 25 bps
round trip; 10 and 50 bps are sensitivity reports only.

Primary endpoints are mean 25 bps-net policy return from blended entry and mean
policy-minus-unconditional-hold return. Selection-adjusted one-sided alpha is
`0.05 / 2 viewed model arms / 2 endpoints / 3 viewed states = 0.0041667`.
Twenty thousand cluster bootstrap draws are run separately by ISO week and by
symbol; both adjusted lower confidence bounds must exceed zero for both primary
endpoints. Bootstrap tail mass is descriptive and is not called a p-value.

All of the following gates must pass:

```text
primary rows >= 10,000; HOLD rows >= 1,000; EXIT rows >= 500
absolute mean policy EV > 0 at 25 bps
mean improvement over unconditional hold > 0
week- and symbol-cluster adjusted lower bounds > 0 for both endpoints
positive policy mean in at least four calendar months
policy CVaR5 no worse than unconditional hold CVaR5
removing the top 1% of rows still leaves positive aggregate return
```

Every joined row must retain `feature_cutoff <= snapshot < future_start`, use a
weekly model frozen no later than the snapshot, have a complete 48-hour horizon
strictly before 2026, and match the previously frozen recovery target exactly.
The decision is derived before future-return columns are consulted; a mutation
test proves that changing future returns cannot change HOLD versus EXIT.

This gate is not an ex-ante entry strategy and contains no funding history,
physical TP/SL, portfolio capital, concurrency, or liquidation simulation. A
pass authorizes design of those mechanics under structural anchors; it does not
establish a deployable edge or authorize access to 2026. A fail closes this
structural-protection branch without retuning it on the same rows.

### Frozen structural-protection EV result

Status: `IS_STRUCTURAL_PROTECTION_EV_GATES_FAIL`.

The causal join produced 27,560 immutable decision rows. All horizons ended
before 2026, every weekly model was frozen before its decision, the temporal
audit passed, and no 2026 row was read. The primary 3→6 state contained 19,821
rows: 12,228 HOLD decisions and 7,593 protective EXIT decisions.

At the frozen 25 bps cost:

```text
policy mean return: -1.4436%
unconditional 48h-hold mean return: -1.2718%
policy-minus-hold mean: -0.1718 percentage points
policy positive-row rate: 25.59%
positive trading-day fraction: 29.05% across 148 days
policy CVaR5: -32.37%; unconditional-hold CVaR5: -38.13%
positive months: 0/5
top-trade removal fraction to non-positive: 0% (already non-positive)
```

The selection-adjusted lower bounds were negative under both dependence views.
For absolute policy return they were -2.332% by ISO week and -1.917% by symbol;
for improvement over hold they were -1.062 and -0.685 percentage points. The
secondary 5→10 and 10→20 states also had negative means at 25 bps (-1.94% and
-5.79%), and all three states remained negative even at the 10 bps sensitivity.

The protection reduced extreme left-tail loss but worsened average EV. That is
risk transformation, not edge. Because absolute EV, relative EV, inference,
month stability, and concentration gates failed, no TP/SL, execution, leverage,
or portfolio stage is authorized for this policy. The 0.92 cutoff and exact
states may not be retuned on these rows. The 2026 partition remains untouched.

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

.venv\Scripts\python.exe main.py build-drawdown-ladder-stage1 `
  --source-dir .output/market/binance_vision/um_futures/enriched_1m `
  --stage0 .output/research/drawdown_ladder/stage0_is `
  --out .output/research/drawdown_ladder/stage1_is `
  --workers 4 `
  --max-inflight-symbols 8
```
