# Residual response + OI absorption protocol v1

## Status

Pre-registered before feature discovery and modelling. The purpose of the
research is to try to falsify two related mechanisms, not to optimize PnL.

## Locked calendar boundary

- IS: 2025-06-03 00:00 UTC through 2025-12-31 23:59:59.999 UTC.
- OOS: 2026-01-01 00:00 UTC through 2026-06-17 23:59:59.999 UTC.
- IS input views have an exclusive maximum timestamp of 2026-01-01 00:00 UTC.
- OOS may not be read for distributions, feature choice, thresholds, models,
  calibration, execution parameters, or report layout before protocol freeze.
- All tuning and weekly walk-forward validation happen inside IS.

The non-analytic 2026 schema-inspection incident is disclosed separately in
`residual_absorption_oos_access_log.md`. It changed no registered definition.

## Strategy in plain language

After a broad market impulse, a coin may react less or more than its own causal
history and the current cross-section imply. The first mechanism asks whether a
temporarily lagging coin later catches up with the factor move. The second asks
whether aggressive local flow and rising OI are being absorbed, leaving newly
opened positions trapped. These mechanisms are evaluated separately before any
combined policy is allowed.

The model estimates future-state probabilities. It does not receive a direct
PnL target and does not emit an unconstrained buy/sell instruction.

## Registered stage-1 definitions

The broad factor is the median 15-minute return of point-in-time core-eligible
alt perpetuals, excluding BTC and ETH. Its causal baseline contains only the
previous 20 completed sessions of the same type. The primary impulse threshold
is an absolute robust z-score of 3.0, at least 60% directional breadth, and at
least 30 alt symbols. Z-score variants 2.5/3.5 and breadth variants 50%/70% are
registered sensitivity arms, not hidden retuning. BTC/ETH agreement is retained
as a separate confirmation variable so its incremental value can be tested.

For every event/symbol, the factor is recomputed leave-one-out. A 15-minute
alpha, beta, correlation, and R-squared are estimated only from prior completed
same-type sessions and frozen at the event. The online residual is observed
return minus the frozen expected return. Positive direction-adjusted
underreaction means the coin moved less in the impulse direction than expected.

Future response outcomes are residual changes after the event at 15/30/60/120
minutes using event-time-frozen beta and factor membership. They are research
labels, never online features. A prior outcome enters memory only after its
120-minute resolution time. Unresolved events may contribute counts but cannot
expose catch-up, zero-cross, terminal-residual, or timing fields.

### Registered stage-1 falsification report

This report was locked after event-time profiles were built but before any
future response path was materialized. The primary population is every causal
candidate profile with a finite frozen beta and a fully observed 120-minute IS
path. No outcome-dependent trimming, winsorization, symbol removal, or minimum
underreaction threshold is permitted in the primary arm.

The primary predictor is direction-adjusted underreaction at the event. The
primary outcome is direction-adjusted residual catch-up after 60 minutes. The
two co-primary estimands are (a) Spearman association between underreaction and
60-minute catch-up and (b) median 60-minute catch-up among observations with
strictly positive underreaction. Confidence intervals are obtained by UTC-day
cluster bootstrap so coins observed in the same market event are never treated
as independent evidence. Symbol-cluster intervals are a registered dependence
sensitivity check.

The stage-1 mechanism passes only if both co-primary point estimates have the
predicted positive sign and both 95% UTC-day-cluster confidence intervals have
strictly positive lower bounds. The 15/30/120-minute horizons, zero crossing,
time to half catch-up, direction, session, BTC/ETH confirmation, calendar month,
and underreaction deciles are secondary diagnostics. Horizon p-values, if
reported, use Holm correction. Failure is retained as a valid negative result;
subgroup success cannot rescue a failed primary arm.

Passing this gate establishes conditional residual-response predictability,
not tradable edge. It does not authorize a threshold, CatBoost, PnL simulation,
or a claim that costs can be overcome.

### Registered post-primary robustness and win/loss trait analysis

This extension was registered after the stage-1 primary result was known and
before the robustness, placebo, or trait reports were run. It is therefore an
IS strengthening exercise, not a second independent confirmation of the
original hypothesis.

