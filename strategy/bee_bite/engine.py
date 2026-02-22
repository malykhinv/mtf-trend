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
from strategy.bee_bite.config import BeeBiteParams, get_bee_bite_runtime, get_bee_bite_score_threshold
from strategy.bee_bite.trade_plan import BeeBiteTradePlan, build_bee_bite_trade_plan, resolve_profile_tp1_share
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
    break_idx: int | None = None
    reclaim_idx: int | None = None
    lowest_break: float | None = None
    retest_touch_idx: int | None = None
    retest_deadline_idx: int | None = None
    t_pump_start: int | None = None
    t_pump_end: int | None = None
    high_pump: float | None = None
    low_before_pump: float | None = None


class BeeBiteEngine:
    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
    CONSERVATIVE_RETEST_LIMIT = 6
    CONSERVATIVE_RETEST_TOUCH_OFFSET_ATR_BG = 0.05
    CONSERVATIVE_RETEST_CONFIRM_OFFSET_ATR_BG = 0.10
    IMPULSE_THRESHOLDS: dict[str, float] = {
        "A": 2.5,
        "B": 2.2,
        "C": 2.0,
    }
    PROFILE_RANGE_WINDOW: dict[str, int] = {
        "A": 48,
        "B": 40,
        "C": 32,
    }
    FIXED_RANGE_WINDOW: int | None = None
    PROFILE_STABILITY_THRESHOLD: dict[str, float] = {
        "A": 0.20,
        "B": 0.25,
        "C": 0.30,
    }
    PROFILE_TIME_EXIT_HOURS_NO_TP1: dict[str, int] = {
        "A": 12,
        "B": 8,
        "C": 6,
    }
    PROFILE_RECLAIM_TIMEOUT: dict[str, int] = {
        "A": 5,
        "B": 6,
        "C": 8,
    }

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
        if not 0.0 < params.bite_tp1_share < 1.0:
            raise ValueError("bite_tp1_share должен быть в интервале (0, 1)")
        if params.bite_r_trade <= 0:
            raise ValueError("bite_r_trade должен быть > 0")
        if params.bite_portfolio_risk_limit <= 0:
            raise ValueError("bite_portfolio_risk_limit должен быть > 0")

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
        cooldown_until_idx = -1

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
                if i <= cooldown_until_idx:
                    i += 1
                    continue
                pump_signal = self._resolve_pump_signal(rows=rows, idx=i, params=params)
                if pump_signal is not None:
                    (
                        side,
                        level_price,
                        pump_height,
                        atr_pre,
                        atr_bg,
                        t_pump_start,
                        t_pump_end,
                        high_pump,
                        low_before_pump,
                    ) = pump_signal
                    setup = SetupContext(
                        side=side,
                        level_price=level_price,
                        breakout_idx=i,
                        breakout_timestamp=timestamp,
                        pump_height=pump_height,
                        atr_pre=atr_pre,
                        atr_bg=atr_bg,
                        t_pump_start=t_pump_start,
                        t_pump_end=t_pump_end,
                        high_pump=high_pump,
                        low_before_pump=low_before_pump,
                    )
                    state = BeeBiteState.SEEK_RANGE
                    diagnostics["states"].append(state.value)

            if state == BeeBiteState.SEEK_RANGE and setup is not None:
                window_len = self._resolve_range_window_len(params.bite_profile_id)
                max_wait = max(1, self._hours_to_candles(params.bite_retest_window_hours, params.entry_timeframe))
                elapsed = i - setup.breakout_idx
                if elapsed > max_wait:
                    setup = None
                    state = BeeBiteState.IDLE
                    diagnostics["states"].append(state.value)
                elif elapsed >= window_len:
                    range_setup = self._freeze_range(rows=rows, end_idx=i, setup=setup, params=params, window_len=window_len)
                    if range_setup is not None:
                        setup.range_low, setup.range_high, setup.core_width = range_setup
                        setup.retest_idx = i
                        setup.retest_timestamp = timestamp
                        setup.break_idx = None
                        setup.reclaim_idx = None
                        setup.lowest_break = None
                        setup.retest_touch_idx = None
                        state = BeeBiteState.RANGE_LOCKED
                        diagnostics["states"].append(state.value)

            if state == BeeBiteState.RANGE_LOCKED and setup is not None:
                range_started_idx = setup.retest_idx if setup.retest_idx is not None else i
                elapsed_in_range = i - range_started_idx
                max_age_range_candles = self._hours_to_candles(params.bite_max_age_range, params.entry_timeframe)
                if elapsed_in_range > max_age_range_candles:
                    setup = None
                    state = BeeBiteState.IDLE
                    diagnostics["states"].append(state.value)
                    i += 1
                    continue
                boundary = setup.range_low if setup.side == PositionSide.LONG else setup.range_high
                puncture_price = float(row.low) if setup.side == PositionSide.LONG else float(row.high)
                puncture_detected = puncture_price < boundary if setup.side == PositionSide.LONG else puncture_price > boundary
                if puncture_detected and setup.atr_bg > 0 and setup.core_width > 0:
                    depth = abs(puncture_price - boundary)
                    min_depth = params.bite_min_depth_threshold * setup.atr_bg
                    max_depth = 0.5 * setup.core_width
                    if min_depth <= depth <= max_depth:
                        setup.break_idx = i
                        setup.lowest_break = puncture_price
                        setup.reclaim_idx = None
                        setup.retest_touch_idx = None
                        state = BeeBiteState.BREAK_ACTIVE
                        diagnostics["states"].append(state.value)

            if state == BeeBiteState.BREAK_ACTIVE and setup is not None and setup.break_idx is not None:
                boundary = setup.range_low if setup.side == PositionSide.LONG else setup.range_high
                break_price = float(row.low) if setup.side == PositionSide.LONG else float(row.high)
                if setup.lowest_break is None:
                    setup.lowest_break = break_price
                elif setup.side == PositionSide.LONG:
                    setup.lowest_break = min(setup.lowest_break, break_price)
                else:
                    setup.lowest_break = max(setup.lowest_break, break_price)
                reclaim_limit = self.PROFILE_RECLAIM_TIMEOUT.get(params.bite_profile_id, params.bite_reclaim_limit)
                elapsed_since_break = i - setup.break_idx
                emergency_level = 0.7 * setup.core_width
                emergency_break = (
                    float(row.low) < (boundary - emergency_level)
                    if setup.side == PositionSide.LONG
                    else float(row.high) > (boundary + emergency_level)
                )
                if emergency_break or elapsed_since_break > reclaim_limit:
                    setup = None
                    state = BeeBiteState.IDLE
                    diagnostics["states"].append(state.value)
                else:
                    reclaim_offset_threshold = params.bite_micro_offset * setup.atr_bg
                    reclaim_ok = price_close > boundary if setup.side == PositionSide.LONG else price_close < boundary
                    micro_ok = (
                        price_close > (boundary + reclaim_offset_threshold)
                        if setup.side == PositionSide.LONG
                        else price_close < (boundary - reclaim_offset_threshold)
                    )
                    if reclaim_ok and not micro_ok:
                        cooldown_bars = self._hours_to_candles(get_bee_bite_runtime(params.bite_profile_id).cooldown_hours, params.entry_timeframe)
                        cooldown_until_idx = i + cooldown_bars
                        setup = None
                        state = BeeBiteState.IDLE
                        diagnostics["states"].append(state.value)
                        i += 1
                        continue
                    if setup.reclaim_idx is None and reclaim_ok and micro_ok:
                        setup.reclaim_idx = i
                        if params.bite_profile_id == "A":
                            setup.retest_deadline_idx = i + self.CONSERVATIVE_RETEST_LIMIT
                            setup.retest_touch_idx = None

                    if setup.reclaim_idx is not None:
                        if params.bite_profile_id == "A":
                            retest_touch_offset = self.CONSERVATIVE_RETEST_TOUCH_OFFSET_ATR_BG * setup.atr_bg
                            retest_confirm_offset = self.CONSERVATIVE_RETEST_CONFIRM_OFFSET_ATR_BG * setup.atr_bg
                            retest_deadline = setup.retest_deadline_idx
                            if retest_deadline is None:
                                retest_deadline = setup.reclaim_idx + self.CONSERVATIVE_RETEST_LIMIT
                                setup.retest_deadline_idx = retest_deadline
                            touch_zone = (
                                float(row.low) <= (boundary + retest_touch_offset)
                                if setup.side == PositionSide.LONG
                                else float(row.high) >= (boundary - retest_touch_offset)
                            )
                            if setup.retest_touch_idx is None and touch_zone:
                                setup.retest_touch_idx = i
                            confirm_close = (
                                price_close > (boundary + retest_confirm_offset)
                                if setup.side == PositionSide.LONG
                                else price_close < (boundary - retest_confirm_offset)
                            )
                            if setup.retest_touch_idx is not None and i > setup.retest_touch_idx and confirm_close:
                                state = BeeBiteState.ENTRY_SIGNAL
                                diagnostics["states"].append(state.value)
                            elif i > retest_deadline:
                                setup = None
                                state = BeeBiteState.IDLE
                                diagnostics["states"].append(state.value)
                        elif params.bite_entry_trigger == EntryTrigger.IMMEDIATE:
                            state = BeeBiteState.ENTRY_SIGNAL
                            diagnostics["states"].append(state.value)
                        else:
                            confirm_idx = setup.reclaim_idx + params.bite_confirmation_bars
                            if i >= confirm_idx:
                                confirm_close = (
                                    price_close > (boundary + reclaim_offset_threshold)
                                    if setup.side == PositionSide.LONG
                                    else price_close < (boundary - reclaim_offset_threshold)
                                )
                                if confirm_close:
                                    state = BeeBiteState.ENTRY_SIGNAL
                                    diagnostics["states"].append(state.value)

            if state == BeeBiteState.ENTRY_SIGNAL and setup is not None and setup.retest_idx is not None:
                trade, exit_idx, rejected = self._simulate_trade(rows=rows, entry_idx=i, setup=setup, params=params)
                if trade is not None:
                    trades.append(trade)
                    diagnostics["trades_generated"] = int(diagnostics["trades_generated"]) + 1
                elif rejected:
                    cooldown_bars = self._hours_to_candles(get_bee_bite_runtime(params.bite_profile_id).cooldown_hours, params.entry_timeframe)
                    cooldown_until_idx = i + cooldown_bars
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
    ) -> tuple[TradeResult | None, int, bool]:
        entry_row = rows[entry_idx]
        entry_price = float(entry_row.close)
        if setup.range_low is None or setup.range_high is None:
            return None, entry_idx, False

        buffer = max(0.1 * setup.atr_bg, 0.001 * entry_price)
        if setup.side == PositionSide.LONG:
            if setup.lowest_break is None:
                return None, entry_idx, False
            stop = float(setup.lowest_break) - buffer
        else:
            if setup.lowest_break is None:
                return None, entry_idx, False
            stop = float(setup.lowest_break) + buffer
        trade_plan = build_bee_bite_trade_plan(
            side=setup.side,
            entry_price=entry_price,
            atr_bg=setup.atr_bg,
            stop_loss=stop,
            support=float(setup.range_low),
            resistance=float(setup.range_high),
            high_pump=setup.high_pump,
            low_before_pump=setup.low_before_pump,
        )
        if trade_plan is None:
            return None, entry_idx, False
        if trade_plan.stop_distance < (0.3 * setup.atr_bg):
            return None, entry_idx, True

        score_threshold = get_bee_bite_score_threshold(params.bite_profile_id).min_score
        trade_score = self._score_trade_plan(setup=setup, plan=trade_plan, entry_price=entry_price)
        if trade_score < score_threshold:
            return None, entry_idx, True
        risk = max(trade_plan.stop_distance, entry_price * 0.0001)
        stop = trade_plan.stop_loss
        tp1 = trade_plan.tp1

        trade_risk = params.bite_r_trade
        if trade_risk > params.bite_portfolio_risk_limit:
            return None, entry_idx, False

        position_size = params.bite_r_trade / risk
        tp1_share = resolve_profile_tp1_share(params.bite_profile_id)
        remainder_share = 1.0 - tp1_share

        trailing_mode = trade_plan.trailing_mode
        tp2_fixed = None if trailing_mode else trade_plan.tp2
        trailing_reference = entry_price
        tp1_hit = False
        stop_after_tp1 = trade_plan.be_stop

        time_exit_hours = self.PROFILE_TIME_EXIT_HOURS_NO_TP1.get(params.bite_profile_id, 8)
        time_exit_candles = max(1, self._hours_to_candles(time_exit_hours, params.entry_timeframe))

        limit = len(rows) - 1
        if params.bite_t_max_in_trade is not None:
            limit = min(limit, entry_idx + max(1, params.bite_t_max_in_trade))

        result_type = TradeResultType.BE
        exit_price = entry_price
        exit_idx = limit
        realized_pnl = 0.0
        for idx in range(entry_idx + 1, limit + 1):
            row = rows[idx]
            low = float(row.low)
            high = float(row.high)
            close = float(row.close)

            if not tp1_hit and (idx - entry_idx) >= time_exit_candles:
                exit_price = close
                result_type = TradeResultType.BE
                exit_idx = idx
                break

            if setup.side == PositionSide.LONG:
                active_stop = stop_after_tp1 if tp1_hit else stop
                if low <= active_stop:
                    exit_price = active_stop
                    if tp1_hit:
                        realized_pnl += (active_stop - entry_price) * remainder_share * position_size
                        result_type = TradeResultType.TP1_BE
                    else:
                        realized_pnl += (active_stop - entry_price) * position_size
                    result_type = TradeResultType.SL
                    if tp1_hit:
                        result_type = TradeResultType.TP1_BE
                    exit_idx = idx
                    break
                if not tp1_hit and high >= tp1:
                    tp1_hit = True
                    realized_pnl += (tp1 - entry_price) * tp1_share * position_size
                    trailing_reference = max(trailing_reference, close)
                if tp1_hit:
                    trailing_reference = max(trailing_reference, close)
                    trailing_stop = trailing_reference - setup.atr_bg
                    tp2_target = tp2_fixed if not trailing_mode else trailing_stop
                    if high >= tp2_target:
                        exit_price = tp2_target
                        realized_pnl += (tp2_target - entry_price) * remainder_share * position_size
                        result_type = TradeResultType.TP2
                        exit_idx = idx
                        break
                    if low <= stop_after_tp1:
                        exit_price = stop_after_tp1
                        realized_pnl += (stop_after_tp1 - entry_price) * remainder_share * position_size
                        result_type = TradeResultType.TP1_BE
                        exit_idx = idx
                        break
            else:
                active_stop = stop_after_tp1 if tp1_hit else stop
                if high >= active_stop:
                    exit_price = active_stop
                    if tp1_hit:
                        realized_pnl += (entry_price - active_stop) * remainder_share * position_size
                        result_type = TradeResultType.TP1_BE
                    else:
                        realized_pnl += (entry_price - active_stop) * position_size
                        result_type = TradeResultType.SL
                    exit_idx = idx
                    break
                if not tp1_hit and low <= tp1:
                    tp1_hit = True
                    realized_pnl += (entry_price - tp1) * tp1_share * position_size
                    trailing_reference = min(trailing_reference, close)
                if tp1_hit:
                    trailing_reference = min(trailing_reference, close)
                    trailing_stop = trailing_reference + setup.atr_bg
                    tp2_target = tp2_fixed if not trailing_mode else trailing_stop
                    if low <= tp2_target:
                        exit_price = tp2_target
                        realized_pnl += (entry_price - tp2_target) * remainder_share * position_size
                        result_type = TradeResultType.TP2
                        exit_idx = idx
                        break
                    if high >= stop_after_tp1:
                        exit_price = stop_after_tp1
                        realized_pnl += (entry_price - stop_after_tp1) * remainder_share * position_size
                        result_type = TradeResultType.TP1_BE
                        exit_idx = idx
                        break

            exit_price = close

        if exit_idx == limit:
            if tp1_hit:
                if setup.side == PositionSide.LONG:
                    realized_pnl += (exit_price - entry_price) * remainder_share * position_size
                    result_type = TradeResultType.TP1_BE
                else:
                    realized_pnl += (entry_price - exit_price) * remainder_share * position_size
                    result_type = TradeResultType.TP1_BE
            else:
                if setup.side == PositionSide.LONG:
                    realized_pnl = (exit_price - entry_price) * position_size
                else:
                    realized_pnl = (entry_price - exit_price) * position_size

        if exit_idx != limit and not tp1_hit and result_type != TradeResultType.SL:
            if setup.side == PositionSide.LONG:
                realized_pnl = (exit_price - entry_price) * position_size
            else:
                realized_pnl = (entry_price - exit_price) * position_size

        pnl = realized_pnl
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
        return trade, exit_idx, False

    @staticmethod
    def _score_trade_plan(*, setup: SetupContext, plan: BeeBiteTradePlan, entry_price: float) -> float:
        tp1 = float(plan.tp1)
        stop_distance = max(float(plan.stop_distance), 1e-12)
        if setup.side == PositionSide.LONG:
            reward = max(tp1 - entry_price, 0.0)
        else:
            reward = max(entry_price - tp1, 0.0)
        rr = reward / stop_distance
        return max(rr * 2.0, 0.0)

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
    ) -> tuple[PositionSide, float, float, float, float, int, int, float, float] | None:
        pump_window = 6
        pre_pump_len = 14
        atr_bg_window_len = 96
        before_pump_idx = idx - pump_window
        atr_start_idx = before_pump_idx - pre_pump_len
        pump_start_idx = idx - pump_window + 1
        atr_bg_start_idx = pump_start_idx - atr_bg_window_len
        if atr_start_idx < 0:
            return None
        if atr_bg_start_idx < 0:
            return None

        recent = rows[idx - pump_window + 1 : idx + 1]
        before_pump = rows[before_pump_idx]
        atr_window = rows[atr_start_idx:before_pump_idx]

        tr_values: list[float] = []
        for n, item in enumerate(atr_window):
            prev_close = float(rows[atr_start_idx + n - 1].close)
            high = float(item.high)
            low = float(item.low)
            tr_values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr_pre = float(np.mean(tr_values)) if tr_values else 0.0
        if atr_pre <= 0:
            return None

        atr_bg_values = [float(item.atr14) for item in rows[atr_bg_start_idx:pump_start_idx]]
        atr_bg = float(np.median(atr_bg_values)) if atr_bg_values else 0.0
        if atr_bg <= 0:
            return None

        high_pump = max(float(item.high) for item in recent)
        low_pump = min(float(item.low) for item in recent)
        low_before_pump = float(before_pump.low)
        high_before_pump = float(before_pump.high)

        up_impulse = high_pump - low_before_pump
        down_impulse = high_before_pump - low_pump
        impulse_multiplier = self.IMPULSE_THRESHOLDS.get(params.bite_profile_id, self.IMPULSE_THRESHOLDS["B"])
        impulse_threshold = impulse_multiplier * atr_pre
        if up_impulse < impulse_threshold and down_impulse < impulse_threshold:
            return None

        t_pump_start = int(recent[0].timestamp)
        t_pump_end = int(recent[-1].timestamp)
        if up_impulse >= down_impulse:
            return (
                PositionSide.LONG,
                high_pump,
                up_impulse,
                atr_pre,
                atr_bg,
                t_pump_start,
                t_pump_end,
                high_pump,
                low_before_pump,
            )
        return (
            PositionSide.SHORT,
            low_pump,
            down_impulse,
            atr_pre,
            atr_bg,
            t_pump_start,
            t_pump_end,
            high_pump,
            low_before_pump,
        )

    def _freeze_range(
        self,
        *,
        rows: list[object],
        end_idx: int,
        setup: SetupContext,
        params: BeeBiteParams,
        window_len: int,
    ) -> tuple[float, float, float] | None:
        min_start_idx = setup.breakout_idx + 1
        start_idx = end_idx - window_len + 1
        if start_idx < min_start_idx:
            return None

        window = rows[start_idx : end_idx + 1]
        lows = np.array([float(item.low) for item in window])
        highs = np.array([float(item.high) for item in window])
        atr_bg = float(setup.atr_bg)
        if atr_bg <= 0:
            return None
        p10 = float(np.quantile(lows, 0.10))
        p85 = float(np.quantile(highs, 0.85))
        p90 = float(np.quantile(highs, 0.90))
        core_width = max(p90 - p10, 0.0)

        width_limit = min(0.45 * setup.pump_height, 7.0 * atr_bg)
        if core_width > width_limit:
            return None

        if len(window) < 6:
            return None

        p10_last6: list[float] = []
        for idx in range(end_idx - 5, end_idx + 1):
            rolling_start_idx = idx - window_len + 1
            if rolling_start_idx < min_start_idx:
                return None
            rolling_window = rows[rolling_start_idx : idx + 1]
            rolling_lows = np.array([float(item.low) for item in rolling_window])
            p10_last6.append(float(np.quantile(rolling_lows, 0.10)))

        stability_threshold = self.PROFILE_STABILITY_THRESHOLD.get(params.bite_profile_id, params.bite_max_retest_depth)
        if max(p10_last6) - min(p10_last6) > stability_threshold * atr_bg:
            return None

        support_band_high = p10 + (0.2 * atr_bg)
        support_cluster = lows[(lows >= p10) & (lows <= support_band_high)]
        support = float(np.median(support_cluster)) if support_cluster.size >= 2 else p10
        resistance = p85
        return support, resistance, core_width

    def _resolve_range_window_len(self, profile_id: str) -> int:
        if self.FIXED_RANGE_WINDOW is not None:
            return max(6, int(self.FIXED_RANGE_WINDOW))
        return max(6, int(self.PROFILE_RANGE_WINDOW.get(profile_id, 40)))


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
