# Strategy spec

Active research strategy:

```text
strategy_family = anomaly
canonical_spec = docs/strategies/anomaly_strategy.md
primary_variant = broad_anomaly_v1
strategy_contract_version = base_strategy_v2_structural_execution
```

This is a research strategy, not a live trading strategy. It studies whether
future anomaly nature is predictable online from point-in-time state and feature
artifacts. Decision timing, EV, and simplified pessimistic simulation are
research artifacts only. Shadow live and production live remain intentionally
absent until prediction calibration, timing, EV, simulation, and governance pass
audits.

Current scientific sequence:
1. detect broad anomalies;
2. build online 1m state;
3. build future paths;
4. audit temporal correctness;
5. study calibrated prediction and negative controls;
6. study decision timing and EV;
7. study simplified pessimistic simulation;
8. only then consider shadow live or production execution realism.

Legacy trading rules are reference-only and must not be imported into the new core.
