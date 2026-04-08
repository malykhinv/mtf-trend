"""Strategy wrapper for PNO."""

from __future__ import annotations

import pandas as pd

from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.pno.config import PnoParams, build_pno_grid, validate_pno_params, with_pno_risk
from strategy.pno.engine import PnoEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class PnoStrategy(BaseStrategy[PnoParams]):
    def __init__(self, *, deposit: float, risk_pct: float) -> None:
        self._deposit = deposit
        self._risk_pct = risk_pct
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
        return [
            with_pno_risk(params, deposit=self._deposit, risk_pct=self._risk_pct)
            for params in build_pno_grid()
        ]

    def params_to_row(self, params: PnoParams) -> dict[str, int | float | str | None]:
        return {
            "pno_variant_id": params.pno_variant_id,
            "pno_deposit": params.pno_deposit,
            "pno_risk_pct": params.pno_risk_pct,
            "pno_r_trade": params.pno_r_trade if params.pno_r_trade is not None else params.pno_deposit * params.pno_risk_pct,
            "pno_fee_rate": params.fee_rate,
            "pno_min_score": params.min_score,
            "pno_strong_score": params.strong_score,
            "deposit": params.pno_deposit,
        }

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        return dict(self._engine.consume_last_generation_diagnostics())