The robustness report adds equal-event weighting, deterministic non-overlap of
120-minute event windows, leave-one-month-out estimates, outcome concentration,
and within-event outcome permutation. A robust result must retain the predicted
sign under equal-event weighting and non-overlapping events, must not be made
positive by one calendar month, and its observed association must exceed the
95th percentile of the within-event permutation null. These checks cannot alter
the registered primary population or replace a failed primary result.

At this stage a `response win` means strictly positive direction-adjusted
60-minute residual catch-up; a `response loss` means zero or negative catch-up.
These are research labels, not executed trades and not net-of-cost wins/losses.

Common win/loss traits are evaluated only from event-time causal features.
Identifiers, future paths, outcome-memory fields unresolved at the snapshot,
and every post-event value are forbidden as trait inputs. Numeric traits are
reported by robust win/loss medians, rank-biserial effect, missingness, and
calendar-month effects with Benjamini-Hochberg correction across the registered
feature family.

Temporal stability is assessed forward: for each month from August through
December 2025, the expected feature direction is estimated from all prior IS
months and checked only on the next month. A trait is called stable only when
it has at least 80% causal coverage, its forward sign agrees in at least four of
five checks, and its sign is non-zero in at least six of seven individual
months. Continuous traits remain continuous. This report may prioritize
feature-family ablations and monotonic constraints, but it may not manufacture
an outcome-derived trading threshold or filter the training population.

### Registered stage-2 coarse local-activity grid

This feature grid was locked after the post-primary stage-1 trait report and
before local-flow/OI features were associated with response outcomes. For each
already selected event/symbol, complete one-minute bars in the trailing
5/15/30-minute windows ending at the selection snapshot are used. No row whose
close is after the snapshot is admissible.

Each window records log return, quote volume, trade count, signed taker quote
volume, taker imbalance, high-low range, realized one-minute volatility, path
efficiency, return per million quote volume, OI start/end/absolute/relative
change and availability coverage, long/short liquidation volume, liquidation
imbalance, and missingness. Raw components are retained so CatBoost does not
depend on a hand-crafted absorption formula.

The 15-minute primary window additionally reports price/flow alignment,
direction-adjusted taker imbalance, direction-adjusted OI change, OI change per
unit signed flow, and low-impact aggressive-flow magnitude. Divisions use an
explicit data-scale floor and expose a separate denominator-validity flag; no
silent fallback or infinity replacement is allowed.

Stage-2 features are joined to the causal feature matrix but outcomes remain in
a physically separate label artifact. Win/loss trait stability uses the same
forward-month rules above. High-resolution enrichment remains a later paired
ablation and cannot change the coarse population.

### Registered stage-2 event-scoped aggTrades grid

This grid was locked after the coarse local-activity trait report and before
aggTrades features were associated with response outcomes. The immutable
request population is every already selected event/symbol. Each request covers
the trailing 15 minutes ending at, but not including, the coarse selection
snapshot. The request record retains the original event id, symbol, snapshot,
feature cutoff, five-minute selection granularity, raw-trade source, and exact
requested interval. Archive availability cannot add, remove, or re-time a
coarse event.

Raw aggTrades are projected into closed-minute microstructure summaries and
then aggregated over fixed 5/15-minute windows. Registered families are trade
intensity and its minute burstiness; trade-size p50/p95/p99 and tail ratios;
top-one-percent notional share, notional Gini and large-trade frequency;
same-aggressor run length, sign entropy and side-flip rate; inter-arrival time
and dispersion; buy/sell price impact; impulse-aligned versus adverse impact;
and 5-minute versus 15-minute acceleration. Missingness and exact minute
coverage are explicit features. No outcome-derived threshold or symbol list is
allowed.

The primary high-resolution arm requires all 15 closed minutes. Partial and
missing archive rows stay in the coarse experiment and receive explicit
coverage flags; they are never silently deleted or imputed. Because the local
archive was collected for a different strategy and its coverage is not random,
coverage must be reported by month, session, impulse direction, symbol and
causal coarse covariates. High-resolution value is tested only as a paired
coarse-only versus coarse-plus-aggTrades ablation on identical covered rows.
The result is exploratory IS evidence and cannot rescue a failed coarse model.

### Registered paired weekly walk-forward aggTrades ablation

