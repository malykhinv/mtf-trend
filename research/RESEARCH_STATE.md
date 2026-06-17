# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- First target is MVP 1: dataset/state/future-path/audit, not trading.
- Current implemented slice: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events, online 1m anomaly state, raw future paths.
- Future paths are raw outcomes only; scenario labels, prediction, EV, PnL, simulation, shadow live, and production live are still intentionally absent.

Current commit: UNKNOWN until committed and verified with `git rev-parse HEAD`.
