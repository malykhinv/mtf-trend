"""Signal-density contract for the causal crossing population."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .contracts import SpotTrendContractError


@dataclass(frozen=True, slots=True)
class SignalDensityConfig:
    target_mean_entries_per_calendar_day: float = 3.0

    def __post_init__(self) -> None:
        if self.target_mean_entries_per_calendar_day != 3.0:
            raise SpotTrendContractError("IS signal-density target is frozen at 3 entries/day")


def build_signal_density_audit(
    causal_crossings: pd.DataFrame,
    *,
    config: SignalDensityConfig = SignalDensityConfig(),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Count unique signals over all calendar days; this is not a daily quota."""

    required = {"event_id", "snapshot_time"}
    missing = required.difference(causal_crossings.columns)
    if missing:
        raise SpotTrendContractError(f"signal-density audit missing columns: {sorted(missing)}")
    events = causal_crossings[["event_id", "snapshot_time"]].drop_duplicates("event_id")
    event_dates = pd.to_datetime(events["snapshot_time"], utc=True).dt.floor("D")
    if event_dates.empty:
        raise SpotTrendContractError("signal-density audit received no causal events")
    calendar = pd.date_range(event_dates.min(), event_dates.max(), freq="D", tz="UTC")
    counts = event_dates.value_counts().reindex(calendar, fill_value=0).sort_index()
    daily = pd.DataFrame({"date": calendar, "unique_signals": counts.to_numpy(dtype=int)})
    target = config.target_mean_entries_per_calendar_day
    mean_frequency = float(daily["unique_signals"].mean())
    target_event_count = int(target * len(daily))
    summary = pd.DataFrame(
        [
            {
                "period_start": calendar.min(),
                "period_end": calendar.max(),
                "calendar_days": len(calendar),
                "active_days": int(daily["unique_signals"].gt(0).sum()),
                "unique_signals": len(events),
                "mean_signals_per_calendar_day": mean_frequency,
                "mean_signals_per_active_day": float(
                    daily.loc[daily["unique_signals"].gt(0), "unique_signals"].mean()
                ),
                "median_signals_per_calendar_day": float(daily["unique_signals"].median()),
                "p95_signals_per_calendar_day": float(daily["unique_signals"].quantile(0.95)),
                "maximum_signals_per_calendar_day": int(daily["unique_signals"].max()),
                "target_mean_entries_per_calendar_day": target,
                "target_event_count_over_period": target_event_count,
                "required_rejection_share": max(0.0, 1.0 - target_event_count / len(events)),
                "quality_reduction_required": mean_frequency > target,
            }
        ]
    )
    return daily, summary


def write_signal_density_audit(
    audit: tuple[pd.DataFrame, pd.DataFrame],
    output_dir: Path,
) -> None:
    daily, summary = audit
    daily.to_csv(output_dir / "causal_crossing_daily_frequency.csv", index=False)
    summary.to_csv(output_dir / "causal_crossing_frequency_summary.csv", index=False)
