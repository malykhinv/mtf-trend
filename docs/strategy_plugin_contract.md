# Strategy plugin boundary

Core is treated as a stable engine. A strategy may declare semantics through
`anomaly_science.contracts.strategy.BaseStrategy`; it must not reimplement Core
mechanics.

## Strategy owns

- trigger generation and causal trigger configuration;
- semantic horizons and required data streams;
- custom causal feature declarations and calculations;
- label interpretation;
- admissible structural stop, target, trigger, and partial-close policies;
- experiment populations and pre-registered strategy hypotheses.

## Core owns

- market-data ingestion and quality;
- point-in-time state and generic feature infrastructure;
- artifact contracts and serialization;
- walk-forward fitting, calibration, controls, EV calculations, simulation,
  costs, exposure accounting, and audits.

Core consumes a strategy only through the stable contract and the registry
port. It must not import a concrete strategy package, inspect a strategy name or
family string, or calculate a strategy-specific feature.

Adding a strategy therefore requires changes only under
`src/anomaly_science/strategy/`: implement the contract and add its explicit
registry declaration. No Core module or artifact schema is changed. New
strategy features travel through the declared custom-feature extension rather
than new fixed Core columns.

The `post_pump_*` fixed artifact fields are retained solely as a versioned
compatibility surface for existing artifacts. Their values are calculated by
the anomaly strategy through `generate_artifact_compatibility_features`; Core
only serializes the returned values. New strategies must not add compatibility
columns.

`tests/test_architecture_boundaries.py` is the permanent enforcement gate.
