# Binance Spot trend ensemble: frozen scientific protocol

Protocol freeze date: **2026-07-20**.  Strategy version:
`binance_spot_trend_v1`.  Feature schema: `spot_trend_features_v1`.

## Scientific status of the supplied specification

The supplied interval 2025-03-20 through 2026-06-30 is not called blind by
code.  It becomes a locked holdout only when the researcher records, before a
run, that none of its results were inspected.  Otherwise it is historical OOS
and the true forward period starts on 2026-07-21.

The supplied monthly CatBoost refit is replaced by the repository-wide heavy
ML contract: one frozen fit per calendar week.  Each origin uses a 1,095-day
lookback, a 180-day validation block, a 20-day feature purge, and only labels
whose `label_end_date` is no later than the corresponding fit boundary.  Daily
rows within the application week never update model weights.

The research decision order is frozen as follows:

1. audit point-in-time data, universe membership, features and labels;
2. test whether the 20-day risk-adjusted target is predictable in weekly OOS;
3. require positive OOS expected target in the predicted top quintile, not
   merely a relative ordering improvement;
4. test whether the rank information is available at the close before the
   next-open decision;
5. compare expected-value ordering and only then run variants A/B/C through
   the causal portfolio simulator;
6. apply the pre-registered admission rules; never select a variant by looking
   only at final PnL.

## Strategy-owned semantics

The long-only signal is the fraction of active close-based Donchian models for
the frozen horizons `5, 10, 20, 30, 60, 90, 150, 250, 360`.  A breakout is
strictly greater than the maximum of the preceding closes.  An active model
first tests the stop carried from the preceding close; only a surviving model
may advance its stop to the current inclusive Donchian midpoint.  Signals
formed at close `t` are executable no earlier than open `t+1`.

The CatBoost overlay cannot create a position, short, lever, identify a symbol,
or exceed the baseline target.  Three fixed seeds (17, 43, 91) are averaged.
The OOS daily percentile rank maps to `[0.5, 1.0]`.  The negative control uses
the frozen seed 20250319 and permutes predictions only within the same date.

## Point-in-time data contract

Daily UTC spot bars are never forward-filled.  The symbol master is an
as-of history and includes trading lifecycle, stablecoin/leveraged-product
classification, canonical underlying identity, delisting announcement,
Monitoring Tag state and an explicit `monitoring_tag_known` availability flag.
Universe construction uses only the last master record
whose `as_of_date <= snapshot_date` and only market bars available by that
snapshot.  A redenomination or contract migration remains a new symbol and a
new lifecycle.

Every learning row satisfies:

```text
feature_cutoff_date = snapshot_date
future_start_date   > snapshot_date
label_end_date      >= future_start_date
label_end_date      <= model training boundary
```

## Explicit numerical conventions

- Return features and the target use natural-log returns.
- Calendar windows are reindexed without price filling.  A gap makes every
  affected rolling feature, exact `t+1` open, exact `t+H` close, or covariance
  estimate unavailable; it never silently shortens or stretches the horizon.
- Realized volatility uses sample standard deviation and `sqrt(365)`.
- Downside volatility is the root mean square of negative log returns,
  annualized by `sqrt(365)`.
- Cross-sectional percentile ranks use average ranks and map a singleton to
  `0.5`.
- EWMA covariance uses the latest 60 complete common return observations,
  lambda 0.94, then 50% diagonal shrinkage.  If it is not estimable, targets
  are explicitly set to zero and the reason is emitted; there is no fallback.
- A missing bar prevents opening or increasing.  A daily portfolio return with
  an unpriceable held asset is marked missing and excluded from ratio metrics,
  rather than silently recorded as zero.
- The ambiguous phrase “noticeably beats the negative control” is frozen as a
  Sharpe advantage of at least 0.10 at the 25 bps one-way cost scenario.
- “Does not depend on one coin/year” means net profit remains positive after
  removing the best coin contribution and after removing the best calendar
  year contribution.

The 10/25/50 bps cost cases, 0.10% base and 0.05% stress participation caps,
5% single-name cap, 100% gross cap, 15% portfolio-volatility target, and all
admission thresholds remain as supplied.

## Reproducible entry point

```powershell
python -m anomaly_science.strategy.spot_trend.cli `
  --daily-bars data/spot_daily.parquet `
  --symbol-master data/spot_symbol_master_history.parquet `
  --prediction-start 2025-03-20 `
  --prediction-end 2026-06-30 `
  --candidate-was-unseen `
  --output-dir .output/spot_trend_v1
```

Omit both attestation flags when the historical interval's blind status is not
known.  The manifest will then refuse to label it blind.  The two `--skip-*`
flags are intended only for fast development diagnostics; an admission run
must retain both robustness studies, otherwise the corresponding gate fails.