The paired model ablation was locked after the univariate aggTrades report and
before any weekly CatBoost predictions were generated. It is an internal IS
walk-forward exercise, not the untouched 2026 OOS. Development begins on
2025-06-03; frozen weekly evaluation begins on Monday 2025-08-04 and ends
before 2026-01-01. A label may train a weekly model only when its 120-minute
resolution time is strictly earlier than that week's Monday freeze. Models and
calibrators are frozen for the complete ISO week.

Both arms use exactly the rows with complete trailing-15-minute aggTrades
coverage. The baseline contains every numeric causal stage-1, causal symbol
memory, and registered one-minute local-activity feature plus registered coarse
categoricals. The augmented arm adds all non-missingness aggTrades measurements
from the locked 5/15-minute grid. Symbol identity, ex-post symbol diagnosis,
future outcomes, calendar month labels, and context winners are forbidden model
features. Event recurrence chains whose event gaps are no more than 120 minutes
are kept exclusive across a weekly test fold and its training history.

Each arm uses the shared binary weekly walk-forward engine, CatBoost log loss,
event-time chronological 60/20/20 fit/validation/calibration partitions,
early stopping, and quantile-binned beta-isotonic calibration. CatBoost and the
calibrator are fit once per ISO week. The target is positive direction-adjusted
60-minute residual catch-up; it is not an executed or net-of-cost trade label.

Incremental aggTrades value is accepted only if the paired arm improves AUC by
at least 0.01, log loss by at least 0.002, and Brier score by at least 0.001;
for all three metrics the ISO-week cluster-bootstrap 95% lower bound must be
positive and the week-level sign-flip familywise p-value must not exceed 1/60.
All comparisons use identical prediction keys and weekly freeze times. Failure
means aggTrades are rejected from the response-probability model rather than
retuned on the same IS predictions.

Observed after this freeze: all 22 weeks and 19,487 identical prediction rows
were scored in both arms. Coarse AUC was 0.507273 and augmented AUC was
0.509071; delta 0.001797 had ISO-week bootstrap 95% CI
[-0.004849, 0.009310] and sign-flip p=0.28. Augmented log loss and Brier score
were slightly worse. All nine incremental gates failed, so aggTrades are
rejected from this model family without retuning.

### Registered full-population coarse weekly walk-forward

The full-population coarse WFA was locked after rejection of the paired
aggTrades arm and before any full-population CatBoost predictions were made.
It uses all causal coarse event/symbol rows rather than the non-random
aggTrades-covered subset. Calendar boundaries, recurrence-chain isolation,
feature families, CatBoost settings, calibration, and absolute probability
gates remain exactly those registered for the coarse paired arm.

Because market impulses contain different numbers of symbols, CatBoost fit and
calibration weights are normalized by market event: every event has equal
total weight irrespective of its cross-section size. The event/symbol row id
remains unique for prediction pairing, while the separate weight-group field is
the market event id. This is a strategy-neutral capability of the shared binary
WFA engine and is covered by its contract tests.

The full coarse probability hypothesis passes only if every preregistered
absolute gate passes, including AUC at least 0.60, log-loss improvement at least
0.005, Brier improvement at least 0.002, ECE at most 0.05, permutation tests,
and high-probability reliability. Failure stops progression to EV, execution,
SL/TP, PnL, and the untouched 2026 OOS; it cannot be rescued by symbol or
context cherry-picking.

Observed after this freeze: all 22 weeks produced 77,635 internal-IS
walk-forward predictions with event-normalized training weights. AUC was
0.523367, log-loss improvement over the frozen weekly baseline was 0.000937,
Brier improvement was 0.000468, and ECE was 0.002600. The two permutation
tests passed, showing a detectable non-random association, but all three
minimum-effect gates failed and no calibrated prediction reached 0.60. The
full coarse probability hypothesis therefore fails. This research branch stops
before EV, execution, structural SL/TP, PnL simulation, and 2026 OOS access.

### Registered symbol reliability and context attribution

This post-primary analysis distinguishes ex-post diagnosis from online memory.
The ex-post table may explain the IS sample but can never become a static symbol
whitelist or blacklist. Online rows may use only outcomes whose 120-minute
resolution time is no later than the current snapshot.

