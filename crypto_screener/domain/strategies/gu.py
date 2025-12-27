from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from crypto_screener.config.gu_config import GuConfig, gu_cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_type import CascadeType
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Setup, Unfilled
from crypto_screener.domain.models.setup_data import Gu
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.strategies.strategy import (
    Strategy,
    StrategyRuntimeConfig,
    StrategyVolumeConfig,
)


@dataclass(frozen=True)
class Extremum:
    time: datetime
    price: float


class GuStrategy(Strategy):
    name = "ГУ"

    def __init__(
            self,
            config: GuConfig = gu_cfg,
            direction: CascadeType = CascadeType.LONG
    ) -> None:
        self._config = config
        self._direction = direction

    @property
    def allowed_timeframes(self) -> tuple[Timeframe, ...]:
        return self._config.ALLOWED_TIMEFRAMES

    def detect_setup(
            self,
            symbol: str,
            bars: list[Bar],
            timeframe: Timeframe,
            context: Context,
    ) -> Setup:
        bars = bars[-self._config.LEVEL_LOOKBACK_BARS:]
        setup_data = Gu(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            direction=self._direction,
            level_price=None,
            open_extremums=None,
            atr=None,
            current_price=None,
            distance_to_level=None,
            distance_atr_ratio=None,
        )
        setup = Unfilled(data=setup_data)

        if len(bars) < self._config.LEVEL_MIN_SWINGS:
            return setup

        avg_range = self._calculate_average_range(bars, self._config.LEVEL_ATR_WINDOW)
        setup.data.atr = avg_range
        if avg_range <= 0:
            return setup

        open_extremums = self._find_open_extremums(bars)
        setup.data.open_extremums = [
            (extremum.time, extremum.price) for extremum in open_extremums
        ]

        level = self._find_nearest_level(open_extremums, avg_range)
        if level is None:
            return setup
        setup.data.level_price = level

        capture_distance = avg_range * self._config.LEVEL_CAPTURE_DISTANCE_NATR
        cross_eps = avg_range * self._config.LEVEL_CROSS_EPS_NATR
        current_price = bars[-1].close
        setup.data.current_price = current_price
        price_delta = current_price - level
        setup.data.distance_to_level = price_delta
        setup.data.distance_atr_ratio = price_delta / avg_range if avg_range else None

        if abs(price_delta) > capture_distance:
            return setup

        if self._direction is CascadeType.LONG and price_delta < -cross_eps:
            return setup
        if self._direction is CascadeType.SHORT and price_delta > cross_eps:
            return setup

        return Capture(data=setup.data)

    def _find_nearest_level(self, extremums: list[Extremum], avg_range: float) -> float | None:
        tolerance = avg_range * self._config.LEVEL_TOLERANCE_NATR
        if tolerance <= 0 or len(extremums) < self._config.LEVEL_MIN_SWINGS:
            return None

        clusters = self._cluster_extremums_by_price(
            extremums, tolerance, self._config.LEVEL_MIN_SWINGS
        )
        if not clusters:
            return None

        def latest_time(cluster: list[Extremum]) -> datetime:
            return max(extremum.time for extremum in cluster)

        latest_cluster = max(clusters, key=latest_time)
        ordered_cluster = sorted(latest_cluster, key=lambda extremum: extremum.time)
        return float(ordered_cluster[0].price) if ordered_cluster else None

    def _find_open_extremums(self, bars: list[Bar]) -> list[Extremum]:
        extremums: list[Extremum] = []
        for index, bar in enumerate(bars):
            price = self._get_directional_extremum(bar)
            overlapped = any(later_bar.low <= price <= later_bar.high for later_bar in bars[index + 1 :])
            if not overlapped:
                extremums.append(Extremum(time=bar.time, price=price))

        return extremums

    def _get_directional_extremum(self, bar: Bar) -> float:
        return bar.low if self._direction is CascadeType.LONG else bar.high

    @staticmethod
    def _cluster_extremums_by_price(
            extremums: list[Extremum],
            tolerance: float,
            min_count: int,
    ) -> list[list[Extremum]]:
        if not extremums:
            return []

        sorted_extremums = sorted(extremums, key=lambda extremum: extremum.price)
        clusters: list[list[Extremum]] = []
        current_cluster: list[Extremum] = []

        for extremum in sorted_extremums:
            if not current_cluster:
                current_cluster.append(extremum)
                continue

            median_price = float(np.median([e.price for e in current_cluster]))
            if abs(extremum.price - median_price) <= tolerance:
                current_cluster.append(extremum)
            else:
                clusters.append(current_cluster)
                current_cluster = [extremum]

        if current_cluster:
            clusters.append(current_cluster)

        return [cluster for cluster in clusters if len(cluster) >= min_count]

    @staticmethod
    def _calculate_average_range(bars: list[Bar], window: int) -> float:
        if len(bars) < 2 or window <= 0:
            return 0.0
        window = min(window, len(bars) - 1)
        start_index = len(bars) - window

        true_ranges: list[float] = []
        for index in range(start_index, len(bars)):
            bar = bars[index]
            prev_close = bars[index - 1].close
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
            true_ranges.append(true_range)

        return float(np.mean(true_ranges)) if true_ranges else 0.0

    def get_runtime_config(self) -> StrategyRuntimeConfig:
        return StrategyRuntimeConfig(
            capture_timeout_multiplier=self._config.CAPTURE_TIMEOUT_MULTIPLIER,
            risk_per_trade_usdt=self._config.RISK_PER_TRADE_USDT,
            min_position_notional_usdt=self._config.MIN_POSITION_NOTIONAL_USDT,
            min_position_quantity=self._config.MIN_POSITION_QUANTITY,
        )

    def get_volume_config(self) -> StrategyVolumeConfig:
        return StrategyVolumeConfig(
            min_side_bars=self._config.VOLUME_TRIM_SIDE_BARS_MIN,
            high_volume_factor=self._config.HIGH_VOLUME_THRESHOLD,
            min_high_fraction=self._config.HIGH_VOLUME_FRACTION_MIN,
        )
