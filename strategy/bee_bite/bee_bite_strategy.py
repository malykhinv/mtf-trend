"""Адаптер стратегии bee_bite поверх breakout-ядра."""

from __future__ import annotations

from strategy.base_strategy import BaseStrategy
from strategy.bee_bite.config import (
    BeeBiteGridMode,
    BeeBiteParams,
    BeeBiteProfileId,
    build_bee_bite_grid,
    validate_bee_bite_params,
)
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import BreakoutParams
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class BeeBiteStrategy(BaseStrategy[BeeBiteParams]):
    """bee_bite использует breakout-логику с собственными именами параметров."""

    def __init__(
        self,
        breakout: BreakoutStrategy,
        *,
        profile_id: BeeBiteProfileId,
        grid_mode: BeeBiteGridMode,
    ) -> None:
        self._breakout = breakout
        self._profile_id = profile_id
        self._grid_mode = grid_mode

    def _to_breakout_params(self, params: BeeBiteParams) -> BreakoutParams:
        return BreakoutParams(
            lookback=params.bite_lookback,
            volume_mult=params.bite_volume_mult,
            retest_window_hours=params.bite_retest_window_hours,
            retest_zone=params.bite_retest_zone,
            min_rr=params.bite_min_rr,
            sl_mode=params.bite_sl_mode,
            tp2_mult=params.bite_tp2_mult,
            min_body_ratio=params.bite_min_body_ratio,
            min_move_atr=params.bite_min_move_atr,
            max_retest_depth=params.bite_max_retest_depth,
            confirmation_bars=params.bite_confirmation_bars,
            entry_trigger=params.bite_entry_trigger,
            symbol=params.symbol,
            retest_zone_atr=params.bite_retest_zone_atr,
            levels_timeframe=params.levels_timeframe,
            entry_timeframe=params.entry_timeframe,
            r_trade=params.bite_r_trade,
            portfolio_risk_limit=params.bite_portfolio_risk_limit,
            min_stop_atr_ratio=params.bite_min_stop_atr_ratio,
            t_max_in_trade=params.bite_t_max_in_trade,
        )

    @staticmethod
    def _from_breakout_params(params: BreakoutParams) -> BeeBiteParams:
        return BeeBiteParams(
            bite_lookback=params.lookback,
            bite_volume_mult=params.volume_mult,
            bite_retest_window_hours=params.retest_window_hours,
            bite_retest_zone=params.retest_zone,
            bite_min_rr=params.min_rr,
            bite_sl_mode=params.sl_mode,
            bite_tp2_mult=params.tp2_mult,
            bite_min_body_ratio=params.min_body_ratio,
            bite_min_move_atr=params.min_move_atr,
            bite_max_retest_depth=params.max_retest_depth,
            bite_confirmation_bars=params.confirmation_bars,
            bite_entry_trigger=params.entry_trigger,
            symbol=params.symbol,
            bite_retest_zone_atr=params.retest_zone_atr,
            levels_timeframe=params.levels_timeframe,
            entry_timeframe=params.entry_timeframe,
            bite_r_trade=params.r_trade,
            bite_portfolio_risk_limit=params.portfolio_risk_limit,
            bite_min_stop_atr_ratio=params.min_stop_atr_ratio,
            bite_t_max_in_trade=params.t_max_in_trade,
        )

    def validate_config(self, params: BeeBiteParams) -> None:
        validate_bee_bite_params(params)
        self._breakout.validate_config(self._to_breakout_params(params))

    def prepare_data(self, data):
        return self._breakout.prepare_data(data)

    def generate_events(self, data, params: BeeBiteParams):
        return self._breakout.generate_events(data, self._to_breakout_params(params))

    def generate_events_multi_tf(self, *, mtf_frames: SymbolMtfFrames, params: BeeBiteParams):
        return self._breakout.generate_events_multi_tf(mtf_frames=mtf_frames, params=self._to_breakout_params(params))

    def generate_events_portfolio(self, *, symbol_frames: dict[str, SymbolMtfFrames], params: BeeBiteParams):
        return self._breakout.generate_events_portfolio(
            symbol_frames=symbol_frames,
            params=self._to_breakout_params(params),
        )

    def build_parameter_grid(self) -> list[BeeBiteParams]:
        return build_bee_bite_grid(profile_id=self._profile_id, grid_mode=self._grid_mode)

    def params_to_row(self, params: BeeBiteParams) -> dict[str, int | float | str | None]:
        return {
            "bite_profile_id": params.bite_profile_id,
            "bite_grid_mode": params.bite_grid_mode,
            "bite_lookback": params.bite_lookback,
            "bite_volume_mult": params.bite_volume_mult,
            "bite_retest_window_hours": params.bite_retest_window_hours,
            "bite_retest_zone": params.bite_retest_zone,
            "bite_min_rr": params.bite_min_rr,
            "bite_retest_zone_atr": params.bite_retest_zone_atr,
            "bite_sl_mode": params.bite_sl_mode.value,
            "bite_tp2_mult": params.bite_tp2_mult,
            "bite_min_body_ratio": params.bite_min_body_ratio,
            "bite_min_move_atr": params.bite_min_move_atr,
            "bite_max_retest_depth": params.bite_max_retest_depth,
            "bite_confirmation_bars": params.bite_confirmation_bars,
            "bite_entry_trigger": params.bite_entry_trigger.value,
            "bite_r_trade": params.bite_r_trade,
            "bite_portfolio_risk_limit": params.bite_portfolio_risk_limit,
            "bite_min_stop_atr_ratio": params.bite_min_stop_atr_ratio,
            "bite_t_max_in_trade": params.bite_t_max_in_trade,
        }

    def prepare_symbol_context(self, *, symbol: str, mtf_frames: SymbolMtfFrames, params: BeeBiteParams):
        return self._breakout.prepare_symbol_context(
            symbol=symbol,
            mtf_frames=mtf_frames,
            params=self._to_breakout_params(params),
        )

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        return self._breakout.consume_last_generation_diagnostics()
