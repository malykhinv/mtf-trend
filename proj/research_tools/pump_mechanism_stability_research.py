"""Cache-only pump mechanism stability research scaffold.

This module is the upstream layer for failed-pump short research.  Its first
patch intentionally does not mine candidates or PnL: it only installs the
honest command contract and writes a run configuration artifact.  Later patches
will build the immutable pump event/outcome store, mechanism taxonomy, full
negative-space plateau accounting, and daily prequential OOS replay.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import time
from typing import Iterable

import pandas as pd

from constants import DEFAULT_CACHE_DIR, DEFAULT_RESULTS_DIR
from research_tools.failed_pump_short_research import parse_research_end_timestamp_ms

__all__ = (
    "PumpMechanismStabilityConfig",
    "parse_research_end_timestamp_ms",
    "run_pump_mechanism_stability_research",
)

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

RESEARCH_ID = "pump_mechanism_stability_research_v1"
DATA_ACCESS_MODEL = "cache_only_no_exchange_fetch"
CACHE_READ_MODE = "read_only"
CACHE_WRITE_MODEL = "no_cache_writes_outputs_only_to_results_dir"
IMPLEMENTATION_STAGE = "skeleton_cli_and_run_config_only"

# The daily replay contract is fixed here so it is visible before the heavier
# event-store implementation lands.  Do not expose these as CLI optimization
# knobs: changing them must create an explicit config/code diff.
ROLLING_WINDOWS_DAYS = (15, 30, 60)
PRIMARY_EVALUATION_MODEL = "daily_prequential_train_past_test_day"
PLATEAU_ACCOUNTING_MODEL = "full_neighborhood_with_negative_space"
FUTURE_OUTCOME_USAGE_MODEL = "outcomes_for_response_analysis_only_not_entry_features"
OI_MODEL = "closed_5m_oi_asof_setup_or_confirm_close"


@dataclass(frozen=True, slots=True)
class PumpMechanismStabilityConfig:
    """Configuration for the pump mechanism stability research command.

    Only operational parameters belong here.  Research thresholds and scoring
    gates are constants in code so historical runs are reproducible and cannot
    be tuned from the command line after seeing OOS/final artifacts.
    """

    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "pump_mechanism_stability_research"
    days: int = 365
    end_timestamp_ms: int | None = None
    symbol_workers: int = min(4, max(1, os.cpu_count() or 1))
    fail_on_empty_input: bool = True

    def __post_init__(self) -> None:
        if int(self.days) <= 0:
            raise ValueError("days must be > 0")
        if int(self.symbol_workers) <= 0:
            raise ValueError("symbol_workers must be > 0")


def run_pump_mechanism_stability_research(
    config: PumpMechanismStabilityConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str = "pump mechanism stability research",
) -> Path:
    """Install the mechanism-research run contract and write run_config.

    This first patch deliberately writes only metadata.  It gives the project a
    clean CLI boundary and artifact namespace without mixing mechanism discovery
    with the existing failed-pump short PnL/evaluation code.
    """

    started_at = time.monotonic()
    selected_symbols = tuple(str(symbol).strip() for symbol in (symbols or ()) if str(symbol).strip())
    config.output_dir.mkdir(parents=True, exist_ok=True)

    run_config = _run_config_frame(
        config=config,
        selected_symbols=selected_symbols,
        started_at=started_at,
    )
    manifest = _artifact_manifest_frame(config=config)

    _write_csv(config.output_dir / "pump_mechanism_run_config.csv", run_config)
    _write_csv(config.output_dir / "pump_mechanism_artifact_manifest.csv", manifest)

    print(
        f"{progress_label}: scaffold artifacts written to {config.output_dir}",
        flush=True,
    )
    return config.output_dir


def _run_config_frame(
    *,
    config: PumpMechanismStabilityConfig,
    selected_symbols: tuple[str, ...],
    started_at: float,
) -> pd.DataFrame:
    now = datetime.now(UTC)
    row = {
        **asdict(config),
        "research_id": RESEARCH_ID,
        "implementation_stage": IMPLEMENTATION_STAGE,
        "data_access_model": DATA_ACCESS_MODEL,
        "cache_read_mode": CACHE_READ_MODE,
        "cache_write_model": CACHE_WRITE_MODEL,
        "cache_dir": str(Path(config.cache_dir)),
        "output_dir": str(Path(config.output_dir)),
        "rolling_windows_days": ",".join(str(window) for window in ROLLING_WINDOWS_DAYS),
        "primary_evaluation_model": PRIMARY_EVALUATION_MODEL,
        "plateau_accounting_model": PLATEAU_ACCOUNTING_MODEL,
        "future_outcome_usage_model": FUTURE_OUTCOME_USAGE_MODEL,
        "oi_model": OI_MODEL,
        "final_holdout_tuning_allowed": False,
        "human_post_final_threshold_tuning_allowed": False,
        "daily_train_uses_only_days_before_test": True,
        "negative_space_required": True,
        "symbols": ",".join(selected_symbols),
        "symbol_count": int(len(selected_symbols)),
        "created_at_utc": now.isoformat(),
        "end_timestamp_ms_requested": config.end_timestamp_ms,
        "end_time_utc_requested": _format_timestamp_ms(config.end_timestamp_ms),
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
    }
    return pd.DataFrame([row])


def _artifact_manifest_frame(*, config: PumpMechanismStabilityConfig) -> pd.DataFrame:
    planned = [
        ("pump_mechanism_run_config.csv", "written", "command/run contract and anti-leakage governance"),
        ("pump_mechanism_artifact_manifest.csv", "written", "planned artifact namespace"),
        ("pump_mechanism_events.parquet", "planned", "immutable broad pump event store with entry-known features"),
        ("pump_mechanism_outcomes.parquet", "planned", "future response vectors stored only as outcomes"),
        ("pump_mechanism_event_quality.csv", "planned", "cache-only data quality and coverage audit"),
        ("pump_mechanism_taxonomy.csv", "planned", "entry-known mechanism axes per event"),
        ("pump_mechanism_response_surfaces.csv", "planned", "mechanism response diagnostics before PnL"),
        ("pump_mechanism_negative_space.csv", "planned", "all generated neighbors including failed/rejected rules"),
        ("pump_mechanism_plateau_basins.csv", "planned", "basin-level plateau scores"),
        ("pump_mechanism_daily_oos.csv", "planned", "daily prequential OOS ledger"),
        ("pump_mechanism_daily_selection.csv", "planned", "train-only basin selection per test day"),
        ("pump_mechanism_window_health.csv", "planned", "15/30/60d train-window health"),
        ("pump_mechanism_selection_drift.csv", "planned", "selected mechanism drift over time"),
    ]
    rows = []
    for artifact_name, status, description in planned:
        rows.append(
            {
                "artifact_name": artifact_name,
                "status": status,
                "description": description,
                "output_dir": str(Path(config.output_dir)),
                "data_access_model": DATA_ACCESS_MODEL,
                "implementation_stage": IMPLEMENTATION_STAGE,
            }
        )
    return pd.DataFrame(rows)


def _format_timestamp_ms(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "latest-cache"
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