A symbol diagnosis requires at least 30 resolved responses. `Reliable` requires
a positive median 60-minute catch-up and a 95% Wilson lower bound for win rate
above 50%. `Poor` requires a negative median and a Wilson upper bound below 50%.
`Context-dependent` requires neither label globally and at least two registered
contexts with 15 or more observations and opposite median signs. All remaining
eligible symbols are `noisy`; symbols below 30 observations are `insufficient`.
Reports include the share of symbols, rows, wins, positive catch-up, absolute
outcome mass, and event coverage in each diagnosis.

Registered contexts are impulse direction, session, calendar month, core versus
activity-expansion channel, BTC/ETH confirmation, underreaction quintile,
factor-R-squared tercile, and current-activity-ratio tercile. Numeric bins depend
only on causal feature distributions, not outcomes. Context contrasts describe
association and must not be called causal effects.

Online symbol/context memory uses the most recent 5/10/20 resolved responses,
Jeffreys-prior posterior win probability, 10/90% posterior bounds, median and
MAD catch-up, and direction/session/direction-session subsets. Missing history
is retained explicitly. No current or unresolved outcome may enter these
features.

## Stage order and gates

1. Data, time, sessions, point-in-time universe, and enrichment provenance.
2. Market impulse and historical response memory, without ML or PnL.
3. Local activity, taker-flow, price-impact, and OI absorption decomposition.
4. Weekly walk-forward prediction and probability calibration inside IS.
5. Decision timing and conditional EV after conservative costs.
6. Structural trade simulation and portfolio accounting.
7. Protocol freeze, forensic audit, and one OOS replay.

Every passed stage is followed by a lookahead audit. Failure invalidates that
stage and every downstream artifact. A negative hypothesis result is retained.

## Session contract and universe

The shared `utc_liquidity_sessions_v1` calendar is used:

- ASIA 00:00-08:00 UTC
- EU 08:00-13:00 UTC
- OVERLAP 13:00-16:00 UTC
- US 16:00-21:00 UTC
- LATE 21:00-24:00 UTC

There are two separately reported universe channels:

1. Core liquidity: ranked by the median of completed prior sessions of the same
   type. No part of the current unfinished session enters this rank.
2. Activity expansion: current quote volume through the snapshot is compared
   with the same elapsed slice of prior same-type sessions. Current-session
   bars after the snapshot are unavailable.

Trailing 24-hour quote volume is a control variable, not the primary universe
definition. Core, expansion, their intersection, and their union are reported
separately.

## Event-scoped 1-second or aggTrades enrichment

An event must first be selected using only causal 1m/5m inputs. The immutable
selection record stores event id, symbol, selection snapshot, feature cutoff,
coarse granularity, and schema version. Only then may 1s candles or aggTrades be
requested for that symbol and event window.

Stored enrichment may include a tail used by later decision snapshots. Every
feature calculation must take an explicit as-of view requiring both event time
and data-availability time to be no later than its decision snapshot. High-
resolution data cannot add, remove, or re-time the original event selection.

High-resolution features and their missingness are evaluated as a paired
ablation. Events without archive coverage remain in the coarse-data experiment;
they are not silently discarded.

## Structural SL/TP distance floor

Physical stops and targets must be anchored to point-in-time structure. Fixed
percent and ATR-multiple levels are forbidden.

The primary noise floor is the causal 90th percentile of absolute one-minute
movement from the previous 20 completed sessions of the same type. The 75th and
95th percentiles are registered sensitivity variants. The target floor is the
maximum of the noise floor and the causal 95th-percentile round-trip execution
cost estimate. If a structural anchor lies inside its applicable floor, the
trade is rejected; the anchor is never moved to make the trade admissible.

## Mandatory lookahead tests after each stage

- Future-tail mutation invariance.
- Batch versus point-in-time recomputation equality.
- Current-session truncation at each snapshot.
- Availability-time enforcement for candles, OI, and enrichment.
- Leave-one-out construction for cross-sectional factors.
- Resolved-only historical outcome memory.
- Event and recurrence-chain split isolation.
- Physical IS-only input boundary before OOS freeze.

## Final reporting contract

Report gross and net win rate, trade count, maximum drawdown, commissions,
slippage, funding, positive active days, positive calendar days, concentration
by symbol/session/month, and the smallest percentage of top trades whose
path-dependent removal makes final net PnL non-positive. Simulate risk budgets
of 2%, 3%, 4%, and 5% from a USD 1,000 initial account, subject to one-times
portfolio notional exposure. Report all registered ablations and failed arms,
not only the best result.
