from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import polars as pl

from .contracts import SpotTrendContractError


IS_START = date(2025, 1, 1)
IS_END = date(2025, 12, 31)
OOS_START = date(2026, 1, 1)


@dataclass(frozen=True, slots=True)
class LocalFuturesAggregationConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    output_dir: Path = Path(".output/market/binance_vision/um_futures/daily_local_is_v1")
    minimum_daily_minutes: int = 1_400
    quote_suffix: str = "USDT"
    is_start: date = IS_START
    is_end: date = IS_END

    def __post_init__(self) -> None:
        if self.is_start != IS_START or self.is_end != IS_END:
            raise SpotTrendContractError(
                f"futures aggregation is frozen to calendar IS {IS_START}..{IS_END}"
            )


@dataclass(frozen=True, slots=True)
class LocalFuturesAggregationStats:
    source_files: int
    completed_symbols: int
    failed_symbols: int
    daily_rows: int
    first_date: str
    last_date: str
    failures: tuple[str, ...]


ProgressCallback = Callable[[str], None]


def _aggregate_symbol(path: Path, config: LocalFuturesAggregationConfig) -> pd.DataFrame:
    symbol = path.stem.upper()
    if not symbol.endswith(config.quote_suffix):
        return pd.DataFrame()
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_quote_volume",
        "open_interest",
        "oi_available",
        "missing_oi_flag",
    }
    schema_names = set(pl.scan_parquet(path).collect_schema().names())
    missing = sorted(required.difference(schema_names))
    if missing:
        raise SpotTrendContractError(f"{path.name} missing enriched 1m columns: {missing}")
    start_ms = int(pd.Timestamp(config.is_start, tz="UTC").timestamp() * 1_000)
    end_exclusive_ms = int((pd.Timestamp(config.is_end, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1_000)
    frame = (
        pl.scan_parquet(path)
        .filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_exclusive_ms))
        .select(sorted(required))
        .with_columns(
            pl.from_epoch("timestamp", time_unit="ms").dt.replace_time_zone("UTC").dt.date().alias("date"),
            ((pl.col("timestamp") % 86_400_000) // 60_000).cast(pl.Int16).alias("minute_of_day"),
        )
        .with_columns(
            pl.when(pl.col("date").eq(pl.col("date").shift(1)))
            .then(pl.col("close").log().diff())
            .otherwise(None)
            .alias("log_return_1m"),
            pl.when(
                pl.col("date").eq(pl.col("date").shift(1))
                & pl.col("open_interest").gt(0)
                & pl.col("open_interest").shift(1).gt(0)
            )
            .then(pl.col("open_interest").log().diff())
            .otherwise(None)
            .alias("oi_log_change_1m"),
            pl.when(pl.col("quote_volume").gt(0))
            .then(2.0 * pl.col("taker_buy_quote_volume") / pl.col("quote_volume") - 1.0)
            .otherwise(None)
            .alias("taker_imbalance_1m"),
            pl.when(pl.col("trade_count").gt(0))
            .then(pl.col("quote_volume") / pl.col("trade_count"))
            .otherwise(None)
            .alias("mean_trade_notional_1m"),
        )
        .sort("timestamp")
        .group_by("date", maintain_order=True)
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("base_volume"),
            pl.col("quote_volume").sum().alias("quote_volume"),
            pl.col("trade_count").sum().alias("number_of_trades"),
            pl.col("taker_buy_quote_volume").sum().alias("taker_buy_quote_volume"),
            pl.col("open_interest").drop_nulls().first().alias("open_interest_open"),
            pl.col("open_interest").drop_nulls().last().alias("open_interest_close"),
            pl.col("open_interest").min().alias("open_interest_low"),
            pl.col("open_interest").max().alias("open_interest_high"),
            pl.col("open_interest").mean().alias("open_interest_mean"),
            pl.col("open_interest").count().alias("open_interest_observations"),
            pl.col("oi_available").sum().alias("oi_available_minutes"),
            pl.col("missing_oi_flag").sum().alias("missing_oi_minutes"),
            (pl.col("log_return_1m") * pl.col("log_return_1m")).sum().alias("intraday_realized_variance"),
            pl.when(pl.col("log_return_1m") < 0)
            .then(pl.col("log_return_1m") * pl.col("log_return_1m"))
            .otherwise(0.0)
            .sum()
            .alias("intraday_downside_semivariance"),
            pl.when(pl.col("log_return_1m") > 0)
            .then(pl.col("log_return_1m") * pl.col("log_return_1m"))
            .otherwise(0.0)
            .sum()
            .alias("intraday_upside_semivariance"),
            pl.col("log_return_1m").abs().max().alias("intraday_max_absolute_return"),
            pl.col("log_return_1m").quantile(0.05).alias("intraday_return_q05"),
            pl.col("log_return_1m").quantile(0.95).alias("intraday_return_q95"),
            pl.col("log_return_1m").skew().alias("intraday_return_skew"),
            pl.col("log_return_1m").kurtosis().alias("intraday_return_kurtosis"),
            (pl.col("log_return_1m") > 0).mean().alias("intraday_positive_minute_share"),
            (
                (pl.col("quote_volume") * pl.col("quote_volume")).sum()
                / (pl.col("quote_volume").sum() * pl.col("quote_volume").sum())
            ).alias("intraday_quote_volume_hhi"),
            (pl.col("quote_volume").max() / pl.col("quote_volume").sum()).alias(
                "intraday_max_minute_volume_share"
            ),
            pl.col("taker_imbalance_1m").mean().alias("intraday_taker_imbalance_mean"),
            pl.col("taker_imbalance_1m").std().alias("intraday_taker_imbalance_std"),
            pl.col("mean_trade_notional_1m").std().alias("intraday_trade_notional_std"),
            pl.corr("log_return_1m", "oi_log_change_1m").alias("oi_price_change_correlation_1m"),
            pl.col("oi_log_change_1m").std().alias("oi_intraday_change_volatility"),
            pl.col("oi_log_change_1m").abs().max().alias("oi_intraday_max_absolute_change"),
            pl.col("log_return_1m").filter(pl.col("minute_of_day") < 480).sum().alias("asia_session_return"),
            pl.col("log_return_1m")
            .filter((pl.col("minute_of_day") >= 480) & (pl.col("minute_of_day") < 960))
            .sum()
            .alias("europe_session_return"),
            pl.col("log_return_1m").filter(pl.col("minute_of_day") >= 960).sum().alias("us_session_return"),
            (pl.col("quote_volume").filter(pl.col("minute_of_day") < 480).sum() / pl.col("quote_volume").sum())
            .alias("asia_session_volume_share"),
            (
                pl.col("quote_volume")
                .filter((pl.col("minute_of_day") >= 480) & (pl.col("minute_of_day") < 960))
                .sum()
                / pl.col("quote_volume").sum()
            ).alias("europe_session_volume_share"),
            (pl.col("quote_volume").filter(pl.col("minute_of_day") >= 960).sum() / pl.col("quote_volume").sum())
            .alias("us_session_volume_share"),
            (pl.col("quote_volume").filter(pl.col("minute_of_day") < 60).sum() / pl.col("quote_volume").sum())
            .alias("first_hour_volume_share"),
            (pl.col("quote_volume").filter(pl.col("minute_of_day") >= 1_380).sum() / pl.col("quote_volume").sum())
            .alias("last_hour_volume_share"),
            pl.col("minute_of_day").sort_by("high").last().alias("intraday_high_minute"),
            pl.col("minute_of_day").sort_by("low").first().alias("intraday_low_minute"),
            pl.len().alias("minute_count"),
            (pl.col("timestamp").max() + 60_000).alias("available_time_ms"),
        )
        .with_columns(
            pl.lit(symbol).alias("symbol"),
            (pl.col("minute_count") >= config.minimum_daily_minutes).alias("complete_daily_bar"),
        )
        .collect()
    )
    output = frame.to_pandas()
    output["date"] = pd.to_datetime(output["date"], utc=True).dt.normalize()
    return output.sort_values("date", kind="stable").reset_index(drop=True)


