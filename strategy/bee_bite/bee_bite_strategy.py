"""Стратегия bee_bite поверх собственного FSM-движка."""

from __future__ import annotations

import warnings

import pandas as pd

from domain.models.trade_result import TradeResult
from simulation.portfolio_state_engine import PortfolioEngineConfig, PortfolioStateEngine
from strategy.base_strategy import BaseStrategy
from strategy.bee_bite.config import (
    BeeBiteGridMode,
    BeeBiteParams,
    BeeBiteProfileId,
    BeeBiteReclaimMode,
    BeeBiteRetestMode,
    build_bee_bite_grid,
    get_bee_bite_score_threshold,
    get_bee_bite_top_n,
    validate_bee_bite_params,
)
from strategy.bee_bite.engine import BeeBiteEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class BeeBiteStrategy(BaseStrategy[BeeBiteParams]):
    def __init__(
        self,
        *,
        profile_id: BeeBiteProfileId,
        grid_mode: BeeBiteGridMode,
        reclaim_mode: BeeBiteReclaimMode,
        retest_mode: BeeBiteRetestMode,
        cooldown_hours: int,
        max_age_range_hours: int,
    ) -> None:
        self._profile_id = profile_id
        self._grid_mode = grid_mode
        self._reclaim_mode = reclaim_mode
        self._retest_mode = retest_mode
        self._cooldown_hours = cooldown_hours
        self._max_age_range_hours = max_age_range_hours
        self._engine = BeeBiteEngine()
        self._last_generation_diagnostics: dict[str, object] = {}

    def validate_config(self, params: BeeBiteParams) -> None:
        validate_bee_bite_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data):
        return self._engine.prepare_data(data)

    def generate_events(self, data, params: BeeBiteParams):
        warnings.warn(
            "BeeBiteStrategy.generate_events(...) deprecated: используйте generate_events_portfolio(...) "
            "(или generate_events_multi_tf для совместимости с legacy-вызовами).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._engine.generate_events(data, params)

    def generate_events_multi_tf(self, *, mtf_frames: SymbolMtfFrames, params: BeeBiteParams):
        warnings.warn(
            "BeeBiteStrategy.generate_events_multi_tf(...) deprecated: основной путь для bee_bite — "
            "generate_events_portfolio(...).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._engine.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    def generate_events_portfolio(
        self,
        *,
        symbol_frames: dict[str, SymbolMtfFrames],
        params: BeeBiteParams,
    ) -> list[TradeResult]:
        entry_frames: dict[str, pd.DataFrame] = {}
        for symbol, mtf in sorted(symbol_frames.items(), key=lambda item: item[0]):
            frame = self._engine.prepare_data(mtf.entry_frame)
            if frame.empty:
                continue
            columns = ["timestamp", "open", "high", "low", "close", "volume"]
            optional_columns = [
                "open_interest",
                "taker_buy_volume",
                "taker_buy_ratio",
                "taker_ratio",
                "avg_volume_range",
                "avg_range_volume",
                "range_volume_avg",
                "oi_reclaim",
                "oi_break_avg",
                "range_volume_zscore",
                "volume_range_zscore",
                "zscore_range_volume",
                "atr_bg",
                "spread",
                "bid_ask_spread",
                "effective_spread",
                "high_pump",
                "lowest_break",
                "support",
                "resistance",
            ]
            columns.extend(column for column in optional_columns if column in frame.columns)
            entry_frames[symbol] = frame[columns].copy()
        if not entry_frames:
            return []

        profile_id = params.bite_profile_id
        engine = PortfolioStateEngine(
            config=PortfolioEngineConfig(
                top_n=get_bee_bite_top_n(profile_id),
                score_threshold=get_bee_bite_score_threshold(profile_id).min_score,
                r_trade=params.bite_r_trade,
                portfolio_risk_limit=params.bite_portfolio_risk_limit,
                min_stop_atr_ratio=params.bite_min_stop_atr_ratio,
                t_max_in_trade=params.bite_t_max_in_trade,
                cooldown_bars=self._hours_to_15m_bars(params.bite_cooldown_hours),
                max_age_range_bars=self._hours_to_15m_bars(params.bite_max_age_range_hours),
                reclaim_limit_bars=params.bite_reclaim_limit_bars,
                bee_bite_profile_id=profile_id,
            ),
            commission_rate=0.0,
            slippage=0.0,
        )
        trades = engine.run(entry_frames)
        self._last_generation_diagnostics = {
            "mode": "portfolio_only",
            "profile_id": profile_id,
            "top_n": get_bee_bite_top_n(profile_id),
            "score_threshold": get_bee_bite_score_threshold(profile_id).min_score,
            "portfolio_score": engine.consume_last_run_diagnostics(),
            "trades_generated": len(trades),
        }
        return trades

    @staticmethod
    def _hours_to_15m_bars(hours: int) -> int:
        return max(1, hours * 4)

    def build_parameter_grid(self) -> list[BeeBiteParams]:
        return build_bee_bite_grid(
            profile_id=self._profile_id,
            grid_mode=self._grid_mode,
            reclaim_mode=self._reclaim_mode,
            retest_mode=self._retest_mode,
            cooldown_hours=self._cooldown_hours,
            max_age_range_hours=self._max_age_range_hours,
        )

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
            "bite_min_depth_threshold": params.bite_min_depth_threshold,
            "bite_micro_offset": params.bite_micro_offset,
            "bite_reclaim_limit_bars": params.bite_reclaim_limit_bars,
            "bite_reclaim_mode": params.bite_reclaim_mode,
            "bite_retest_mode": params.bite_retest_mode,
            "bite_max_age_range_hours": params.bite_max_age_range_hours,
            "bite_cooldown_hours": params.bite_cooldown_hours,
            "bite_r_trade": params.bite_r_trade,
            "bite_portfolio_risk_limit": params.bite_portfolio_risk_limit,
            "bite_min_stop_atr_ratio": params.bite_min_stop_atr_ratio,
            "bite_t_max_in_trade": params.bite_t_max_in_trade,
        }

    def prepare_symbol_context(self, *, symbol: str, mtf_frames: SymbolMtfFrames, params: BeeBiteParams):
        return None

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        if self._last_generation_diagnostics:
            diagnostics = self._last_generation_diagnostics.copy()
            self._last_generation_diagnostics = {}
            return diagnostics
        return self._engine.consume_last_generation_diagnostics()
