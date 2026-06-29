# Current strategy source of truth

The current active research strategy has exactly one canonical Strategy Spec:

```text
canonical_spec = docs/pump_fade_archetype_protocol.md
strategy_name = pump_fade_close_race_v1
strategy_family = pump_fade
strategy_contract_version = horizon_free_event_strategy_v1
feature_schema_version = pump_fade_market_mechanics_v2
nature_label_schema_version = pump_fade_event_peak_close_race_v1
decision_label_schema_version = pump_fade_close_race_horizon_free_v1
live_trading_strategy = false
```

This file is only a research-state pointer. It must not duplicate trigger
semantics, labels, feature admissibility, evidence language, execution policy,
or lifecycle rules from the canonical spec.

Compatibility note:

```text
docs/strategies/anomaly_strategy.md documents older fixed-horizon MVP1 registry
variants and compatibility smoke paths. It is not the source of truth for the
current structural pump-fade research target.
```

Shadow live and production live remain intentionally absent until calibrated
prediction, timing, EV, pessimistic simulation, and governance pass their audits.

Legacy trading rules are reference-only and must not be imported into the new
core.
