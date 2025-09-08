"""Aggregation of streaming market data into per-symbol metrics."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque

import constants
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent
from domain.services.symbol_registry import SymbolRegistry
from domain.ports.ws_client import WsClient
from .metric_utils import update_ewma, zscore, zscore_window
from . import baseline_store


class MetricAggregator:
    """Update ``SymbolRegistry`` state based on incoming market data events."""

    def __init__(self, registry: SymbolRegistry) -> None:
        self._registry = registry
        self._alpha = 1 - math.exp(-math.log(2) / constants.EWMA_HALF_LIFE_MIN)

    def _update_price_volume_metrics(
        self, trade: AggTrade, metrics
    ) -> tuple[float, float]:
        price_win = metrics.price_win
        vol_win = metrics.vol_win
        price_win.append(trade.price)
        vol_win.append(trade.quantity)
        metrics.price_ewma_mean, metrics.price_ewma_std = update_ewma(
            trade.price, metrics.price_ewma_mean, metrics.price_ewma_std, self._alpha
        )
        metrics.vol_ewma_mean, metrics.vol_ewma_std = update_ewma(
            trade.quantity, metrics.vol_ewma_mean, metrics.vol_ewma_std, self._alpha
        )
        z_px = zscore(
            trade.price, metrics.price_ewma_mean, metrics.price_ewma_std, price_win
        )
        z_vol = zscore(
            trade.quantity, metrics.vol_ewma_mean, metrics.vol_ewma_std, vol_win
        )
        return z_px, z_vol

    def _update_candle(
        self, trade: AggTrade, metrics
    ) -> tuple[float, float, float, bool, bool]:
        price_win = metrics.price_win
        vol_win = metrics.vol_win
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

        return delta_sigma, delta_abs, close_pos, low_break, avwap_loss

    def _update_entry_flags(
        self,
        trade: AggTrade,
        metrics,
        z_px: float,
        z_vol: float,
        delta_sigma: float,
        delta_abs: float,
        close_pos: float,
        low_break: bool,
        avwap_loss: bool,
    ) -> None:
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


    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def process_trade(self, trade: AggTrade) -> None:
        state = self._registry.get(trade.symbol)
        metrics = state.metrics
        z_px, z_vol = self._update_price_volume_metrics(trade, metrics)
        delta_sigma, delta_abs, close_pos, low_break, avwap_loss = self._update_candle(
            trade, metrics
        )
        self._update_entry_flags(
            trade, metrics, z_px, z_vol, delta_sigma, delta_abs, close_pos, low_break, avwap_loss
        )

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
        liqs_z = zscore_window(liq.quantity, liq_win)
        metrics.liqs_z = liqs_z
        metrics.last_liq_side = liq.side
        self._registry.update(liq.symbol, state)


async def bar_maker(ws: WsClient, registry: SymbolRegistry) -> None:
    """Build per-symbol metrics from websocket events."""

    aggregator = MetricAggregator(registry)
    symbols = registry.all_symbols()
    baselines = baseline_store.load(symbols)
    for sym in symbols:
        state = registry.get(sym)
        price_win, vol_win = baselines.get(
            sym,
            (
                deque(maxlen=constants.Z_BASE_WINDOW_MIN),
                deque(maxlen=constants.Z_BASE_WINDOW_MIN),
            ),
        )
        state.metrics.price_win = price_win
        state.metrics.vol_win = vol_win

    next_persist = time.time() + 60
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

        if time.time() >= next_persist:
            baseline_store.save(
                {
                    sym: (
                        registry.get(sym).metrics.price_win,
                        registry.get(sym).metrics.vol_win,
                    )
                    for sym in symbols
                }
            )
            next_persist = time.time() + 60

        await asyncio.sleep(0)

