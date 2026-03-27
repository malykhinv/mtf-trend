"""Strategy wrapper for post-pump absorption."""

from __future__ import annotations

import pandas as pd

from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.post_pump_absorption.config import (
    POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME,
    PostPumpAbsorptionParams,
    PostPumpAbsorptionProfileId,
    build_post_pump_absorption_grid,
    validate_post_pump_absorption_params,
    with_post_pump_absorption_risk,
)
from strategy.post_pump_absorption.engine import PostPumpAbsorptionEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class PostPumpAbsorptionStrategy(BaseStrategy[PostPumpAbsorptionParams]):
    def __init__(
        self,
        *,
        profile_id: PostPumpAbsorptionProfileId,
        deposit: float,
        risk_pct: float,
    ) -> None:
        self._profile_id = profile_id
        self._deposit = deposit
        self._risk_pct = risk_pct
        self._engine = PostPumpAbsorptionEngine()
        self._symbol_context_cache: dict[tuple[str, int, int], dict[str, pd.DataFrame | str]] = {}

    def validate_config(self, params: PostPumpAbsorptionParams) -> None:
        validate_post_pump_absorption_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return self._engine.prepare_data(data)

    def generate_events(self, data: pd.DataFrame, params: PostPumpAbsorptionParams) -> list[TradeResult]:
        return self._engine.generate_events(data, params)

    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: PostPumpAbsorptionParams,
        **context: object,
    ) -> list[TradeResult]:
        return self._engine.generate_events_multi_tf(
            entry_frame=mtf_frames.entry_frame,
            prepared_entry_frame=(
                context.get("prepared_entry_frame")
                if isinstance(context.get("prepared_entry_frame"), pd.DataFrame)
                else None
            ),
            params=params,
            oi_frame=context.get("oi_frame") if isinstance(context.get("oi_frame"), pd.DataFrame) else None,
            oi_source_timeframe=(
                str(context.get("oi_source_timeframe"))
                if context.get("oi_source_timeframe") is not None
                else None
            ),
            collect_diagnostics=bool(context.get("collect_diagnostics", True)),
        )

    def build_parameter_grid(self) -> list[PostPumpAbsorptionParams]:
        return [
            with_post_pump_absorption_risk(params, deposit=self._deposit, risk_pct=self._risk_pct)
            for params in build_post_pump_absorption_grid(profile_id=self._profile_id)
        ]

    def params_to_row(self, params: PostPumpAbsorptionParams) -> dict[str, int | float | str | None]:
        return {
            "ppa_profile_id": params.profile_id,
            "ppa_grid_variant_id": params.grid_variant_id,
            "ppa_atr_window_minutes": params.atr_window_minutes,
            "ppa_pump_window_minutes": params.pump_window_minutes,
            "ppa_pump_baseline_window_minutes": params.pump_baseline_window_minutes,
            "ppa_pump_min_move_atr": params.pump_min_move_atr,
            "ppa_pump_volume_mult": params.pump_volume_mult,
            "ppa_range_min_minutes": params.range_min_minutes,
            "ppa_range_max_minutes": params.range_max_minutes,
            "ppa_lower_zone_fraction": params.lower_zone_fraction,
            "ppa_max_range_width_atr": params.max_range_width_atr,
            "ppa_max_range_width_pump_fraction": params.max_range_width_pump_fraction,
            "ppa_taker_ratio_threshold": params.taker_ratio_threshold,
            "ppa_taker_volume_mult": params.taker_volume_mult,
            "ppa_oi_min_delta_pct": params.oi_min_delta_pct,
            "ppa_oi_ratio_threshold_relaxation": params.oi_ratio_threshold_relaxation,
            "ppa_oi_volume_mult_relaxation": params.oi_volume_mult_relaxation,
            "ppa_flow_baseline_window_minutes": params.flow_baseline_window_minutes,
            "ppa_structure_break_minutes": params.structure_break_minutes,
            "ppa_micro_base_minutes": params.micro_base_minutes,
            "ppa_micro_base_max_width_atr": params.micro_base_max_width_atr,
            "ppa_entry_break_buffer_atr": params.entry_break_buffer_atr,
            "ppa_stop_buffer_atr": params.stop_buffer_atr,
            "ppa_min_stop_atr": params.min_stop_atr,
            "ppa_max_stop_atr": params.max_stop_atr,
            "ppa_max_stop_range_fraction": params.max_stop_range_fraction,
            "ppa_max_entry_range_fraction": params.max_entry_range_fraction,
            "ppa_tp1_share": params.tp1_share,
            "ppa_be_buffer_pct": params.be_buffer_pct,
            "ppa_time_exit_minutes": params.time_exit_minutes,
            "ppa_deposit": params.ppa_deposit,
            "ppa_risk_pct": params.ppa_risk_pct,
            "ppa_r_trade": params.ppa_r_trade,
        }

    def prepare_symbol_context(
        self,
        *,
        symbol: str,
        mtf_frames: SymbolMtfFrames,
        params: PostPumpAbsorptionParams,
    ) -> dict[str, pd.DataFrame | str] | None:
        del params

        cache_key = (
            symbol,
            id(mtf_frames.entry_frame),
            id(mtf_frames.levels_frame),
        )
        cached = self._symbol_context_cache.get(cache_key)
        if cached is not None:
            return cached

        oi_frame: pd.DataFrame | None = None
        oi_source_timeframe: str | None = None
        prepared_entry_frame = self._engine.prepare_data(mtf_frames.entry_frame)
        if mtf_frames.entry_timeframe == Timeframe.M5 and "open_interest" in mtf_frames.entry_frame.columns:
            oi_frame = mtf_frames.entry_frame
            oi_source_timeframe = mtf_frames.entry_timeframe.value
        elif (
            mtf_frames.levels_timeframe == POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME
            and "open_interest" in mtf_frames.levels_frame.columns
        ):
            oi_frame = mtf_frames.levels_frame
            oi_source_timeframe = mtf_frames.levels_timeframe.value

        context: dict[str, pd.DataFrame | str] = {
            "prepared_entry_frame": prepared_entry_frame,
        }
        if oi_frame is not None and oi_source_timeframe is not None:
            context["oi_frame"] = oi_frame
            context["oi_source_timeframe"] = oi_source_timeframe
        self._symbol_context_cache[cache_key] = context
        return context

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        return dict(self._engine.consume_last_generation_diagnostics())
