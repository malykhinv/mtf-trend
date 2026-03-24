"""Signal engine for post-pump absorption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
import pandas as pd

from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
from strategy.post_pump_absorption.config import (
    PostPumpAbsorptionParams,
    PostPumpAbsorptionRuntime,
    build_post_pump_absorption_runtime,
)
from strategy.post_pump_absorption.trade_plan import (
    PostPumpAbsorptionTradePlan,
    build_post_pump_absorption_trade_plan,
)


@dataclass(slots=True)
class PriceRow:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr14: float
    taker_buy_ratio_resolved: float
    taker_buy_volume_resolved: float


@dataclass(slots=True)
class PumpContext:
    pump_idx: int
    pump_high: float
    pump_height: float
    atr_bg: float


@dataclass(slots=True)
class RangeContext:
    range_low: float
    range_high: float
    range_width: float
    lower_zone_high: float
    range_age_bars: int
    range_width_atr: float
    range_width_pump_fraction: float


@dataclass(slots=True)
class AggressionContext:
    ratio_now: float
    baseline_ratio: float
    volume_now: float
    baseline_volume: float
    volume_mult: float


@dataclass(slots=True)
class SwingPoint:
    idx: int
    price: float


@dataclass(slots=True)
class EntrySetup:
    setup_type: str
    stop_anchor: float
    trigger_price: float
    entry_range_fraction: float
    aggression: AggressionContext
    range_ctx: RangeContext


class GenerationDiagnostics(TypedDict, total=False):
    pumps_found: int
    range_candidates: int
    range_invalidated: int
    lower_zone_hits: int
    aggression_hits: int
    lsb_hits: int
    mbb_hits: int
    trades_generated: int
    reentries_generated: int
    missing_taker_data: int
    trade_details: list[dict[str, object]]
    context: dict[str, object]


class PostPumpAbsorptionEngine:
    REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
    FLOW_COLUMNS = ("taker_buy_volume", "taker_buy_ratio", "taker_ratio")
    COUNT_KEYS = (
        "pumps_found",
        "range_candidates",
        "range_invalidated",
        "lower_zone_hits",
        "aggression_hits",
        "lsb_hits",
        "mbb_hits",
        "trades_generated",
        "reentries_generated",
        "missing_taker_data",
    )

    def __init__(self) -> None:
        self._last_generation_diagnostics = self._empty_diagnostics()

    def consume_last_generation_diagnostics(self) -> GenerationDiagnostics:
        diagnostics = dict(self._last_generation_diagnostics)
        trade_details = diagnostics.get("trade_details")
        if isinstance(trade_details, list):
            diagnostics["trade_details"] = [dict(item) for item in trade_details]
        context = diagnostics.get("context")
        if isinstance(context, dict):
            diagnostics["context"] = dict(context)
        self._last_generation_diagnostics = self._empty_diagnostics()
        return diagnostics

    @staticmethod
    def _empty_diagnostics(*, symbol: str | None = None) -> GenerationDiagnostics:
        diagnostics: GenerationDiagnostics = {
            "trade_details": [],
            "context": {},
        }
        for key in PostPumpAbsorptionEngine.COUNT_KEYS:
            diagnostics[key] = 0
        if symbol:
            diagnostics["context"] = {"symbol": symbol}
        return diagnostics

    @staticmethod
    def validate_config(params: PostPumpAbsorptionParams) -> None:
        if params.ppa_r_trade is not None and params.ppa_r_trade <= 0.0:
            raise ValueError("ppa_r_trade must be > 0 when provided")

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        missing = [column for column in self.REQUIRED_COLUMNS if column not in data.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        prepared = data.copy()
        prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp"])
        prepared["timestamp"] = prepared["timestamp"].astype("int64")
        for column in self.REQUIRED_COLUMNS[1:]:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

        for optional_column in self.FLOW_COLUMNS:
            if optional_column in prepared.columns:
                prepared[optional_column] = pd.to_numeric(prepared[optional_column], errors="coerce")

        prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        return prepared.reset_index(drop=True)

    def generate_events(self, data: pd.DataFrame, params: PostPumpAbsorptionParams) -> list[TradeResult]:
        prepared = self.prepare_data(data)
        return self._run(prepared=prepared, params=params)

    def generate_events_multi_tf(
        self,
        *,
        entry_frame: pd.DataFrame,
        params: PostPumpAbsorptionParams,
    ) -> list[TradeResult]:
        prepared = self.prepare_data(entry_frame)
        return self._run(prepared=prepared, params=params)

    def _run(self, *, prepared: pd.DataFrame, params: PostPumpAbsorptionParams) -> list[TradeResult]:
        runtime = build_post_pump_absorption_runtime(params)
        diagnostics = self._empty_diagnostics(symbol=params.symbol)
        enriched = self._append_features(prepared, atr_window_bars=runtime.atr_window_bars)
        if not self._has_usable_flow_data(enriched):
            diagnostics["missing_taker_data"] = 1
            self._last_generation_diagnostics = diagnostics
            return []

        rows = self._build_price_rows(enriched)
        if not rows:
            self._last_generation_diagnostics = diagnostics
            return []

        trades: list[TradeResult] = []
        i = max(
            runtime.pump_window_bars + runtime.pump_baseline_window_bars,
            runtime.flow_baseline_window_bars,
            runtime.structure_break_lookback_bars + runtime.range_min_bars,
            runtime.micro_base_bars + runtime.range_min_bars,
            20,
        )
        max_entry_offset = max(runtime.range_min_bars, 2)
        while i < len(rows) - max_entry_offset:
            pump_ctx = self._detect_pump(rows=rows, idx=i, params=params, runtime=runtime)
            if pump_ctx is None:
                i += 1
                continue

            pump_ctx = self._refine_pump_context(rows=rows, pump_ctx=pump_ctx, runtime=runtime)

            diagnostics["pumps_found"] = int(diagnostics.get("pumps_found", 0)) + 1
            regime_trades, regime_diagnostics, next_i = self._scan_pump_regime(
                rows=rows,
                pump_ctx=pump_ctx,
                params=params,
                runtime=runtime,
            )
            trades.extend(regime_trades)
            self._merge_diagnostics(target=diagnostics, source=regime_diagnostics)
            i = max(i + 1, next_i)

        self._last_generation_diagnostics = diagnostics
        return trades

    @classmethod
    def _merge_diagnostics(cls, *, target: GenerationDiagnostics, source: GenerationDiagnostics) -> None:
        for key in cls.COUNT_KEYS:
            target[key] = int(target.get(key, 0)) + int(source.get(key, 0))

        target_trade_details = target.setdefault("trade_details", [])
        source_trade_details = source.get("trade_details", [])
        if isinstance(target_trade_details, list) and isinstance(source_trade_details, list):
            target_trade_details.extend(source_trade_details)

    @staticmethod
    def _append_features(frame: pd.DataFrame, *, atr_window_bars: int) -> pd.DataFrame:
        if frame.empty:
            return frame.assign(atr14=np.nan)

        work = frame.copy()
        prev_close = work["close"].shift(1).fillna(work["close"])
        tr = pd.concat(
            [
                work["high"] - work["low"],
                (work["high"] - prev_close).abs(),
                (work["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        window = max(5, int(atr_window_bars))
        work["atr14"] = tr.rolling(window=window, min_periods=window).mean().fillna(0.0)

        if "taker_buy_ratio" in work.columns:
            ratio = work["taker_buy_ratio"]
        elif "taker_ratio" in work.columns:
            ratio = work["taker_ratio"]
        elif "taker_buy_volume" in work.columns:
            volume = work["volume"].replace(0, np.nan)
            ratio = work["taker_buy_volume"] / volume
        else:
            ratio = pd.Series(np.nan, index=work.index)
        work["taker_buy_ratio_resolved"] = (
            pd.to_numeric(ratio, errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
        )

        if "taker_buy_volume" in work.columns:
            taker_buy_volume = work["taker_buy_volume"]
        else:
            taker_buy_volume = work["volume"] * work["taker_buy_ratio_resolved"]
        work["taker_buy_volume_resolved"] = (
            pd.to_numeric(taker_buy_volume, errors="coerce").fillna(0.0).clip(lower=0.0)
        )
        return work

    @classmethod
    def _has_usable_flow_data(cls, frame: pd.DataFrame) -> bool:
        if not any(column in frame.columns for column in cls.FLOW_COLUMNS):
            return False
        if "taker_buy_ratio_resolved" not in frame.columns or "taker_buy_volume_resolved" not in frame.columns:
            return False

        ratio = pd.to_numeric(frame["taker_buy_ratio_resolved"], errors="coerce").fillna(0.0)
        volume = pd.to_numeric(frame["taker_buy_volume_resolved"], errors="coerce").fillna(0.0)
        usable = ratio.gt(0.0) & volume.gt(0.0)
        return bool(usable.any())

    @staticmethod
    def _build_price_rows(frame: pd.DataFrame) -> list[PriceRow]:
        return [
            PriceRow(
                timestamp=int(row.timestamp),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                atr14=float(row.atr14),
                taker_buy_ratio_resolved=float(row.taker_buy_ratio_resolved),
                taker_buy_volume_resolved=float(row.taker_buy_volume_resolved),
            )
            for row in frame.itertuples(index=False)
        ]

    def _scan_pump_regime(
        self,
        *,
        rows: list[PriceRow],
        pump_ctx: PumpContext,
        params: PostPumpAbsorptionParams,
        runtime: PostPumpAbsorptionRuntime,
    ) -> tuple[list[TradeResult], GenerationDiagnostics, int]:
        diagnostics = self._empty_diagnostics()
        trades: list[TradeResult] = []
        latest_exit_idx = pump_ctx.pump_idx

        range_start_idx = pump_ctx.pump_idx + 1
        max_scan_idx = min(pump_ctx.pump_idx + runtime.range_max_bars, len(rows) - 2)
        first_entry_idx = range_start_idx + runtime.range_min_bars
        if first_entry_idx > max_scan_idx:
            return trades, diagnostics, max_scan_idx + 1

        locked_window = rows[range_start_idx:first_entry_idx]
        if len(locked_window) < runtime.range_min_bars:
            return trades, diagnostics, max_scan_idx + 1

        range_low = min(row.low for row in locked_window)
        range_high = max(row.high for row in locked_window)
        last_locked_idx = first_entry_idx - 1
        entry_idx = first_entry_idx

        while entry_idx <= max_scan_idx:
            range_ctx = self._build_locked_range_context(
                range_low=range_low,
                range_high=range_high,
                pump_ctx=pump_ctx,
                range_age_bars=(last_locked_idx - range_start_idx + 1),
                params=params,
            )
            if range_ctx is None:
                diagnostics["range_invalidated"] = int(diagnostics.get("range_invalidated", 0)) + 1
                break

            diagnostics["range_candidates"] = int(diagnostics.get("range_candidates", 0)) + 1
            current_row = rows[entry_idx]
            if current_row.close > range_ctx.lower_zone_high and current_row.low > range_ctx.lower_zone_high:
                range_low, range_high, last_locked_idx = self._extend_locked_range(
                    rows=rows,
                    range_low=range_low,
                    range_high=range_high,
                    last_locked_idx=last_locked_idx,
                    next_locked_idx=entry_idx,
                )
                entry_idx += 1
                continue

            diagnostics["lower_zone_hits"] = int(diagnostics.get("lower_zone_hits", 0)) + 1
            aggression = self._measure_aggression(rows=rows, idx=entry_idx, params=params, runtime=runtime)
            if aggression is None:
                range_low, range_high, last_locked_idx = self._extend_locked_range(
                    rows=rows,
                    range_low=range_low,
                    range_high=range_high,
                    last_locked_idx=last_locked_idx,
                    next_locked_idx=entry_idx,
                )
                entry_idx += 1
                continue

            diagnostics["aggression_hits"] = int(diagnostics.get("aggression_hits", 0)) + 1
            setup = self._detect_entry_setup(
                rows=rows,
                pump_ctx=pump_ctx,
                range_ctx=range_ctx,
                aggression=aggression,
                entry_idx=entry_idx,
                params=params,
                runtime=runtime,
            )
            if setup is None:
                range_low, range_high, last_locked_idx = self._extend_locked_range(
                    rows=rows,
                    range_low=range_low,
                    range_high=range_high,
                    last_locked_idx=last_locked_idx,
                    next_locked_idx=entry_idx,
                )
                entry_idx += 1
                continue

            setup_key = "lsb_hits" if setup.setup_type == "LSB" else "mbb_hits"
            diagnostics[setup_key] = int(diagnostics.get(setup_key, 0)) + 1
            trade, exit_idx = self._simulate_trade(
                rows=rows,
                entry_idx=entry_idx,
                setup=setup,
                pump_ctx=pump_ctx,
                params=params,
                runtime=runtime,
            )
            if trade is None:
                range_low, range_high, last_locked_idx = self._extend_locked_range(
                    rows=rows,
                    range_low=range_low,
                    range_high=range_high,
                    last_locked_idx=last_locked_idx,
                    next_locked_idx=entry_idx,
                )
                entry_idx += 1
                continue

            trades.append(trade)
            diagnostics["trades_generated"] = int(diagnostics.get("trades_generated", 0)) + 1
            if len(trades) > 1:
                diagnostics["reentries_generated"] = int(diagnostics.get("reentries_generated", 0)) + 1

            trade_details = diagnostics.setdefault("trade_details", [])
            if isinstance(trade_details, list):
                trade_details.append(self._build_trade_detail(trade))

            latest_exit_idx = max(latest_exit_idx, exit_idx)
            next_entry_idx = exit_idx + 1
            if next_entry_idx > max_scan_idx:
                return trades, diagnostics, max(max_scan_idx + 1, latest_exit_idx + 1)

            range_low, range_high, last_locked_idx = self._extend_locked_range(
                rows=rows,
                range_low=range_low,
                range_high=range_high,
                last_locked_idx=last_locked_idx,
                next_locked_idx=next_entry_idx - 1,
            )
            entry_idx = next_entry_idx

        return trades, diagnostics, max(max_scan_idx + 1, latest_exit_idx + 1)

    @staticmethod
    def _extend_locked_range(
        *,
        rows: list[PriceRow],
        range_low: float,
        range_high: float,
        last_locked_idx: int,
        next_locked_idx: int,
    ) -> tuple[float, float, int]:
        if next_locked_idx <= last_locked_idx:
            return range_low, range_high, last_locked_idx

        updated_low = range_low
        updated_high = range_high
        updated_idx = last_locked_idx
        for idx in range(last_locked_idx + 1, next_locked_idx + 1):
            row = rows[idx]
            updated_low = min(updated_low, row.low)
            updated_high = max(updated_high, row.high)
            updated_idx = idx
        return updated_low, updated_high, updated_idx

    def _detect_pump(
        self,
        *,
        rows: list[PriceRow],
        idx: int,
        params: PostPumpAbsorptionParams,
        runtime: PostPumpAbsorptionRuntime,
    ) -> PumpContext | None:
        pump_start_idx = idx - runtime.pump_window_bars + 1
        baseline_end_idx = pump_start_idx - 1
        baseline_start_idx = baseline_end_idx - runtime.pump_baseline_window_bars + 1
        if pump_start_idx < 1 or baseline_start_idx < 0:
            return None

        recent = rows[pump_start_idx: idx + 1]
        baseline = rows[baseline_start_idx: baseline_end_idx + 1]
        if not baseline or not recent:
            return None

        atr_values = [row.atr14 for row in baseline if row.atr14 > 0.0]
        if not atr_values:
            return None
        atr_bg = float(np.median(atr_values))
        if atr_bg <= 0.0:
            return None

        baseline_volumes = [row.volume for row in baseline if row.volume > 0.0]
        if not baseline_volumes:
            return None
        baseline_volume = float(np.median(baseline_volumes))
        pump_volume = float(np.mean([row.volume for row in recent]))
        if baseline_volume <= 0.0 or pump_volume < (params.pump_volume_mult * baseline_volume):
            return None

        pump_high = max(row.high for row in recent)
        pump_peak_relative_idx = max(range(len(recent)), key=lambda item_idx: recent[item_idx].high)
        if pump_peak_relative_idx < len(recent) - 2:
            return None
        low_before_pump = min(row.low for row in baseline[-min(6, len(baseline)):])
        pump_height = pump_high - low_before_pump
        if pump_height < (params.pump_min_move_atr * atr_bg):
            return None

        return PumpContext(
            pump_idx=idx,
            pump_high=pump_high,
            pump_height=pump_height,
            atr_bg=atr_bg,
        )

    @staticmethod
    def _refine_pump_context(
        *,
        rows: list[PriceRow],
        pump_ctx: PumpContext,
        runtime: PostPumpAbsorptionRuntime,
    ) -> PumpContext:
        peak_idx = pump_ctx.pump_idx
        peak_high = pump_ctx.pump_high
        pump_base_low = pump_ctx.pump_high - pump_ctx.pump_height
        scan_end_idx = min(len(rows) - 1, pump_ctx.pump_idx + runtime.pump_window_bars)

        for idx in range(pump_ctx.pump_idx + 1, scan_end_idx + 1):
            row = rows[idx]
            if row.high >= peak_high:
                peak_high = row.high
                peak_idx = idx

        if peak_idx == pump_ctx.pump_idx:
            return pump_ctx

        return PumpContext(
            pump_idx=peak_idx,
            pump_high=peak_high,
            pump_height=peak_high - pump_base_low,
            atr_bg=pump_ctx.atr_bg,
        )

    def _build_locked_range_context(
        self,
        *,
        range_low: float,
        range_high: float,
        pump_ctx: PumpContext,
        range_age_bars: int,
        params: PostPumpAbsorptionParams,
    ) -> RangeContext | None:
        range_width = range_high - range_low
        if range_width <= 0.0:
            return None

        range_width_atr = range_width / max(pump_ctx.atr_bg, 1e-12)
        if range_width_atr > params.max_range_width_atr:
            return None

        range_width_pump_fraction = range_width / max(pump_ctx.pump_height, 1e-12)
        if range_width_pump_fraction > params.max_range_width_pump_fraction:
            return None

        return RangeContext(
            range_low=range_low,
            range_high=range_high,
            range_width=range_width,
            lower_zone_high=range_low + (params.lower_zone_fraction * range_width),
            range_age_bars=range_age_bars,
            range_width_atr=range_width_atr,
            range_width_pump_fraction=range_width_pump_fraction,
        )

    def _measure_aggression(
        self,
        *,
        rows: list[PriceRow],
        idx: int,
        params: PostPumpAbsorptionParams,
        runtime: PostPumpAbsorptionRuntime,
    ) -> AggressionContext | None:
        row = rows[idx]
        baseline_start = max(0, idx - runtime.flow_baseline_window_bars)
        baseline = rows[baseline_start:idx]
        if not baseline:
            return None

        baseline_buy_volume = [
            item.taker_buy_volume_resolved for item in baseline if item.taker_buy_volume_resolved > 0.0
        ]
        if not baseline_buy_volume:
            return None

        baseline_ratio_values = [item.taker_buy_ratio_resolved for item in baseline if item.taker_buy_ratio_resolved > 0.0]
        if not baseline_ratio_values:
            return None

        baseline_volume = float(np.median(baseline_buy_volume))
        baseline_ratio = float(np.median(baseline_ratio_values))
        ratio_now = max(
            row.taker_buy_ratio_resolved,
            float(np.mean([item.taker_buy_ratio_resolved for item in rows[max(0, idx - 1): idx + 1]])),
        )
        volume_now = row.taker_buy_volume_resolved
        if baseline_volume <= 0.0:
            return None

        volume_mult = volume_now / baseline_volume
        if (
            ratio_now < params.taker_ratio_threshold
            or volume_mult < params.taker_volume_mult
            or ratio_now < baseline_ratio
        ):
            return None

        return AggressionContext(
            ratio_now=ratio_now,
            baseline_ratio=baseline_ratio,
            volume_now=volume_now,
            baseline_volume=baseline_volume,
            volume_mult=volume_mult,
        )

    def _detect_entry_setup(
        self,
        *,
        rows: list[PriceRow],
        pump_ctx: PumpContext,
        range_ctx: RangeContext,
        aggression: AggressionContext,
        entry_idx: int,
        params: PostPumpAbsorptionParams,
        runtime: PostPumpAbsorptionRuntime,
    ) -> EntrySetup | None:
        entry_row = rows[entry_idx]
        entry_fraction = (entry_row.close - range_ctx.range_low) / max(range_ctx.range_width, 1e-12)
        if entry_fraction > params.max_entry_range_fraction:
            return None

        lsb = self._detect_local_structure_break(
            rows=rows,
            range_ctx=range_ctx,
            aggression=aggression,
            entry_idx=entry_idx,
            params=params,
            atr_bg=pump_ctx.atr_bg,
            runtime=runtime,
            entry_fraction=entry_fraction,
        )
        if lsb is not None:
            return lsb

        return self._detect_micro_base_breakout(
            rows=rows,
            range_ctx=range_ctx,
            aggression=aggression,
            entry_idx=entry_idx,
            params=params,
            atr_bg=pump_ctx.atr_bg,
            runtime=runtime,
            entry_fraction=entry_fraction,
        )

    def _detect_local_structure_break(
        self,
        *,
        rows: list[PriceRow],
        range_ctx: RangeContext,
        aggression: AggressionContext,
        entry_idx: int,
        params: PostPumpAbsorptionParams,
        atr_bg: float,
        runtime: PostPumpAbsorptionRuntime,
        entry_fraction: float,
    ) -> EntrySetup | None:
        start_idx = max(1, entry_idx - runtime.structure_break_lookback_bars)
        if entry_idx - start_idx < runtime.structure_break_lookback_bars:
            return None

        swing_highs = self._find_swing_highs(rows=rows, start_idx=start_idx, end_idx=entry_idx)
        if len(swing_highs) < 2:
            return None

        previous_high = swing_highs[-2]
        last_lower_high = swing_highs[-1]
        if last_lower_high.price >= previous_high.price:
            return None

        trigger_price = last_lower_high.price + (params.entry_break_buffer_atr * atr_bg)
        if rows[entry_idx].close <= trigger_price:
            return None

        structure_low = self._resolve_structure_low(
            rows=rows,
            start_idx=last_lower_high.idx,
            end_idx=entry_idx,
        )
        if structure_low is None or structure_low.price > range_ctx.lower_zone_high:
            return None

        return EntrySetup(
            setup_type="LSB",
            stop_anchor=structure_low.price,
            trigger_price=trigger_price,
            entry_range_fraction=entry_fraction,
            aggression=aggression,
            range_ctx=range_ctx,
        )

    def _detect_micro_base_breakout(
        self,
        *,
        rows: list[PriceRow],
        range_ctx: RangeContext,
        aggression: AggressionContext,
        entry_idx: int,
        params: PostPumpAbsorptionParams,
        atr_bg: float,
        runtime: PostPumpAbsorptionRuntime,
        entry_fraction: float,
    ) -> EntrySetup | None:
        start_idx = entry_idx - runtime.micro_base_bars
        if start_idx < 0:
            return None
        base = rows[start_idx:entry_idx]
        if len(base) < runtime.micro_base_bars:
            return None

        base_high = max(item.high for item in base)
        base_low = min(item.low for item in base)
        base_width = base_high - base_low
        if base_width <= 0.0:
            return None
        if base_width > (params.micro_base_max_width_atr * atr_bg):
            return None
        if base_low > range_ctx.lower_zone_high:
            return None

        trigger_price = base_high + (params.entry_break_buffer_atr * atr_bg)
        if rows[entry_idx].close <= trigger_price:
            return None

        return EntrySetup(
            setup_type="MBB",
            stop_anchor=base_low,
            trigger_price=trigger_price,
            entry_range_fraction=entry_fraction,
            aggression=aggression,
            range_ctx=range_ctx,
        )

    @staticmethod
    def _find_swing_highs(
        *,
        rows: list[PriceRow],
        start_idx: int,
        end_idx: int,
    ) -> list[SwingPoint]:
        swing_highs: list[SwingPoint] = []
        for idx in range(max(1, start_idx), max(1, end_idx - 1)):
            prev_row = rows[idx - 1]
            row = rows[idx]
            next_row = rows[idx + 1]
            if row.high > prev_row.high and row.high >= next_row.high:
                swing_highs.append(SwingPoint(idx=idx, price=row.high))
        return swing_highs

    @staticmethod
    def _find_swing_lows(
        *,
        rows: list[PriceRow],
        start_idx: int,
        end_idx: int,
    ) -> list[SwingPoint]:
        swing_lows: list[SwingPoint] = []
        for idx in range(max(1, start_idx), max(1, end_idx - 1)):
            prev_row = rows[idx - 1]
            row = rows[idx]
            next_row = rows[idx + 1]
            if row.low < prev_row.low and row.low <= next_row.low:
                swing_lows.append(SwingPoint(idx=idx, price=row.low))
        return swing_lows

    def _resolve_structure_low(
        self,
        *,
        rows: list[PriceRow],
        start_idx: int,
        end_idx: int,
    ) -> SwingPoint | None:
        if end_idx <= start_idx:
            return None

        swing_lows = self._find_swing_lows(rows=rows, start_idx=start_idx, end_idx=end_idx)
        swing_lows = [point for point in swing_lows if point.idx > start_idx]
        if swing_lows:
            return swing_lows[-1]

        structure_slice = rows[start_idx:end_idx]
        if not structure_slice:
            return None

        local_idx, local_row = min(enumerate(structure_slice), key=lambda item: item[1].low)
        return SwingPoint(idx=start_idx + local_idx, price=local_row.low)

    def _simulate_trade(
        self,
        *,
        rows: list[PriceRow],
        entry_idx: int,
        setup: EntrySetup,
        pump_ctx: PumpContext,
        params: PostPumpAbsorptionParams,
        runtime: PostPumpAbsorptionRuntime,
    ) -> tuple[TradeResult | None, int]:
        entry_row = rows[entry_idx]
        entry_price = entry_row.close
        stop_loss = setup.stop_anchor - (params.stop_buffer_atr * pump_ctx.atr_bg)
        stop_distance = entry_price - stop_loss
        if stop_distance <= 0.0:
            return None, entry_idx

        min_stop = params.min_stop_atr * pump_ctx.atr_bg
        max_stop = min(
            params.max_stop_atr * pump_ctx.atr_bg,
            params.max_stop_range_fraction * setup.range_ctx.range_width,
        )
        if stop_distance < min_stop or stop_distance > max_stop:
            return None, entry_idx

        trade_plan = build_post_pump_absorption_trade_plan(
            entry_price=entry_price,
            stop_loss=stop_loss,
            range_low=setup.range_ctx.range_low,
            range_high=setup.range_ctx.range_high,
            be_buffer_pct=params.be_buffer_pct,
        )
        if trade_plan is None:
            return None, entry_idx

        trade_risk = (
            params.ppa_r_trade
            if params.ppa_r_trade is not None
            else params.ppa_deposit * params.ppa_risk_pct
        )
        if trade_risk <= 0.0:
            return None, entry_idx

        position_size = trade_risk / max(trade_plan.stop_distance, 1e-12)
        tp1_share = params.tp1_share
        remainder_share = 1.0 - tp1_share
        limit = min(len(rows) - 1, entry_idx + runtime.time_exit_bars)
        metadata = {
            "strategy_id": "post_pump_absorption",
            "symbol": params.symbol,
            "setup_type": setup.setup_type,
            "entry_range_fraction": round(setup.entry_range_fraction, 6),
            "trigger_price": round(setup.trigger_price, 8),
            "aggression_ratio": round(setup.aggression.ratio_now, 6),
            "aggression_baseline_ratio": round(setup.aggression.baseline_ratio, 6),
            "aggression_volume_mult": round(setup.aggression.volume_mult, 6),
            "range_low": round(setup.range_ctx.range_low, 8),
            "range_high": round(setup.range_ctx.range_high, 8),
            "range_width": round(setup.range_ctx.range_width, 8),
            "range_age_bars": setup.range_ctx.range_age_bars,
            "range_width_atr": round(setup.range_ctx.range_width_atr, 6),
            "range_width_pump_fraction": round(setup.range_ctx.range_width_pump_fraction, 6),
            "pump_height_atr": round(pump_ctx.pump_height / max(pump_ctx.atr_bg, 1e-12), 6),
            "stop_distance": round(stop_distance, 8),
            "stop_distance_atr": round(stop_distance / max(pump_ctx.atr_bg, 1e-12), 6),
            "stop_range_fraction": round(stop_distance / max(setup.range_ctx.range_width, 1e-12), 6),
        }
        return self._simulate_trade_path(
            rows=rows,
            entry_idx=entry_idx,
            limit=limit,
            position_size=position_size,
            trade_plan=trade_plan,
            tp1_share=tp1_share,
            remainder_share=remainder_share,
            metadata=metadata,
        )

    def _simulate_trade_path(
        self,
        *,
        rows: list[PriceRow],
        entry_idx: int,
        limit: int,
        position_size: float,
        trade_plan: PostPumpAbsorptionTradePlan,
        tp1_share: float,
        remainder_share: float,
        metadata: dict[str, int | float | str | bool | None],
    ) -> tuple[TradeResult | None, int]:
        entry_row = rows[entry_idx]
        entry_price = float(entry_row.close)
        stop_distance = max(trade_plan.stop_distance, 1e-12)
        tp1_hit = False
        range_mid_hit = False
        range_high_hit = False
        exit_idx = limit
        exit_price = entry_price
        realized_pnl = 0.0
        result_type = TradeResultType.BE
        max_favorable = 0.0
        max_adverse = 0.0

        for idx in range(entry_idx + 1, limit + 1):
            row = rows[idx]
            low = float(row.low)
            high = float(row.high)
            close = float(row.close)
            active_stop = trade_plan.be_stop if tp1_hit else trade_plan.stop_loss
            max_favorable = max(max_favorable, high - entry_price)
            max_adverse = max(max_adverse, entry_price - low)

            if high >= trade_plan.tp1:
                range_mid_hit = True
            if high >= trade_plan.tp2:
                range_high_hit = True

            if low <= active_stop:
                exit_price = active_stop
                if tp1_hit:
                    realized_pnl += (active_stop - entry_price) * remainder_share * position_size
                    result_type = TradeResultType.TP1_BE
                else:
                    realized_pnl += (active_stop - entry_price) * position_size
                    result_type = TradeResultType.SL
                exit_idx = idx
                break

            if not tp1_hit and high >= trade_plan.tp1:
                tp1_hit = True
                realized_pnl += (trade_plan.tp1 - entry_price) * tp1_share * position_size

            if tp1_hit and high >= trade_plan.tp2:
                exit_price = trade_plan.tp2
                realized_pnl += (trade_plan.tp2 - entry_price) * remainder_share * position_size
                result_type = TradeResultType.TP2
                exit_idx = idx
                break

            if idx == limit:
                exit_idx = idx
                exit_price = close
                if tp1_hit:
                    realized_pnl += (exit_price - entry_price) * remainder_share * position_size
                    result_type = TradeResultType.TP1_BE
                else:
                    realized_pnl = (exit_price - entry_price) * position_size
                    result_type = self._classify_time_exit_result(realized_pnl)

        pnl_percent = 0.0 if entry_price == 0.0 else (realized_pnl / entry_price) * 100.0
        trade_metadata = dict(metadata)
        trade_metadata.update(
            {
                "range_mid_hit": range_mid_hit,
                "range_high_hit": range_high_hit,
                "tp1_hit": tp1_hit,
                "tp2_hit": result_type == TradeResultType.TP2,
                "holding_bars": exit_idx - entry_idx,
                "mfe_r": round(max_favorable / stop_distance, 6),
                "mae_r": round(max_adverse / stop_distance, 6),
                "mfe_pct": round((max_favorable / max(entry_price, 1e-12)) * 100.0, 6),
                "mae_pct": round((max_adverse / max(entry_price, 1e-12)) * 100.0, 6),
            }
        )
        return (
            TradeResult(
                entry_price=Price(entry_price),
                exit_price=Price(exit_price),
                entry_timestamp_ms=int(entry_row.timestamp),
                exit_timestamp_ms=int(rows[exit_idx].timestamp),
                result_type=result_type,
                pnl=realized_pnl,
                pnl_percent=Percentage(pnl_percent),
                breakout_timestamp_ms=int(entry_row.timestamp),
                retest_timestamp_ms=None,
                pump_to_peak_bars=None,
                pump_to_peak_minutes=None,
                metadata=trade_metadata,
            ),
            exit_idx,
        )

    @staticmethod
    def _build_trade_detail(trade: TradeResult) -> dict[str, object]:
        detail: dict[str, object] = {
            "entry_timestamp_ms": int(trade.entry_timestamp_ms),
            "exit_timestamp_ms": int(trade.exit_timestamp_ms),
            "pnl": float(trade.pnl),
            "pnl_percent": float(trade.pnl_percent.value),
            "result_type": trade.result_type.value,
        }
        if trade.metadata:
            detail.update(trade.metadata)
        return detail

    @staticmethod
    def _classify_time_exit_result(pnl: float) -> TradeResultType:
        epsilon = 1e-9
        if pnl < -epsilon:
            return TradeResultType.SL
        if pnl > epsilon:
            return TradeResultType.TIME_EXIT_PROFIT
        return TradeResultType.BE
