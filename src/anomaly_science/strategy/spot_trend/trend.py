from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import DEFAULT_HORIZONS, SpotTrendContractError
from .universe import validate_daily_bars


def _symbol_trend_state(group: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    actual = group.sort_values("date", kind="stable").copy()
    symbol = str(actual["symbol"].iloc[0])
    calendar = pd.date_range(actual["date"].min(), actual["date"].max(), freq="D", tz="UTC")
    ordered = actual.set_index("date").reindex(calendar).rename_axis("date").reset_index()
    ordered["symbol"] = symbol
    close = ordered["close"].to_numpy(dtype=np.float64)
    active = {horizon: False for horizon in horizons}
    stops = {horizon: np.nan for horizon in horizons}
    last_breakout_index: int | None = None
    rows: list[dict[str, object]] = []

    for index, value in enumerate(close):
        if not np.isfinite(value):
            continue
        row: dict[str, object] = {
            "date": ordered.at[index, "date"],
            "symbol": ordered.at[index, "symbol"],
            "trend_state_changed": False,
            "new_breakout": False,
        }
        for horizon in horizons:
            entered = False
            exited = False
            has_complete_window = index >= horizon and np.isfinite(close[index - horizon : index + 1]).all()
            has_complete_inclusive_window = index >= horizon - 1 and np.isfinite(
                close[index - horizon + 1 : index + 1]
            ).all()
            if active[horizon]:
                stop_before_close = float(stops[horizon])
                if value <= stop_before_close:
                    active[horizon] = False
                    stops[horizon] = np.nan
                    exited = True
                elif has_complete_inclusive_window:
                    inclusive = close[index - horizon + 1 : index + 1]
                    middle = 0.5 * (float(np.max(inclusive)) + float(np.min(inclusive)))
                    stops[horizon] = max(stop_before_close, middle)
            elif has_complete_window:
                inclusive = close[index - horizon + 1 : index + 1]
                middle = 0.5 * (float(np.max(inclusive)) + float(np.min(inclusive)))
                upper_previous = float(np.max(close[index - horizon : index]))
                if value > upper_previous:
                    active[horizon] = True
                    stops[horizon] = middle
                    entered = True
                    last_breakout_index = index
            row[f"active_{horizon}"] = active[horizon]
            row[f"stop_{horizon}"] = float(stops[horizon]) if active[horizon] else np.nan
            row[f"entered_{horizon}"] = entered
            row[f"exited_{horizon}"] = exited
            row[f"stop_distance_{horizon}"] = (
                float(value / stops[horizon] - 1.0) if active[horizon] and stops[horizon] > 0 else np.nan
            )
            row["trend_state_changed"] = bool(row["trend_state_changed"] or entered or exited)
            row["new_breakout"] = bool(row["new_breakout"] or entered)

        active_horizons = [horizon for horizon in horizons if active[horizon]]
        active_distances = [float(row[f"stop_distance_{horizon}"]) for horizon in active_horizons]
        row["active_count"] = len(active_horizons)
        row["trend_signal"] = len(active_horizons) / len(horizons)
        row["minimum_active_horizon"] = min(active_horizons) if active_horizons else np.nan
        row["maximum_active_horizon"] = max(active_horizons) if active_horizons else np.nan
        row["days_since_new_breakout"] = index - last_breakout_index if last_breakout_index is not None else np.nan
        row["minimum_stop_distance"] = min(active_distances) if active_distances else np.nan
        row["mean_stop_distance"] = float(np.mean(active_distances)) if active_distances else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def build_trend_state(
    daily_bars: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Return close-time Donchian state; every row is executable only next day."""

    if not horizons or tuple(sorted(set(horizons))) != horizons:
        raise SpotTrendContractError("horizons must be unique, positive, and increasing")
    if horizons[0] <= 0:
        raise SpotTrendContractError("horizons must be positive")
    bars = validate_daily_bars(daily_bars)
    frames = [_symbol_trend_state(group, horizons) for _, group in bars.groupby("symbol", sort=False)]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