def aggregate_local_futures_1m(
    config: LocalFuturesAggregationConfig = LocalFuturesAggregationConfig(),
    *,
    progress: ProgressCallback | None = print,
) -> LocalFuturesAggregationStats:
    """Aggregate the existing causal enriched 1m cache; never access network."""

    if not config.source_dir.exists():
        raise FileNotFoundError(config.source_dir)
    paths = sorted(
        path
        for path in config.source_dir.glob("*.parquet")
        if path.stem.upper().endswith(config.quote_suffix)
    )
    if not paths:
        raise SpotTrendContractError("local enriched 1m cache contains no requested futures symbols")
    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for index, path in enumerate(paths, start=1):
        frame = _aggregate_symbol(path, config)
        if not frame.empty:
            frames.append(frame)
        if progress and (index % 25 == 0 or index == len(paths)):
            progress(
                f"local 1m->1d {index}/{len(paths)} completed={len(frames)} "
                f"failed={len(failures)} daily_rows={sum(len(item) for item in frames)}"
            )
    if not frames:
        raise SpotTrendContractError("local futures aggregation produced no daily rows")
    daily = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    daily_path = config.output_dir / "futures_daily_is.parquet"
    daily.to_parquet(daily_path, index=False)
    stats = LocalFuturesAggregationStats(
        source_files=len(paths),
        completed_symbols=len(frames),
        failed_symbols=len(failures),
        daily_rows=len(daily),
        first_date=str(daily["date"].min().date()),
        last_date=str(daily["date"].max().date()),
        failures=tuple(failures),
    )
    manifest = {
        "schema_version": "binance_usdm_daily_local_is_v2_intraday",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "existing enriched_1m local parquet cache",
        "network_access": False,
        "historical_partition": "is_only",
        "is_start": IS_START.isoformat(),
        "is_end_inclusive": IS_END.isoformat(),
        "oos_start": OOS_START.isoformat(),
        "config": {key: str(value) if isinstance(value, Path) else value for key, value in asdict(config).items()},
        "stats": asdict(stats),
    }
    (config.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return stats


def load_local_futures_daily_is(directory: Path) -> pd.DataFrame:
    manifest_path = directory / "manifest.json"
    data_path = directory / "futures_daily_is.parquet"
    if not manifest_path.exists() or not data_path.exists():
        raise FileNotFoundError(f"missing local futures daily cache under {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("network_access") is not False or manifest.get("historical_partition") != "is_only":
        raise SpotTrendContractError("local futures manifest does not prove the IS-only no-network contract")
    if manifest.get("is_start") != IS_START.isoformat() or manifest.get("is_end_inclusive") != IS_END.isoformat():
        raise SpotTrendContractError("local futures manifest does not match the frozen calendar-2025 IS")
    frame = pd.read_parquet(data_path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.normalize()
    if frame["date"].min().date() < IS_START or frame["date"].max().date() > IS_END:
        raise SpotTrendContractError("local futures daily cache contains a row outside calendar-2025 IS")
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


_STABLE_BASES = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI"})
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def canonical_futures_base(symbol: str) -> str:
    base = symbol.upper().removesuffix("USDT")
    for prefix in ("1000000", "1000"):
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix) :]
    return base


def build_data_inferred_futures_master(daily_bars: pd.DataFrame) -> pd.DataFrame:
    """Create causal active/inactive episodes from the local bar stream only."""

    rows: list[dict[str, object]] = []
    final_date = pd.to_datetime(daily_bars["date"], utc=True).dt.normalize().max()
    for symbol, group in daily_bars.groupby("symbol", sort=True):
        dates = pd.DatetimeIndex(pd.to_datetime(group["date"], utc=True).dt.normalize().drop_duplicates()).sort_values()
        if dates.empty:
            continue
        base = symbol.removesuffix("USDT")
        canonical = canonical_futures_base(symbol)
        run_starts = [dates[0]]
        run_ends: list[pd.Timestamp] = []
        for previous, current in zip(dates[:-1], dates[1:], strict=False):
            if (current - previous).days > 1:
                run_ends.append(previous)
                run_starts.append(current)
        run_ends.append(dates[-1])
        for run_start, run_end in zip(run_starts, run_ends, strict=True):
            common = {
                "symbol": symbol,
                "base_asset": base,
                "canonical_asset_id": canonical,
                "quote_asset": "USDT",
                "trading_start_date": dates[0],
                "delisting_announcement_date": pd.NaT,
                "delisting_date": pd.NaT,
                "monitoring_tag": False,
                "monitoring_tag_known": False,
                "is_stablecoin": canonical in _STABLE_BASES,
                "is_leveraged_token": canonical.endswith(_LEVERAGED_SUFFIXES),
            }
            rows.append(
                {
                    **common,
                    "as_of_date": run_start,
                    "trading_end_date": pd.NaT,
                    "spot_trading_allowed": True,
                    "status": "TRADING",
                }
            )
            inactive_date = run_end + pd.Timedelta(days=1)
            if inactive_date <= final_date:
                rows.append(
                    {
                        **common,
                        "as_of_date": inactive_date,
                        "trading_end_date": inactive_date,
                        "spot_trading_allowed": False,
                        "status": "DATA_INFERRED_INACTIVE",
                    }
                )
    return pd.DataFrame(rows).sort_values(["as_of_date", "symbol"], kind="stable").reset_index(drop=True)
