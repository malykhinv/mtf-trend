"""Самостоятельное ядро стратегии bee_bite с явной конечной машиной состояний."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from domain.enums.entry_trigger import EntryTrigger
from domain.enums.position_side import PositionSide
from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
from strategy.bee_bite.config import BeeBiteParams
from strategy.breakout.indicators.level_detector import LevelDetector
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class BeeBiteState(StrEnum):
    IDLE = "IDLE"
    SEEK_PUMP = "SEEK_PUMP"
    SEEK_RANGE = "SEEK_RANGE"
    RANGE_LOCKED = "RANGE_LOCKED"
    BREAK_ACTIVE = "BREAK_ACTIVE"
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    IN_TRADE = "IN_TRADE"


@dataclass(slots=True)
class SetupContext:
    side: PositionSide
    level_price: float
    breakout_idx: int
    breakout_timestamp: int
    retest_idx: int | None = None
    retest_timestamp: int | None = None
    range_high: float | None = None
    range_low: float | None = None
    pump_height: float = 0.0
    atr_pre: float = 0.0
    atr_bg: float = 0.0
    core_width: float = 0.0


class BeeBiteEngine:
    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

    def __init__(self) -> None:
        self._detector = LevelDetector()
        self._last_generation_diagnostics: dict[str, object] = {}

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        diagnostics = self._last_generation_diagnostics.copy()
        self._last_generation_diagnostics = {}
        return diagnostics

    def validate_config(self, params: BeeBiteParams) -> None:
        if params.bite_lookback < 5:
            raise ValueError("bite_lookback должен быть >= 5")
        if params.bite_confirmation_bars < 1:
            raise ValueError("bite_confirmation_bars должен быть >= 1")

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        missing = [col for col in self.REQUIRED_COLUMNS if col not in data.columns]
        if missing:
            raise ValueError(f"Отсутствуют обязательные колонки: {missing}")

        prepared = data.copy()
        prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp"])
        prepared["timestamp"] = prepared["timestamp"].astype("int64")
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        for col in ("open", "high", "low", "close", "volume"):
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
        prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
        return prepared

    def generate_events(self, data: pd.DataFrame, params: BeeBiteParams) -> list[TradeResult]:
        prepared = self.prepare_data(data)
        annotated = self._append_volatility(prepared)
        return self._run_fsm(annotated=annotated, higher_levels=pd.DataFrame(), params=params)

    def generate_events_multi_tf(self, *, mtf_frames: SymbolMtfFrames, params: BeeBiteParams) -> list[TradeResult]:
        higher = self.prepare_data(mtf_frames.get_frame(params.levels_timeframe))[["timestamp", "high", "low", "close"]].copy()
        lower = self.prepare_data(mtf_frames.get_frame(params.entry_timeframe))[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        annotated = self._append_volatility(lower)
        higher_levels = self._detector.detect(higher_base=higher, lookback=params.bite_lookback)
        return self._run_fsm(annotated=annotated, higher_levels=higher_levels, params=params)

    def _run_fsm(self, *, annotated: pd.DataFrame, higher_levels: pd.DataFrame, params: BeeBiteParams) -> list[TradeResult]:
        diagnostics: dict[str, object] = {
            "states": [BeeBiteState.IDLE.value],
            "trades_generated": 0,
            "symbol": params.symbol,
        }
        if annotated.empty:
            self._last_generation_diagnostics = diagnostics
            return []

        state = BeeBiteState.IDLE
        setup: SetupContext | None = None
        trades: list[TradeResult] = []
        level_idx = 0

        rows = list(annotated.itertuples(index=False))
        levels = list(higher_levels.itertuples(index=False)) if not higher_levels.empty else []

        i = 0
        while i < len(rows):
            row = rows[i]
            timestamp = int(row.timestamp)
            price_close = float(row.close)
            level_high: float | None = None
            level_low: float | None = None
            while level_idx < len(levels) and int(levels[level_idx].timestamp) <= timestamp:
                level_high = float(levels[level_idx].level_high)
                level_low = float(levels[level_idx].level_low)
                level_idx += 1

            if state == BeeBiteState.IDLE:
                state = BeeBiteState.SEEK_PUMP
                diagnostics["states"].append(state.value)

            if state == BeeBiteState.SEEK_PUMP:
                pump_signal = self._resolve_pump_signal(rows=rows, idx=i, params=params)
                if pump_signal is not None:
                    side, level_price, pump_height, atr_pre = pump_signal
                    setup = SetupContext(
                        side=side,
                        level_price=level_price,
                        breakout_idx=i,
                        breakout_timestamp=timestamp,
                        pump_height=pump_height,
                        atr_pre=atr_pre,
                    )
                    state = BeeBiteState.SEEK_RANGE
                    diagnostics["states"].append(state.value)

            if state == BeeBiteState.SEEK_RANGE and setup is not None:
                window_len = max(6, params.bite_lookback)
                max_wait = max(1, self._hours_to_candles(params.bite_retest_window_hours, params.entry_timeframe))
                elapsed = i - setup.breakout_idx
                if elapsed > max_wait:
                    setup = None
                    state = BeeBiteState.IDLE
                    diagnostics["states"].append(state.value)
                elif elapsed >= window_len:
                    range_setup = self._freeze_range(rows=rows, end_idx=i, setup=setup, params=params, window_len=window_len)
                    if range_setup is not None:
                        setup.range_low, setup.range_high, setup.core_width, setup.atr_bg = range_setup
                        setup.retest_idx = i
                        setup.retest_timestamp = timestamp
                        state = BeeBiteState.RANGE_LOCKED
                        diagnostics["states"].append(state.value)
                    else:
                        setup = None
                        state = BeeBiteState.IDLE
                        diagnostics["states"].append(state.value)

            if state == BeeBiteState.RANGE_LOCKED and setup is not None:
                state = BeeBiteState.BREAK_ACTIVE
                diagnostics["states"].append(state.value)

            if state == BeeBiteState.BREAK_ACTIVE and setup is not None and setup.retest_idx is not None:
                if params.bite_entry_trigger == EntryTrigger.IMMEDIATE:
                    state = BeeBiteState.ENTRY_SIGNAL
                    diagnostics["states"].append(state.value)
                else:
                    confirm_idx = setup.retest_idx + params.bite_confirmation_bars
                    if i >= confirm_idx:
                        ok = (setup.side == PositionSide.LONG and price_close > setup.level_price) or (
                            setup.side == PositionSide.SHORT and price_close < setup.level_price
                        )
                        if ok:
                            state = BeeBiteState.ENTRY_SIGNAL
                            diagnostics["states"].append(state.value)

            if state == BeeBiteState.ENTRY_SIGNAL and setup is not None and setup.retest_idx is not None:
                trade, exit_idx = self._simulate_trade(rows=rows, entry_idx=i, setup=setup, params=params)
                if trade is not None:
                    trades.append(trade)
                    diagnostics["trades_generated"] = int(diagnostics["trades_generated"]) + 1
                i = max(i, exit_idx)
                state = BeeBiteState.IN_TRADE
                diagnostics["states"].append(state.value)

            if state == BeeBiteState.IN_TRADE:
                setup = None
                state = BeeBiteState.IDLE
                diagnostics["states"].append(state.value)

            i += 1

        self._last_generation_diagnostics = diagnostics
        return trades

    def _simulate_trade(
        self,
        *,
        rows: list[object],
        entry_idx: int,
        setup: SetupContext,
        params: BeeBiteParams,
    ) -> tuple[TradeResult | None, int]:
        entry_row = rows[entry_idx]
        entry_price = float(entry_row.close)
        if setup.range_low is None or setup.range_high is None:
            return None, entry_idx

        if setup.side == PositionSide.LONG:
            stop = min(setup.range_low, setup.level_price)
            risk = max(entry_price - stop, entry_price * 0.0001)
            tp2 = entry_price + risk * params.bite_min_rr * params.bite_tp2_mult
        else:
            stop = max(setup.range_high, setup.level_price)
            risk = max(stop - entry_price, entry_price * 0.0001)
            tp2 = entry_price - risk * params.bite_min_rr * params.bite_tp2_mult

        limit = len(rows) - 1
        if params.bite_t_max_in_trade is not None:
            limit = min(limit, entry_idx + max(1, params.bite_t_max_in_trade))

        result_type = TradeResultType.BE
        exit_price = entry_price
        exit_idx = limit
        for idx in range(entry_idx + 1, limit + 1):
            row = rows[idx]
            low = float(row.low)
            high = float(row.high)
            close = float(row.close)
            if setup.side == PositionSide.LONG:
                if low <= stop:
                    exit_price = stop
                    result_type = TradeResultType.SL
                    exit_idx = idx
                    break
                if high >= tp2:
                    exit_price = tp2
                    result_type = TradeResultType.TP2
                    exit_idx = idx
                    break
            else:
                if high >= stop:
                    exit_price = stop
                    result_type = TradeResultType.SL
                    exit_idx = idx
                    break
                if low <= tp2:
                    exit_price = tp2
                    result_type = TradeResultType.TP2
                    exit_idx = idx
                    break
            exit_price = close

        direction = 1.0 if setup.side == PositionSide.LONG else -1.0
        pnl = (exit_price - entry_price) * direction
        pnl_percent = 0.0 if entry_price == 0 else (pnl / entry_price) * 100.0
        trade = TradeResult(
            entry_price=Price(entry_price),
            exit_price=Price(exit_price),
            entry_timestamp_ms=int(entry_row.timestamp),
            exit_timestamp_ms=int(rows[exit_idx].timestamp),
            result_type=result_type,
            pnl=pnl,
            pnl_percent=Percentage(pnl_percent),
            breakout_timestamp_ms=setup.breakout_timestamp,
            retest_timestamp_ms=setup.retest_timestamp,
        )
        return trade, exit_idx

    @staticmethod
    def _body_ratio(row: object) -> float:
        spread = max(float(row.high) - float(row.low), 1e-12)
        return abs(float(row.close) - float(row.open)) / spread

    @staticmethod
    def _append_volatility(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.assign(natr=np.nan)

        work = frame.copy().sort_values("timestamp").reset_index(drop=True)
        prev_close = work["close"].shift(1).fillna(work["close"])
        tr = pd.concat(
            [
                work["high"] - work["low"],
                (work["high"] - prev_close).abs(),
                (work["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=14, min_periods=14).mean()
        work["atr14"] = atr.fillna(0.0)
        work["natr"] = (work["atr14"] / work["close"].replace(0, np.nan)).fillna(0.0)
        return work

    def _resolve_pump_signal(
        self,
        *,
        rows: list[object],
        idx: int,
        params: BeeBiteParams,
    ) -> tuple[PositionSide, float, float, float] | None:
        if idx < 11:
            return None

        recent = rows[idx - 5 : idx + 1]
        before = rows[idx - 11 : idx - 5]
        atr_pre = float(np.mean([float(item.atr14) for item in before]))
        if atr_pre <= 0:
            return None

        high_pump = max(float(item.high) for item in recent)
        low_pump = min(float(item.low) for item in recent)
        low_before_pump = min(float(item.low) for item in before)
        high_before_pump = max(float(item.high) for item in before)

        up_impulse = high_pump - low_before_pump
        down_impulse = high_before_pump - low_pump
        impulse_threshold = params.bite_min_move_atr * atr_pre
        if up_impulse < impulse_threshold and down_impulse < impulse_threshold:
            return None

        if up_impulse >= down_impulse:
            return PositionSide.LONG, high_pump, up_impulse, atr_pre
        return PositionSide.SHORT, low_pump, down_impulse, atr_pre

    def _freeze_range(
        self,
        *,
        rows: list[object],
        end_idx: int,
        setup: SetupContext,
        params: BeeBiteParams,
        window_len: int,
    ) -> tuple[float, float, float, float] | None:
        start_idx = setup.breakout_idx + 1
        if end_idx - start_idx + 1 < window_len:
            return None

        window = rows[start_idx : start_idx + window_len]
        lows = np.array([float(item.low) for item in window])
        highs = np.array([float(item.high) for item in window])
        atr_bg = float(np.mean([float(item.atr14) for item in window]))
        if atr_bg <= 0:
            return None

        p10 = float(np.quantile(lows, 0.10))
        p85 = float(np.quantile(highs, 0.85))
        p90 = float(np.quantile(highs, 0.90))
        core_width = max(p85 - p10, 0.0)

        width_limit = min(0.45 * setup.pump_height, 7.0 * atr_bg)
        if core_width > width_limit:
            return None

        p10_windows: list[float] = []
        for w_end in range(5, len(window)):
            sub_lows = lows[w_end - 5 : w_end + 1]
            p10_windows.append(float(np.quantile(sub_lows, 0.10)))
        if len(p10_windows) < 6:
            return None

        p10_last6 = p10_windows[-6:]
        if max(p10_last6) - min(p10_last6) > params.bite_max_retest_depth * atr_bg:
            return None

        delta = 0.2 * atr_bg
        support = p10 - delta
        resistance = p90 + delta
        return support, resistance, core_width, atr_bg


    @staticmethod
    def _resolve_retest_zone_ratio(*, params: BeeBiteParams, natr: float) -> float:
        if params.bite_retest_zone_atr is None:
            return max(params.bite_retest_zone, 0.0)
        return max(params.bite_retest_zone_atr * max(natr, 0.0), 0.0)
    @staticmethod
    def _hours_to_candles(hours: int, timeframe) -> int:
        timeframe_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
            "1w": 10080,
        }
        key = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        minutes = timeframe_minutes.get(key, 15)
        return max(1, int((hours * 60) / minutes))
