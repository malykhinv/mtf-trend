# Pump-lifecycle short research protocol

This document freezes the visual discovery stage before any dump outcome,
short signal, or PnL is used.

```text
strategy_name = pump_lifecycle_short_v1
strategy_family = pump_wave_short
candidate_schema_version = pump_lifecycle_candidate_v1
protocol_freeze_id = pump_lifecycle_stage0_20250802_v1
live_trading_strategy = false
```

## Hypothesis in plain language

We seek one continuous market episode:

```text
sleep at the pump base -> wave 1 -> sideways -> wave 2
                       -> [sideways -> wave 3] -> distribution/dump
```

The first wave wakes a coin from a quiet base and attracts attention. Later
waves may distribute inventory into that attention. A future strategy may short
only after a separate causal bearish signal. A rise, a third wave, or an EMA
cross alone is not an entry.

The earlier experiment that called separate recurrence events “wave 2/3” is
withdrawn. An event ordinal is not market morphology. All waves in this protocol
are segments of one manually reviewable lifecycle.

## Stage 0: first-wave population and manual morphology

The source is the existing outcome-free `sleep_pump_review` candidate artifact.
That detector first requires a quiet pre-pump base, then proposes the pump start
and a locally confirmed culmination. Its proposal time occurs after the four-bar
culmination confirmation; no later lifecycle candle participates in membership.

This source artifact is a bounded high-score review frame: at most 500 proposals
and at most three proposals per `(symbol, timeframe)`. It is suitable for learning
and validating the wave ontology efficiently, but it is not a representative
sample of every detected anomaly. Stage 0 therefore cannot estimate market-wide
wave prevalence, detector recall, or final strategy opportunity count. Those
require a later frozen full-population rebuild after the wave definition passes
visual feasibility.

The new desk seed contains exactly one `pump` object, shown as wave 1. The expert
may correct or delete it and may draw the same `pump` tool repeatedly. Pump
objects are sorted and numbered W1, W2, W3, ... . They must not overlap.

For adjacent waves, the desk automatically defines:

```text
sideways i = [culmination of wave i, start of wave i+1]
```

Its price bounds are the minimum low and maximum high observed inside that
interval. The manual wave boundaries are primary evidence; the sideways box is
a deterministic derivative saved with the label for auditability.

The initial pilot is a frozen hash sample of at most ten candidates per 2025
calendar month. Hash sampling uses only immutable source event identity. The
complete 494-row eligible population inside this bounded source frame is
retained separately. Cards show six hours before the proposed wave start and up
to 72 hours after its first culmination, clipped
strictly before 2026. At least 24 hours of IS tail must be available. The right
tail is display-only and cannot alter candidate selection or causal fields.

Required invariants:

```text
feature_cutoff_time_ms = proposal_time_ms
feature_cutoff_time_ms <= selection_snapshot_time_ms < review_end_ms
review_end_ms < 2026-01-01T00:00:00Z
```

No outcome, return, entry, exit, win/loss, TP, SL, or PnL column may enter the
Stage-0 source or sampling function.

## What the expert marks

For each card:

1. Correct or reject the seeded sleep-to-W1 boundary.
2. Add every visually distinct later pump wave with the same `pump` tool.
3. Leave the automatically generated sideways intervals untouched.
4. Record why the episode is convincing or noisy in notes. At this stage the
   important distinctions are continuation of one wave versus real restart,
   loss/retention of the elevated base, self-driven activity versus broad-market
   movement, and whether distribution/dump is visible.

The dump boundary and causal short trigger are deliberately not automated yet.
First we need a reproducible wave ontology. After enough manual examples exist,
we compare causal separators, including EMA-fan expansion during waves and
compression/crossing during sideways. EMA behavior is a tested feature family,
not a definition chosen after seeing winners.

## Frozen annotation codebook and quality gates

The annotation-quality protocol was frozen on 2026-08-03 while the expert-label
file still contained zero rows. Its identifier and frozen pilot identity are:

```text
label_audit_freeze_id = pump_lifecycle_stage0_label_audit_20260803_v1
pilot_rows = 70
pilot_identity_sha256 = 83392c169ce9a6ac966d7276d8be16d2368927d73b3cc1c7da3862c8c172f64f
effective_expert_labels_at_freeze = 0
```

Every card requires explicit categorical answers chosen before any outcome or
PnL study:

```text
sleep before W1: clear / weak / absent / uncertain
later-wave separation: distinct restart / one continuous wave / mixed /
                       no later wave / uncertain
elevated base after W1: retained / partly retained / lost / not applicable /
                        uncertain
visible activity driver: coin-specific / broad market / mixed / uncertain
state after the waves: distribution-dump / sideways / continued markup /
                       not applicable / uncertain
boundary confidence: high / medium / low
```

`no later wave` is valid only when fewer than two pump waves are drawn; it is
invalid for a W2+ label. Conversely, every zero/one-wave label must use
`no later wave`. This cross-field rule is audited automatically.

These answers describe morphology and review context. They are labels, not
causal model features, and must never be joined into a feature row at or before
the prediction snapshot. Free notes must contain at least 20 non-template
characters explaining why the episode is clean or noisy.

The primary pass is usable only when all 70 frozen cards are manually saved,
all schemas and codebook fields pass, all notes are substantive, the pilot hash
is unchanged, and no review candle crosses into 2026. For ontology development
the pilot must contain at least 20 examples with two or more waves and at least
20 reviewed non-multiwave examples. These are class-support requirements, not a
prevalence claim. If either class is short, the only allowed response is a
predeclared outcome-free expansion from the retained population; changing the
definition or sampling by dump, return, win/loss, or later trade result is
forbidden.

After the primary pass, reliability is checked before building a detector. A
deterministic 20% subset (14 cards) is annotated again without showing the
first-pass label, preferably by a second expert. If only one expert is
available, the repeat occurs after a minimum seven-day washout. Frozen gates:

```text
multiwave/non-multiwave agreement >= 12 of 14 cards
exact wave-count agreement >= 11 of 14 cards
for ordinals present in both passes:
  median absolute boundary difference <= 2 chart bars
  90th-percentile absolute boundary difference <= 5 chart bars
```

Failure means the wave ontology is not reproducible. We then revise the
codebook and start a newly versioned annotation study; we do not tune thresholds
until the same labels appear to agree.

## Scientific sequence after Stage 0

1. Audit manual consistency and identify common traits of clean versus noisy
   wave sequences, including symbol and context concentration.
2. Freeze and validate a causal online wave-separation model on 2025 only.
3. Freeze a causal bearish signal after W2/W3 and test nature prediction and
   timing.
4. Prove conditional EV before any trade simulation.
5. Only then evaluate structural SL/TP variants, fees, slippage, funding,
   drawdown, trading-day consistency, top-trade dependence, and 2–5% risk.

The BULLA-2026 example inspired the hypothesis, so 2026 is not a pristine
holdout for this strategy. Development artifacts remain restricted to 2025;
honest confirmation requires forward data observed only after the full protocol
and model are frozen.
