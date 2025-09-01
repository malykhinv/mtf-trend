# data/market_radar.py
from __future__ import annotations

import asyncio
import json
import threading
import queue
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Set

import websockets
from data.loader import Loader
from domain.models.timeframe import Timeframe
from utils.logger import log, logw  # + logs

from config.constants import (
    RADAR_QUEUE_MAX,
    RADAR_WATCH_ONLY_USDT,
    RADAR_USE_FUTURES,
    RADAR_USE_EMA_FAN,
    RADAR_EMA_PERIODS,
    RADAR_CROSS_WINDOW_SEC,
    RADAR_ZERO_HOLD_SEC,
    RADAR_REARM_SEC,
    TIMEZONE,
)


# --- Event model ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RadarEvent:
    symbol: str
    t0: datetime | None = None  # always datetime (tz-aware) or None


# --- EMA incremental calc ------------------------------------------------

class _EmaState:
    __slots__ = ("value", "alpha", "is_init")

    def __init__(self, period: int):
        self.value: float = 0.0
        self.alpha: float = 2.0 / (period + 1.0)
        self.is_init: bool = False

    def update(self, price: float) -> float:
        if not self.is_init:
            self.value = price
            self.is_init = True
        else:
            self.value = self.alpha * price + (1.0 - self.alpha) * self.value
        return self.value


# --- Market Radar --------------------------------------------------------

