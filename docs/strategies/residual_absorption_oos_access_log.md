# Residual absorption OOS access log

## 2026-08-02 — non-analytic schema inspection

- Incident id: `residual_absorption_oos_access_20260802_001`
- Access type: accidental terminal display during input-schema inspection.
- Scope: the first two and last two raw rows of `BTCUSDT.parquet` were printed;
  the last two rows belong to 2026.
- Not accessed: strategy events, labels, feature distributions, CatBoost output,
  thresholds, PnL, execution results, or portfolio metrics.
- Decisions made from the access: none.
- Corrective action: all subsequent strategy reads use a physical IS predicate
  before materialization; schema inspection uses Parquet metadata only.
- Scientific status: the requested 2026 OOS boundary is retained, but final OOS
  reporting must disclose this non-analytic access rather than claiming that no
  2026 row was ever displayed.
