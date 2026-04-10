"""Strategy wrapper for PNO."""

from __future__ import annotations

import pandas as pd

from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.pno.config import PnoParams, build_pno_grid, validate_pno_params, with_pno_risk
from strategy.pno.engine import PnoEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class PnoStrategy(BaseStrategy[PnoParams]):
    def __init__(self, *, deposit: float, risk_pct: float, entry_confirmation_mode_filter: str | None = None) -> None:
        self._deposit = deposit
        self._risk_pct = risk_pct
        self._entry_confirmation_mode_filter = entry_confirmation_mode_filter
        self._engine = PnoEngine()

    def validate_config(self, params: PnoParams) -> None:
        validate_pno_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return self._engine.prepare_data(data)

    def generate_events(self, data: pd.DataFrame, params: PnoParams) -> list[TradeResult]:
        prepared = self._engine.prepare_data(data)
        return self._engine.generate_events_single_frame(frame=prepared, params=params)

    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: PnoParams,
        **context: object,
    ) -> list[TradeResult]:
        del context
        return self._engine.generate_events_multi_tf(
            levels_frame=mtf_frames.levels_frame,
            entry_frame=mtf_frames.entry_frame,
            params=params,
        )

    def build_parameter_grid(self) -> list[PnoParams]:
        grid = build_pno_grid()
        if self._entry_confirmation_mode_filter is not None:
            grid = [params for params in grid if params.entry_confirmation_mode == self._entry_confirmation_mode_filter]
        return [
            with_pno_risk(params, deposit=self._deposit, risk_pct=self._risk_pct)
            for params in grid
        ]

    def params_to_row(self, params: PnoParams) -> dict[str, int | float | str | None]:
        return {
            "pno_variant_id": params.pno_variant_id,
            "pno_entry_confirmation_mode": params.entry_confirmation_mode,
            "pno_levels_timeframe": params.levels_timeframe.value,
            "pno_entry_timeframe": params.entry_timeframe.value,
            "pno_deposit": params.pno_deposit,
            "pno_risk_pct": params.pno_risk_pct,
            "pno_r_trade": params.pno_r_trade if params.pno_r_trade is not None else params.pno_deposit * params.pno_risk_pct,
            "pno_fee_rate": params.fee_rate,
            "pno_min_data_5m": params.min_data_5m,
            "pno_min_data_1m": params.min_data_1m,
            "pno_min_stage1_leg_v1": params.min_stage1_leg_v1,
            "pno_min_stage1_leg_v5_fraction": params.min_stage1_leg_v5_fraction,
            "pno_stage1_hold_fraction": params.stage1_hold_fraction,
            "pno_pullback_min_v1": params.pullback_min_v1,
            "pno_pullback_min_pump_fraction_5m": params.pullback_min_pump_fraction_5m,
            "pno_pullback_valid_max_leg_fraction": params.pullback_valid_max_leg_fraction,
            "pno_pullback_invalid_max_leg_fraction": params.pullback_invalid_max_leg_fraction,
            "pno_pullback_valid_max_v5": params.pullback_valid_max_v5,
            "pno_pullback_invalid_max_v5": params.pullback_invalid_max_v5,
            "pno_pullback_max_age_bars": params.pullback_max_age_bars,
            "pno_stage1_min_cumulative_quote_volume": params.stage1_min_cumulative_quote_volume,
            "pno_stage1_pre_pump_ema_crosses_min": params.stage1_pre_pump_ema_crosses_min,
            "pno_stage1_barcode_max_fraction_1h": params.stage1_barcode_max_fraction_1h,
            "pno_stage1_barcode_tr_atr_fraction": params.stage1_barcode_tr_atr_fraction,
            "pno_stage1_barcode_tr_price_fraction": params.stage1_barcode_tr_price_fraction,
            "pno_stage1_min_impulse_atr_pre": params.stage1_min_impulse_atr_pre,
            "pno_stage1_min_peak_bar_tr_atr_pre": params.stage1_min_peak_bar_tr_atr_pre,
            "pno_stage1_min_volume_ratio_start": params.stage1_min_volume_ratio_start,
            "pno_stage1_min_volume_ratio_continue": params.stage1_min_volume_ratio_continue,
            "pno_stage1_min_path_efficiency": params.stage1_min_path_efficiency,
            "pno_stage1_max_wick_share": params.stage1_max_wick_share,
            "pno_stage1_min_body_share_mean": params.stage1_min_body_share_mean,
            "pno_stage1_max_flat_body_share": params.stage1_max_flat_body_share,
            "pno_stage1_min_body_wick_edge": params.stage1_min_body_wick_edge,
            "pno_stage1_max_micro_flat_bar_share": params.stage1_max_micro_flat_bar_share,
            "pno_stage1_max_active_high_upper_wick_share": params.stage1_max_active_high_upper_wick_share,
            "pno_stage1_max_red_body_share_5m": params.stage1_max_red_body_share_5m,
            "pno_stage1_max_counterflow_ratio_5m": params.stage1_max_counterflow_ratio_5m,
            "pno_stage1_max_red_body_share_1m": params.stage1_max_red_body_share_1m,
            "pno_stage1_max_counterflow_ratio_1m": params.stage1_max_counterflow_ratio_1m,
            "pno_stage1_min_pump_pct": params.stage1_min_pump_pct,
            "pno_stage1_min_pretrend_range_ratio_2h": params.stage1_min_pretrend_range_ratio_2h,
            "pno_stage1_pre_pump_high_max_fraction_of_leg": params.stage1_pre_pump_high_max_fraction_of_leg,
            "pno_stage3_max_post_high_wick_share": params.stage3_max_post_high_wick_share,
            "pno_stage3_max_post_high_body_overlap_rate": params.stage3_max_post_high_body_overlap_rate,
            "pno_level_cluster_spread_v1": params.level_cluster_spread_v1,
            "pno_level_cluster_relaxed_spread_v1": params.level_cluster_relaxed_spread_v1,
            "pno_level_latest_high_max_age_bars": params.level_latest_high_max_age_bars,
            "pno_level_touch_tolerance_v1": params.level_touch_tolerance_v1,
            "pno_level_low_minor_break_v1": params.level_low_minor_break_v1,
            "pno_level_low_major_break_v1": params.level_low_major_break_v1,
            "pno_level_min_maturity_fraction": params.level_min_maturity_fraction,
            "pno_level_rearm_min_distance_v1": params.level_rearm_min_distance_v1,
            "pno_max_level_touches": params.max_level_touches,
            "pno_min_score": params.min_score,
            "pno_strong_score": params.strong_score,
            "pno_slip_plan_v1_fraction": params.slip_plan_v1_fraction,
            "pno_min_tick_fraction": params.min_tick_fraction,
            "pno_max_entry_pullback_fraction": params.max_entry_pullback_fraction,
            "pno_min_entry_rr": params.min_entry_rr,
            "pno_close_above_max_entry_pos": params.close_above_max_entry_pos,
            "pno_close_above_max_pullback_fraction_of_leg": params.close_above_max_pullback_fraction_of_leg,
            "pno_close_above_max_post_high_wick_share": params.close_above_max_post_high_wick_share,
            "pno_close_above_min_signal_volume_vs_recent": params.close_above_min_signal_volume_vs_recent,
            "pno_tp1_share": params.tp1_share,
            "pno_be_arm_to_active_high_fraction": params.be_arm_to_active_high_fraction,
            "pno_close_above_be_start_fraction": params.close_above_be_start_fraction,
            "pno_close_above_be_step_fraction": params.close_above_be_step_fraction,
            "pno_close_above_be_step_bars": params.close_above_be_step_bars,
            "pno_close_above_be_min_fraction": params.close_above_be_min_fraction,
            "pno_be_buffer_r_fraction": params.be_buffer_r_fraction,
            "deposit": params.pno_deposit,
        }

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        return dict(self._engine.consume_last_generation_diagnostics())
