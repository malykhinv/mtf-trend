# Recurrent pump-wave short research protocol

This document freezes the first discovery stage before manual review labels or
short-entry outcomes are inspected.

Canonical identity:

```text
strategy_name = recurrent_pump_wave_short_v1
strategy_family = pump_wave_short
candidate_schema_version = recurrent_pump_wave_candidate_v1
source_online_schema_version = pump_fade_online_state_v4
live_trading_strategy = false
```

## Plain-language hypothesis

A first activity anomaly attracts attention and lifts price. A second or third
causally detected anomaly in the same coin and recurrence episode may represent
another distribution wave. The strategy will eventually consider a short only
after that later wave and only after a separately defined causal bearish
structure signal. It does not short merely because price rose or because an
anomaly ordinal is two or three.

## Scientific stages

1. Validate whether the machine's second/third anomaly-wave candidates match
   human-visible second/third waves.
2. Freeze the admissible causal short-signal family using only reviewed 2025
   development examples.
3. Test whether the signal predicts future dump nature and whether prediction
   becomes available early enough.
4. Only after prediction, timing, and conditional EV pass may structural trade
   simulation, fees, slippage, funding, and account risk be evaluated.

Failure at any stage stops every downstream stage. Manual review cannot delete
unattractive candidates after their future path is seen; reviewed negatives are
part of the evidence.

## Stage 0 recurrence-wave contract

The source is the already materialized causal `pump_fade_online_state_v4`
artifact. The builder reads an explicit allowlist of online columns and rejects
label, resolution, future-peak, event-end, and outcome columns. It selects one
row per source event: the first causally qualified new-high state marked
`is_nature_anchor=true`.

The existing source builder defines `recurrence_chain_id` as a causal connected
component for the same symbol: a new qualified event joins the preceding chain
only when its ignition is no more than 48 hours after the preceding qualified
event. No future event is needed to assign an existing event to its chain.

Within each chain, events are ordered by ignition time. The first event is wave
one, the next is wave two, and the next is wave three. At this discovery stage
`wave` means recurrence ordinal of a qualified anomaly event; it is deliberately
not yet claimed to be the final economic wave definition. Human review must
measure how often that operational definition matches visible wave morphology.

The complete causal population contains every ordinal-two and ordinal-three
event in 2025. The desk queue is a reproducible, outcome-blind sample of at most
30 rows per `(calendar month, wave ordinal)` stratum, selected by a frozen hash
of the immutable source event id. The complete population remains an artifact,
so sampling cannot hide detector coverage or symbol concentration.

Charts may display up to four hours before the first wave and 24 hours after the
current candidate snapshot. That future tail is presentation-only. It cannot
change membership, wave ordinal, stored causal features, or sampling hash. The
candidate materializes `selection_snapshot_time_ms` and
`feature_cutoff_time_ms`, with the invariant:

```text
feature_cutoff_time_ms <= selection_snapshot_time_ms < review_end_ms
```

The review tail is clipped strictly before 2026-01-01.

## Manual-review question

For each desk card, the reviewer answers only:

> Does the marked ordinal-two/three recurrence look like a genuine second or
> third pump wave of the same market episode?

`Save` with quality `good` means yes. `No setup` means no. Notes may describe
why: unrelated anomaly, one continuous wave split twice, wrong base, weak
reactivation, already completed dump, data defect, or another explicit reason.
The pre-drawn zigzag is an aid and may be corrected, but corrections never
rewrite the detector artifact.

No short signal, winner/loser label, TP, SL, future return, or PnL is part of
Stage 0.

## Time partitions and holdout status

Development candidates are restricted to snapshots before 2026-01-01. No 2026
row may enter Stage 0 artifacts.

The BULLA-2026 example was known when this hypothesis was proposed. Therefore
2026 cannot honestly be called a pristine holdout for this strategy, even if it
is excluded from development. It may later be reported only as known subsequent
evidence under a frozen protocol. A `PRISTINE_VERIFIED` claim requires market
data first observed after this protocol and all subsequent signal/model choices
are frozen.

## Mandatory Stage 0 audits

- Source schema is the online-only schema and forbidden future columns are
  absent.
- Feature cutoff never exceeds the candidate snapshot.
- Candidate membership and fields are invariant to mutation or addition of
  rows strictly after the candidate snapshot.
- Wave ordinal is computed only from current and earlier events in the same
  causal recurrence chain.
- Review sampling uses no market outcome or future-path field.
- The review tail ends before 2026 and is never used as a feature.
- Population, desk sample, sampling coverage, symbol concentration, and source
  hashes are retained even when the visual precision is poor.
