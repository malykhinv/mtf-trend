"""Стратегия bee_bite поверх собственного FSM-движка."""

from __future__ import annotations

from strategy.base_strategy import BaseStrategy
from strategy.bee_bite.config import (
    BeeBiteGridMode,
    BeeBiteParams,
    BeeBiteProfileId,
    build_bee_bite_grid,
    validate_bee_bite_params,
)
from strategy.bee_bite.engine import BeeBiteEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class BeeBiteStrategy(BaseStrategy[BeeBiteParams]):
    def __init__(self, *, profile_id: BeeBiteProfileId, grid_mode: BeeBiteGridMode) -> None:
        self._profile_id = profile_id
        self._grid_mode = grid_mode
        self._engine = BeeBiteEngine()

    def validate_config(self, params: BeeBiteParams) -> None:
        validate_bee_bite_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data):
        return self._engine.prepare_data(data)

    def generate_events(self, data, params: BeeBiteParams):
        return self._engine.generate_events(data, params)

    def generate_events_multi_tf(self, *, mtf_frames: SymbolMtfFrames, params: BeeBiteParams):
        return self._engine.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    def build_parameter_grid(self) -> list[BeeBiteParams]:
        return build_bee_bite_grid(profile_id=self._profile_id, grid_mode=self._grid_mode)

    def params_to_row(self, params: BeeBiteParams) -> dict[str, int | float | str | None]:
        return {
            "bite_profile_id": params.bite_profile_id,
            "bite_grid_mode": params.bite_grid_mode,
            "bite_lookback": params.bite_lookback,
            "bite_volume_mult": params.bite_volume_mult,
            "bite_retest_window_hours": params.bite_retest_window_hours,
            "bite_min_rr": params.bite_min_rr,
            "bite_tp2_mult": params.bite_tp2_mult,
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
        return None

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        return self._engine.consume_last_generation_diagnostics()
