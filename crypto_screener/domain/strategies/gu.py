from __future__ import annotations

import numpy as np

from crypto_screener.config.gu_config import GuConfig, gu_cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_type import CascadeType
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Setup, Unfilled
from crypto_screener.domain.models.setup_data import Gu
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.strategies.strategy import (
    Strategy,
    StrategyRuntimeConfig,
    StrategyVolumeConfig,
)


class GuStrategy(Strategy):
    name = "ГУ"

    def __init__(self, config: GuConfig = gu_cfg, direction: CascadeType = CascadeType.LONG) -> None:
        self._config = config
        self._direction = direction

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
            open_swings=None,
        )
        setup = Unfilled(data=setup_data)

        if len(bars) < self._config.LEVEL_MIN_SWINGS:
            return setup

        avg_range = self._calculate_average_range(bars, self._config.LEVEL_ATR_WINDOW)
        if avg_range <= 0:
            return setup

        swing_type = SwingType.LOW if self._direction is CascadeType.LONG else SwingType.HIGH
        open_swings = self._get_open_swings(bars, swing_type)
        setup.data.open_swings = open_swings

        level = self._find_nearest_level(open_swings, avg_range)
        if level is None:
            return setup
        setup.data.level_price = level

        capture_distance = avg_range * self._config.LEVEL_CAPTURE_DISTANCE_NATR
        cross_eps = avg_range * self._config.LEVEL_CROSS_EPS_NATR
        current_price = bars[-1].close
        price_delta = current_price - level

        if abs(price_delta) > capture_distance:
            return setup

        if self._direction is CascadeType.LONG and price_delta < -cross_eps:
            return setup
        if self._direction is CascadeType.SHORT and price_delta > cross_eps:
            return setup

        return Capture(data=setup.data)

    def _find_nearest_level(self, swings: list[Swing], avg_range: float) -> float | None:
        tolerance = avg_range * self._config.LEVEL_TOLERANCE_NATR
        if tolerance <= 0 or len(swings) < self._config.LEVEL_MIN_SWINGS:
            return None

        clusters = self._cluster_swings_by_price(swings, tolerance, self._config.LEVEL_MIN_SWINGS)
        if not clusters:
            return None

        def latest_time(cluster: list[Swing]) -> float:
            return max(swing.time.timestamp() for swing in cluster)

        latest_cluster = max(clusters, key=latest_time)
        prices = [swing.extremum_price for swing in latest_cluster]
        return float(np.mean(prices)) if prices else None

    @staticmethod
    def _get_open_swings(bars: list[Bar], swing_type: SwingType) -> list[Swing]:
        return [
            bar.swing
            for bar in bars
            if bar.swing is not None
            and bar.swing.is_open
            and bar.swing.type == swing_type
        ]

    @staticmethod
    def _cluster_swings_by_price(
            swings: list[Swing],
            tolerance: float,
            min_count: int,
    ) -> list[list[Swing]]:
        if not swings:
            return []

        sorted_swings = sorted(swings, key=lambda swing: swing.extremum_price)
        clusters: list[list[Swing]] = []
        current_cluster: list[Swing] = []

        for swing in sorted_swings:
            if not current_cluster:
                current_cluster.append(swing)
                continue

            median_price = float(np.median([s.extremum_price for s in current_cluster]))
            if abs(swing.extremum_price - median_price) <= tolerance:
                current_cluster.append(swing)
            else:
                clusters.append(current_cluster)
                current_cluster = [swing]

        if current_cluster:
            clusters.append(current_cluster)

        return [cluster for cluster in clusters if len(cluster) >= min_count]

    @staticmethod
    def _calculate_average_range(bars: list[Bar], window: int) -> float:
        if not bars or window <= 0:
            return 0.0
        window = min(window, len(bars))
        ranges = [bar.high - bar.low for bar in bars[-window:]]
        return float(np.mean(ranges)) if ranges else 0.0

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
