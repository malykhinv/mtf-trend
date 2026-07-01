from __future__ import annotations

import numpy as np
import pandas as pd

PUMP_FADE_AGGTRADES_MINUTE_SCHEMA_VERSION = "pump_fade_aggtrades_minute_v1"

PUMP_FADE_AGGTRADES_MINUTE_FEATURES: tuple[str, ...] = (
    "aggtrades_trade_count",
    "trade_notional_p50",
    "trade_notional_p75",
    "trade_notional_p90",
    "trade_notional_p95",
    "trade_notional_p99",
    "top1pct_notional_share",
    "large_trade_count",
    "notional_gini",
    "same_side_run_mean",
    "same_side_run_max",
    "side_sign_entropy",
    "side_flip_rate",
    "inter_arrival_ms_mean",
    "inter_arrival_ms_std",
    "inter_arrival_ms_p90",
    "buy_impact_per_notional",
    "sell_impact_per_notional",
)

_MINUTE_MS = 60_000


def build_minute_aggtrades_features(
    *,
    price: np.ndarray,
    quantity: np.ndarray,
    transact_time_ms: np.ndarray,
    is_buyer_maker: np.ndarray,
) -> pd.DataFrame:
    """Aggregate a raw aggTrades tape into one row of causal features per closed minute.

    Each output row only uses trades whose transact_time falls inside that minute;
    minutes with zero trades are simply absent (missing, not zero).
    """

    price_arr = np.asarray(price, dtype=float)
    quantity_arr = np.asarray(quantity, dtype=float)
    time_arr = np.asarray(transact_time_ms, dtype=np.int64)
    maker_arr = np.asarray(is_buyer_maker, dtype=bool)
    count = len(price_arr)
    if not (count == len(quantity_arr) == len(time_arr) == len(maker_arr)):
        raise ValueError("aggtrades arrays must be aligned")
    if count == 0:
        return pd.DataFrame(columns=("timestamp", *PUMP_FADE_AGGTRADES_MINUTE_FEATURES))

    order = np.argsort(time_arr, kind="mergesort")
    price_arr = price_arr[order]
    quantity_arr = quantity_arr[order]
    time_arr = time_arr[order]
    maker_arr = maker_arr[order]

    notional = price_arr * quantity_arr
    side = np.where(maker_arr, -1.0, 1.0)  # buyer is maker => the aggressor sold
    minute = (time_arr // _MINUTE_MS) * _MINUTE_MS
    day_median_notional = float(np.median(notional))

    unique_minutes, start_indices = np.unique(minute, return_index=True)
    boundaries = np.append(start_indices, count)

    rows: list[dict[str, float]] = []
    for index, minute_ts in enumerate(unique_minutes):
        start, stop = int(boundaries[index]), int(boundaries[index + 1])
        rows.append(
            _minute_row(
                minute_ts=int(minute_ts),
                price=price_arr[start:stop],
                notional=notional[start:stop],
                side=side[start:stop],
                transact_time_ms=time_arr[start:stop],
                day_median_notional=day_median_notional,
            )
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("timestamp").reset_index(drop=True)


def _minute_row(
    *,
    minute_ts: int,
    price: np.ndarray,
    notional: np.ndarray,
    side: np.ndarray,
    transact_time_ms: np.ndarray,
    day_median_notional: float,
) -> dict[str, float]:
    count = len(notional)
    quantiles = np.quantile(notional, [0.5, 0.75, 0.9, 0.95, 0.99])
    sorted_notional = np.sort(notional)[::-1]
    top_n = max(1, int(np.ceil(count * 0.01)))
    total_notional = float(np.sum(notional))
    top_share = float(np.sum(sorted_notional[:top_n]) / max(total_notional, 1e-12))
    large_trade_count = float(np.sum(notional >= 5.0 * max(day_median_notional, 1e-12)))
    gini = _gini(notional)

    flip_rate = float(np.mean(side[1:] != side[:-1])) if count > 1 else 0.0
    run_lengths = _run_lengths(side)
    positive_fraction = float(np.mean(side > 0.0))
    sign_entropy = _binary_entropy(positive_fraction)

    inter_arrival = np.diff(transact_time_ms).astype(float) if count > 1 else np.asarray([])
    minute_return = float(price[-1] / max(price[0], 1e-12) - 1.0)
    buy_notional = float(np.sum(notional[side > 0.0]))
    sell_notional = float(np.sum(notional[side < 0.0]))
    buy_impact = max(minute_return, 0.0) / max(buy_notional, 1e-12)
    sell_impact = max(-minute_return, 0.0) / max(sell_notional, 1e-12)

    return {
        "timestamp": minute_ts,
        "aggtrades_trade_count": float(count),
        "trade_notional_p50": float(quantiles[0]),
        "trade_notional_p75": float(quantiles[1]),
        "trade_notional_p90": float(quantiles[2]),
        "trade_notional_p95": float(quantiles[3]),
        "trade_notional_p99": float(quantiles[4]),
        "top1pct_notional_share": top_share,
        "notional_gini": gini,
        "same_side_run_mean": float(np.mean(run_lengths)) if len(run_lengths) else float("nan"),
        "same_side_run_max": float(np.max(run_lengths)) if len(run_lengths) else float("nan"),
        "side_sign_entropy": sign_entropy,
        "side_flip_rate": flip_rate,
        "inter_arrival_ms_mean": float(np.mean(inter_arrival)) if len(inter_arrival) else float("nan"),
        "inter_arrival_ms_std": float(np.std(inter_arrival)) if len(inter_arrival) else float("nan"),
        "inter_arrival_ms_p90": float(np.quantile(inter_arrival, 0.9)) if len(inter_arrival) else float("nan"),
        "buy_impact_per_notional": buy_impact,
        "sell_impact_per_notional": sell_impact,
        "large_trade_count": large_trade_count,
    }


def _run_lengths(side: np.ndarray) -> np.ndarray:
    if len(side) == 0:
        return np.asarray([])
    change_points = np.flatnonzero(side[1:] != side[:-1]) + 1
    boundaries = np.concatenate(([0], change_points, [len(side)]))
    return np.diff(boundaries)


def _gini(values: np.ndarray) -> float:
    if len(values) == 0:
        return float("nan")
    sorted_values = np.sort(values)
    n = len(sorted_values)
    cumulative_total = float(np.sum(sorted_values))
    if cumulative_total <= 0.0:
        return 0.0
    index = np.arange(1, n + 1)
    return float((2.0 * np.sum(index * sorted_values) / (n * cumulative_total)) - (n + 1) / n)


def _binary_entropy(probability: float) -> float:
    clipped = min(max(probability, 0.0), 1.0)
    if clipped in {0.0, 1.0}:
        return 0.0
    return float(-(clipped * np.log2(clipped) + (1.0 - clipped) * np.log2(1.0 - clipped)))


__all__ = [
    "PUMP_FADE_AGGTRADES_MINUTE_FEATURES",
    "PUMP_FADE_AGGTRADES_MINUTE_SCHEMA_VERSION",
    "build_minute_aggtrades_features",
]
