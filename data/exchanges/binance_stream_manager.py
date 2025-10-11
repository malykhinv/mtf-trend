"""Binance streaming manager with weighted scheduling."""
from __future__ import annotations

import math
import threading
from typing import Any, Callable, Mapping, Optional

from config.stream_limits import (
    DEFAULT_BINANCE_STREAM_PROFILES,
    StreamLoadProfile,
)

from .base import ResyncReason, StreamBuffer
from .binance_stream_pool import _StreamWorkerPool


class BinanceStreamManager:
    """Coordinates Binance stream workers with weighted symbol allocation."""

    def __init__(
        self,
        *,
        endpoints_ws_base: str,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
        depth_stream_interval_ms: int = 100,
        profiles: Mapping[str, StreamLoadProfile] | None = None,
        expected_stream_weights: Mapping[str, float] | None = None,
    ) -> None:
        base = endpoints_ws_base.rstrip("/")
        if not base.endswith("/ws"):
            base = f"{base}/ws"
        self._ws_base = base
        interval = max(1, int(depth_stream_interval_ms))
        self._depth_stream_interval_ms = interval
        self._profiles = dict(DEFAULT_BINANCE_STREAM_PROFILES)
        if profiles is not None:
            self._profiles.update(profiles)
        self._expected_stream_weights = {
            key: max(float(value), 0.0)
            for key, value in (expected_stream_weights or {}).items()
        }

        self._lock = threading.Lock()
        self._pools: dict[str, _StreamWorkerPool] = {}
        self._pools["depth"] = self._create_pool(
            name=f"depth@{interval}ms",
            stream_suffix=f"depth@{interval}ms",
            profile=self._profiles["depth"],
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._pools["trades"] = self._create_pool(
            name="trades",
            stream_suffix="aggTrade",
            profile=self._profiles["trades"],
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._pools["book"] = self._create_pool(
            name="book_ticker",
            stream_suffix="bookTicker",
            profile=self._profiles["book"],
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )

        for stream_type, pool in self._pools.items():
            profile = self._profiles.get(stream_type)
            if profile is None:
                continue
            expected_weight = self._expected_stream_weights.get(stream_type, 0.0)
            if expected_weight <= 0:
                target = 1
            else:
                capacity = max(profile.max_streams_per_connection, 1)
                target = max(1, math.ceil(expected_weight / capacity))
            pool.ensure_workers(target)
        self._assigned_weights: dict[str, float] = {key: 0.0 for key in self._pools}

    @property
    def depth_stream_interval_ms(self) -> int:
        return self._depth_stream_interval_ms

    def register_depth(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
        *,
        weight: float = 1.0,
    ) -> Callable[[], None]:
        return self._register(
            "depth", symbol, buffer, on_message, on_error, weight=weight
        )

    def register_trades(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
        *,
        weight: float = 1.0,
    ) -> Callable[[], None]:
        return self._register(
            "trades", symbol, buffer, on_message, on_error, weight=weight
        )

    def register_book_ticker(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
        *,
        weight: float = 1.0,
    ) -> Callable[[], None]:
        return self._register(
            "book", symbol, buffer, on_message, on_error, weight=weight
        )

    def _create_pool(
        self,
        *,
        name: str,
        stream_suffix: str,
        profile: StreamLoadProfile,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]],
    ) -> _StreamWorkerPool:
        return _StreamWorkerPool(
            name=name,
            ws_base=self._ws_base,
            stream_suffix=stream_suffix,
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
            profile=profile,
        )

    def _register(
        self,
        stream_type: str,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]],
        *,
        weight: float,
    ) -> Callable[[], None]:
        pool = self._pools.get(stream_type)
        if pool is None:
            raise ValueError(f"Unknown stream type: {stream_type}")
        profile = self._profiles.get(stream_type)
        if profile is None:
            raise ValueError(f"No load profile configured for {stream_type}")
        with self._lock:
            current_total = self._assigned_weights.get(stream_type, 0.0)
            projected_total = current_total + weight
            capacity_per_worker = max(profile.max_streams_per_connection, 1)
            target_workers = max(1, math.ceil(projected_total / capacity_per_worker))
        if target_workers > pool.worker_count():
            pool.ensure_workers(target_workers)
        release = pool.register(
            symbol,
            buffer,
            on_message,
            on_error,
            weight=weight,
        )
        with self._lock:
            self._assigned_weights[stream_type] = projected_total

        def _release_wrapper() -> None:
            try:
                release()
            finally:
                with self._lock:
                    current = self._assigned_weights.get(stream_type, 0.0) - weight
                    self._assigned_weights[stream_type] = max(current, 0.0)

        return _release_wrapper


__all__ = ["BinanceStreamManager"]
