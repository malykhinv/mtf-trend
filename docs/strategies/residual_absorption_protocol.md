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
