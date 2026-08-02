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
full source population is retained separately. Cards show six hours before the
proposed wave start and up to 72 hours after its first culmination, clipped
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
