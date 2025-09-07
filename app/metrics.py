"""Aggregation of streaming market data into per-symbol metrics."""

from __future__ import annotations

import asyncio
import math
from typing import Deque

import constants
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent
from domain.services.symbol_registry import SymbolRegistry
from domain.services.ws_client import WsClient


class MetricAggregator:
    """Update ``SymbolRegistry`` state based on incoming market data events."""

    def __init__(self, registry: SymbolRegistry) -> None:
        self._registry = registry
        self._alpha = 1 - math.exp(-math.log(2) / constants.EWMA_HALF_LIFE_MIN)

    @staticmethod
    def update_ewma(
        value: float, mean: float, std: float, alpha: float
    ) -> tuple[float, float]:
        """Return updated EWMA mean and std for ``value``."""
        if mean == 0.0 and std == 0.0:
            return value, 0.0
        var = std**2
        delta = value - mean
        mean += alpha * delta
        var = (1 - alpha) * (var + alpha * delta * delta)
        return mean, var ** 0.5

    @staticmethod
    def zscore(
        value: float, ewma_mean: float, ewma_std: float, window: Deque[float]
    ) -> float:
        """Return z-score of ``value`` using precomputed EWMA statistics."""
        if len(window) < constants.Z_BASE_WINDOW_MIN or ewma_std <= 0:
            return 0.0
        return (value - ewma_mean) / ewma_std

    @staticmethod
    def zscore_window(value: float, window: Deque[float]) -> float:
        """Return z-score of ``value`` within ``window`` values."""
        if len(window) < constants.Z_BASE_WINDOW_MIN:
            return 0.0
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        std = var ** 0.5
        return 0.0 if std == 0 else (value - mean) / std

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def process_trade(self, trade: AggTrade) -> None:
        state = self._registry.get(trade.symbol)
        metrics = state.metrics

        # ------------------------ price & volume z-scores -----------------
        price_win = metrics.price_win
        vol_win = metrics.vol_win
        price_win.append(trade.price)
        vol_win.append(trade.quantity)
        metrics.price_ewma_mean, metrics.price_ewma_std = self.update_ewma(
            trade.price, metrics.price_ewma_mean, metrics.price_ewma_std, self._alpha
        )
        metrics.vol_ewma_mean, metrics.vol_ewma_std = self.update_ewma(
            trade.quantity, metrics.vol_ewma_mean, metrics.vol_ewma_std, self._alpha
        )
        z_px = self.zscore(
            trade.price, metrics.price_ewma_mean, metrics.price_ewma_std, price_win
        )
        z_vol = self.zscore(
            trade.quantity, metrics.vol_ewma_mean, metrics.vol_ewma_std, vol_win
        )

        # ----------------------- candle construction ----------------------
        prev_low = metrics.low
        start_ts = metrics.start_ts
        if trade.timestamp - start_ts >= 60_000 or start_ts == 0:
            start_ts = trade.timestamp
            metrics.high = trade.price
            metrics.low = trade.price
            low_break = False
        else:
            metrics.high = max(metrics.high, trade.price)
            metrics.low = min(metrics.low, trade.price)
            low_break = prev_low > 0 and trade.price < prev_low
        metrics.start_ts = start_ts
        metrics.end_ts = trade.timestamp

        high = metrics.high
        low = metrics.low
        rng = high - low

        total_vol = sum(vol_win)
        avwap = (
            sum(p * v for p, v in zip(price_win, vol_win)) / total_vol
            if total_vol > 0
            else 0.0
        )
        avwap_loss = total_vol > 0 and trade.price < avwap

        std_price = metrics.price_ewma_std
        delta_sigma = rng / std_price if std_price > 0 else 0.0
        delta_abs = (high / low - 1.0) * 100 if low > 0 else 0.0
        close_pos = (trade.price - low) / rng if rng > 0 else 0.0

        metrics.z_px = z_px
        metrics.z_vol = z_vol
        metrics.delta_price_sigma_mult = delta_sigma
        metrics.delta_price_abs_pct = delta_abs
        metrics.close_pos = close_pos
        metrics.last_price = trade.price
        metrics.low_break = low_break
        metrics.avwap_loss = avwap_loss
        if low_break or avwap_loss:
            metrics.entry_price = trade.price
            metrics.direction = Side.SHORT
        else:
            metrics.entry_price = 0.0
            metrics.direction = None

        self._registry.update(trade.symbol, state)

    def process_depth(self, depth: DepthSnapshot) -> None:
        state = self._registry.get(depth.symbol)
        metrics = state.metrics
        if depth.bids and depth.asks:
            best_bid = depth.bids[0][0]
            best_ask = depth.asks[0][0]
            mid = (best_bid + best_ask) / 2.0
            last_price = metrics.last_price if metrics.last_price > 0 else mid
            premium_pct = (
                (mid - last_price) / last_price * 100.0 if last_price > 0 else 0.0
            )
            metrics.best_bid = best_bid
            metrics.best_ask = best_ask
            metrics.premium_pct = premium_pct
        self._registry.update(depth.symbol, state)

    def process_liquidation(self, liq: LiquidationEvent) -> None:
        state = self._registry.get(liq.symbol)
        metrics = state.metrics
        liq_win = metrics.liq_win
        liq_win.append(liq.quantity)
        liqs_z = self.zscore_window(liq.quantity, liq_win)
        metrics.liqs_z = liqs_z
        metrics.last_liq_side = liq.side
        self._registry.update(liq.symbol, state)


async def bar_maker(ws: WsClient, registry: SymbolRegistry) -> None:
    """Build per-symbol metrics from websocket events."""

    aggregator = MetricAggregator(registry)
    while True:
        trade = ws.next_agg_trade()
        if trade:
            aggregator.process_trade(trade)

        depth = ws.next_depth()
        if depth:
            aggregator.process_depth(depth)

        liq = ws.next_liquidation()
        if liq:
            aggregator.process_liquidation(liq)

        await asyncio.sleep(0)

