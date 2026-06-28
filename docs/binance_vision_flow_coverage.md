# Binance Vision flow-data coverage

Verified against the official `data.binance.vision` USD-M archive on 2026-06-28.

## Open interest

Binance publishes historical USD-M `metrics` as **daily**, not monthly, ZIP archives. The old cache builder coupled optional data to the kline block granularity. Consequently, monthly historical kline blocks looked for nonexistent monthly metrics archives and historical OI was mostly marked missing.

Use the dedicated backfill over an existing enriched 1m cache:

```powershell
.venv\Scripts\python.exe main.py backfill-binance-vision-oi
```

The command downloads only daily metrics files covering each parquet's actual timestamp range. It does not download OHLCV again. The join contract is point-in-time:

```text
oi_sample_timestamp <= candle_timestamp
sample UTC day == candle UTC day
sample age <= configured maximum (10 minutes by default)
```

Each parquet is replaced atomically. Resume proofs and a run manifest are written under `enriched_1m/_metadata/oi_backfill/` and `enriched_1m/_metadata/oi_backfill_manifest.json`.

## Liquidations

The official USD-M `daily/liquidationSnapshot` prefix is empty for the 2025-2026 research period; representative direct archive URLs return HTTP 404. Therefore historical USD-M liquidations cannot be backfilled from Binance Public Data.

Do not substitute COIN-M liquidations, inferred aggressive trades, or zero values. Those are different observables. Historical testing requires an external vendor; forward collection can use Binance's forced-order websocket with explicit retention and availability timestamps.