class MarketRadar:
    """
    Binance miniTicker radar that watches EMA fan 'zero-violation' moments and emits RadarEvent.
    All time-related fields are tz-aware datetimes; windows are timedeltas.

    Watchlist support:
      - Pass an explicit set of symbols at construction, or call set_watchlist() later.
      - If watchlist is None, all symbols are allowed (subject to RADAR_WATCH_ONLY_USDT).
    """

    def __init__(self, watchlist: Optional[Set[str]] = None) -> None:
        # Public events queue
        self.events: "queue.Queue[RadarEvent]" = queue.Queue(maxsize=RADAR_QUEUE_MAX)

        # Optional watchlist
        self._watchlist: Optional[Set[str]] = set(watchlist) if watchlist else None

        # Per-symbol EMA states and last signs for pairs
        self._ema_states: dict[str, dict[int, _EmaState]] = defaultdict(dict)
        self._last_signs: dict[str, dict[tuple[int, int], int]] = defaultdict(dict)
        self._crosses: dict[str, dict[tuple[int, int], deque[datetime]]] = defaultdict(lambda: defaultdict(deque))

        # 'Zero-violations' hold timers and rate-limits (tz-aware datetimes)
        self._zero_since: dict[str, datetime | None] = {}  # when zero-violations started
        self._last_emit: dict[str, datetime] = defaultdict(lambda: datetime(1970, 1, 1, tzinfo=TIMEZONE))
        self._last_high_cross_time: dict[str, datetime] = defaultdict(lambda: datetime(1970, 1, 1, tzinfo=TIMEZONE))

        # Track when ascending EMA fan started per symbol
        self._fan_since: dict[str, datetime | None] = {}

        # Precomputed windows
        self._CROSS_WINDOW = timedelta(seconds=RADAR_CROSS_WINDOW_SEC)
        self._ZERO_HOLD = timedelta(seconds=RADAR_ZERO_HOLD_SEC)
        self._REARM = timedelta(seconds=RADAR_REARM_SEC)

        # Telemetry
        self._msg_count: int = 0
        self._event_count: int = 0
        self._last_telemetry: datetime = datetime.now(tz=TIMEZONE)

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

        # data loader for seeding EMA from real candles
        self._loader: Loader = Loader()
        self._seeded: set[str] = set()

    # -- public API -------------------------------------------------------
    def set_watchlist(self, symbols: Iterable[str] | None) -> None:
        """Replace the current watchlist. None -> disable filter."""
        self._watchlist = set(symbols) if symbols is not None else None
        wl_size = len(self._watchlist) if self._watchlist is not None else -1
        log(f"radar: set_watchlist size={wl_size if wl_size >= 0 else 'ALL'}")

    def get_watchlist(self) -> Optional[Set[str]]:
        return set(self._watchlist) if self._watchlist is not None else None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        mode = "futures" if RADAR_USE_FUTURES else "spot"
        wl_size = len(self._watchlist) if self._watchlist is not None else -1
        log(f"radar: starting mode={mode}, watchlist={wl_size if wl_size >= 0 else 'ALL'} symbols")  # + log
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        log("radar: stopped")  # + log

    # -- internals --------------------------------------------------------
    def _run(self) -> None:
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        url = (
            "wss://fstream.binance.com/stream?streams=!miniTicker@arr"
            if RADAR_USE_FUTURES
            else "wss://stream.binance.com:9443/stream?streams=!miniTicker@arr"
        )
        log(f"radar: connecting {url}")  # + log
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    log("radar: connected")  # + log
                    while not self._stop.is_set():
                        msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        self._on_ws_message(msg)
            except asyncio.TimeoutError:
                logw("radar: timeout, reconnect in 1s")  # + log
                await asyncio.sleep(1.0)
            except Exception as e:
                logw(f"radar: error {type(e).__name__}: {e} — reconnect in 1s")  # + log
                await asyncio.sleep(1.0)

    def _on_ws_message(self, msg: str) -> None:
        now = datetime.now(tz=TIMEZONE)
        try:
            payload = json.loads(msg)
        except Exception as e:
            logw(f"radar: bad json: {e}")  # + log
            return

        # Combined stream format: {"stream":"!miniTicker@arr","data":[ {...}, ... ]}
        data = payload.get("data")
        if not isinstance(data, list):
            return

        wl = self._watchlist  # snapshot for speed
        processed = 0

        for item in data:
            try:
                sym = item.get("s")
                # Close price as string per Binance docs
                p_str = item.get("c")
                if not isinstance(sym, str) or not isinstance(p_str, str):
                    continue
                if RADAR_WATCH_ONLY_USDT and not sym.endswith("USDT"):
                    continue
                if wl is not None and sym not in wl:
                    continue
                price = float(p_str)
            except Exception:
                continue
            self._on_price(sym, price, now)
            processed += 1

        # telemetry every ~10s
        self._msg_count += processed
        if (now - self._last_telemetry) >= timedelta(seconds=10):
            symbols_tracked = len(self._ema_states)
            qsize = 0
            try:
                qsize = self.events.qsize()
            except Exception:
                pass
            self._msg_count = 0
            self._last_telemetry = now

    def _seed_ema_states(self, sym: str) -> None:
        """Seed EMA states from recent M1 candles (SMA start, then EMA). Runs once per symbol."""
        if sym in self._seeded:
            return
        try:
            bars = self._loader.fetch_ohlcvi(sym, Timeframe.M1, limit=250, use_cache=True, ttl_minutes=1)
            closes = [b.close for b in bars] if bars else []
            if len(closes) < 2:
                self._seeded.add(sym)
                return
            states = self._ema_states[sym]
            for period in RADAR_EMA_PERIODS:
                # ensure state exists
                st = states.get(period)
                if st is None:
                    st = _EmaState(period)
                    states[period] = st
                p = int(period)
                if p <= 0:
                    continue
                k = 2.0 / (p + 1.0)
                start = sum(closes[:min(p, len(closes))]) / float(min(p, len(closes)))
                ema_val = start
                for v in closes[1:]:
                    ema_val = k * v + (1.0 - k) * ema_val
                st.value = float(ema_val)
                st.is_init = True
            self._seeded.add(sym)
        except Exception as e:
            self._seeded.add(sym)
            logw(f"radar: seed ema failed for {sym}: {e}")

    def _ensure_ema_states(self, sym: str) -> dict[int, _EmaState]:
        s = self._ema_states[sym]
        for period in RADAR_EMA_PERIODS:
            if period not in s:
                s[period] = _EmaState(period)
        self._seed_ema_states(sym)
        return s

    def _on_price(self, s: str, p: float, now: datetime) -> None:
        if not RADAR_USE_EMA_FAN:
            return

        states = self._ensure_ema_states(s)
        emas = {}
        for period, st in states.items():
            emas[period] = st.update(p)

        # Ascending EMA fan check (e.g., 20>50>100>200)
        periods = list(RADAR_EMA_PERIODS)
        eps = 1e-12
        is_fan_up = all(emas[periods[i]] > emas[periods[i+1]] + eps for i in range(len(periods)-1))
        # Track when the fan became strictly ascending
        if is_fan_up:
            self._fan_since[s] = self._fan_since.get(s) or now
        else:
            self._fan_since[s] = None

        # Maintain sign per adjacent pair (a,b) with a<b periods
        signs = self._last_signs[s]
        cross_q = self._crosses[s]

        for i in range(len(RADAR_EMA_PERIODS) - 1):
            a = RADAR_EMA_PERIODS[i]
            b = RADAR_EMA_PERIODS[i + 1]
            diff = emas[a] - emas[b]
            sign = 0 if abs(diff) < 1e-12 else (1 if diff > 0 else -1)
            prev = signs.get((a, b))
            if prev is None:
                # first observation: initialize
                signs[(a, b)] = sign
            elif sign != prev:
                # sign change -> count as a 'cross' moment
                cross_q[(a, b)].append(now)
                signs[(a, b)] = sign

        fan_hold_ok = (self._fan_since.get(s) is not None) and ((now - self._fan_since[s]) >= self._ZERO_HOLD)
        rearm_ok = ((now - self._last_emit[s]) >= self._REARM)

        # Simple extra filter: price above fast EMA
        ema_fast = emas[RADAR_EMA_PERIODS[0]]
        price_above_fast = p >= ema_fast

        if fan_hold_ok and rearm_ok and price_above_fast:
            evt = RadarEvent(symbol=s, t0=now)
            try:
                self.events.put_nowait(evt)
                self._last_emit[s] = now
                self._event_count += 1  # + log metric
                log(f"Событие на {s}")  # + log
            except queue.Full:
                logw("radar: events queue full, dropping event")  # + log
