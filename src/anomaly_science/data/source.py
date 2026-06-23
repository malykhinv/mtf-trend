from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd


class CsvDataSourceError(ValueError):
    """Raised when the explicit CSV data-source boundary cannot load a dataset."""


@dataclass(frozen=True, slots=True)
class CsvDatasetSpec:
    name: str
    file_name: str
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...] = ()
    boundary_time_column: str | None = None


CANDLE_REQUIRED_COLUMNS = (
    "symbol",
    "open_time_ms",
    "available_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
)
CANDLE_OPTIONAL_COLUMNS = ("number_of_trades", "taker_buy_quote_volume")

DATASET_SPECS: dict[str, CsvDatasetSpec] = {
    "candles_1m": CsvDatasetSpec(
        name="candles_1m",
        file_name="candles_1m.csv",
        required_columns=CANDLE_REQUIRED_COLUMNS,
        optional_columns=CANDLE_OPTIONAL_COLUMNS,
        boundary_time_column="open_time_ms",
    ),
    "candles_5m": CsvDatasetSpec(
        name="candles_5m",
        file_name="candles_5m.csv",
        required_columns=CANDLE_REQUIRED_COLUMNS,
        optional_columns=CANDLE_OPTIONAL_COLUMNS,
        boundary_time_column="open_time_ms",
    ),
    "open_interest_5m": CsvDatasetSpec(
        name="open_interest_5m",
        file_name="open_interest_5m.csv",
        required_columns=("symbol", "timestamp_ms", "available_time_ms", "open_interest", "source"),
        boundary_time_column="timestamp_ms",
    ),
    "liquidations": CsvDatasetSpec(
        name="liquidations",
        file_name="liquidations.csv",
        required_columns=(
            "symbol",
            "event_time_ms",
            "available_time_ms",
            "side",
            "price",
            "quantity",
            "quote_quantity",
            "source",
        ),
        boundary_time_column="event_time_ms",
    ),
    "funding_rate": CsvDatasetSpec(
        name="funding_rate",
        file_name="funding_rate.csv",
        required_columns=("symbol", "timestamp_ms", "funding_rate"),
        boundary_time_column="timestamp_ms",
    ),
    "symbol_universe_by_day": CsvDatasetSpec(
        name="symbol_universe_by_day",
        file_name="symbol_universe_by_day.csv",
        required_columns=(
            "trade_date",
            "symbol",
            "listed_asof_day",
            "delisted_asof_day",
            "tradable_on_day",
            "has_1m_data",
            "has_5m_data",
            "has_oi_data",
            "has_liquidation_data",
            "liquidity_eligible_on_day",
            "reason_if_excluded",
        ),
        boundary_time_column="trade_date",
    ),
}


class MarketDataSource(Protocol):
    """Explicit data boundary for MVP1 normalized market-data inputs."""

    def read_frame(self, dataset_name: str, *, required: bool = True) -> pd.DataFrame | None:
        """Return a raw dataset frame or None for optional missing datasets."""


@dataclass(frozen=True, slots=True)
class CsvDirectoryDataSource:
    """Read normalized CSV datasets from a directory.

    This boundary is deliberately narrow and explicit. It does not synthesize
    missing columns, derive proxy fields, or silently convert unknown payloads.
    Optional time bounds are an explicit research input view, not a fallback.
    """

    root: Path
    max_time_ms: int | None = None

    def __init__(self, root: str | Path, *, max_time_ms: int | None = None) -> None:
        object.__setattr__(self, "root", Path(root))
        object.__setattr__(self, "max_time_ms", max_time_ms)

    def dataset_path(self, dataset_name: str) -> Path:
        spec = _dataset_spec(dataset_name)
        return self.root / spec.file_name

    def read_frame(self, dataset_name: str, *, required: bool = True) -> pd.DataFrame | None:
        spec = _dataset_spec(dataset_name)
        path = self.root / spec.file_name
        if not path.exists():
            if required:
                raise CsvDataSourceError(f"required dataset {spec.file_name!r} is missing under {self.root}")
            return None
        frame = pd.read_csv(path)
        missing = [name for name in spec.required_columns if name not in frame.columns]
        if missing:
            raise CsvDataSourceError(f"dataset {spec.file_name!r} is missing required columns: {missing}")
        return _apply_explicit_time_view(frame=frame, spec=spec, max_time_ms=self.max_time_ms)


def _dataset_spec(dataset_name: str) -> CsvDatasetSpec:
    try:
        return DATASET_SPECS[dataset_name]
    except KeyError as exc:
        raise CsvDataSourceError(f"unknown dataset name: {dataset_name!r}") from exc


def available_dataset_names() -> tuple[str, ...]:
    return tuple(DATASET_SPECS)


def _apply_explicit_time_view(
    *,
    frame: pd.DataFrame,
    spec: CsvDatasetSpec,
    max_time_ms: int | None,
) -> pd.DataFrame:
    if max_time_ms is None or spec.boundary_time_column is None:
        return frame
    column = spec.boundary_time_column
    if column not in frame.columns:
        raise CsvDataSourceError(
            f"dataset {spec.file_name!r} cannot apply explicit research input view: "
            f"missing boundary column {column!r}"
        )
    if column == "trade_date":
        end_date = pd.to_datetime(max_time_ms - 1, unit="ms", utc=True).date().isoformat()
        return frame.loc[frame[column].astype(str) <= end_date].copy()
    return frame.loc[pd.to_numeric(frame[column], errors="raise") < max_time_ms].copy()
