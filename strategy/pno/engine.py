"""Signal engine for PNO."""

from __future__ import annotations

from typing import TypedDict

import pandas as pd

from domain.models.trade_result import TradeResult
from strategy.pno.config import PnoParams

PNO_STAGE_1_PUMP = "stage_1_pump"
PNO_STAGE_2_HIGH_PULLBACK = "stage_2_high_pullback"
PNO_STAGE_3_VALID_PULLBACK = "stage_3_valid_pullback"
PNO_STAGE_4_LEVEL = "stage_4_level"
PNO_STAGE_5_TRADE = "stage_5_trade"
PNO_STAGE_SEQUENCE: tuple[str, ...] = (
    PNO_STAGE_1_PUMP,
    PNO_STAGE_2_HIGH_PULLBACK,
    PNO_STAGE_3_VALID_PULLBACK,
    PNO_STAGE_4_LEVEL,
    PNO_STAGE_5_TRADE,
)
PNO_STAGE_PATH = " > ".join(PNO_STAGE_SEQUENCE)


class GenerationDiagnostics(TypedDict, total=False):
    trades_generated: int
    stage_hits: dict[str, int]
    stage_events: list[dict[str, object]]
    context: dict[str, object]


class PnoEngine:
    REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self) -> None:
        self._last_generation_diagnostics: GenerationDiagnostics = {
            "trades_generated": 0,
            "stage_hits": {stage_id: 0 for stage_id in PNO_STAGE_SEQUENCE},
            "stage_events": [],
            "context": {"stage_order": list(PNO_STAGE_SEQUENCE)},
        }

    def consume_last_generation_diagnostics(self) -> GenerationDiagnostics:
        diagnostics = dict(self._last_generation_diagnostics)
        self._last_generation_diagnostics = {
            "trades_generated": 0,
            "stage_hits": {stage_id: 0 for stage_id in PNO_STAGE_SEQUENCE},
            "stage_events": [],
            "context": {"stage_order": list(PNO_STAGE_SEQUENCE)},
        }
        return diagnostics

    @staticmethod
    def validate_config(params: PnoParams) -> None:
        del params

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        missing = [column for column in self.REQUIRED_COLUMNS if column not in data.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        prepared = data.loc[:, list(self.REQUIRED_COLUMNS)].copy()
        prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp"])
        prepared["timestamp"] = prepared["timestamp"].astype("int64")
        for column in self.REQUIRED_COLUMNS[1:]:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        return prepared.reset_index(drop=True)

    def generate_events_single_frame(self, *, frame: pd.DataFrame, params: PnoParams) -> list[TradeResult]:
        del frame, params
        return []

    def generate_events_multi_tf(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
    ) -> list[TradeResult]:
        del levels_frame, entry_frame, params
        return []
