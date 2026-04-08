"""Signal engine for PNO."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypedDict

import numpy as np
import pandas as pd

from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
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


@dataclass(slots=True)
class OneMinuteFrame:
    frame: pd.DataFrame
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    quote_volume: np.ndarray
    cumulative_quote_volume: np.ndarray
    tr: np.ndarray
    v1: np.ndarray
    red: np.ndarray
    confirmed_high_indices: np.ndarray
    confirmed_high_confirmed_at: np.ndarray
    confirmed_low_indices: np.ndarray
    confirmed_low_confirmed_at: np.ndarray


@dataclass(slots=True)
class RangeSearchTree:
    size: int
    tree: np.ndarray
    is_min_tree: bool


@dataclass(slots=True)
class RetiredCluster:
    active_high_idx: int
    cluster_first_idx: int
    cluster_last_idx: int
    level: float
    pullback_low_idx: int
    pullback_low: float


@dataclass(slots=True)
class FiveMinuteFrame:
    frame: pd.DataFrame
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    quote_volume: np.ndarray
    trade_activity: np.ndarray
    tr: np.ndarray
    v5: np.ndarray
    ema9: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    ema100: np.ndarray
    ema200: np.ndarray
    sleep: np.ndarray
    wake: np.ndarray
    inplay: np.ndarray
    pump_start_idx: np.ndarray
    r3_quote: np.ndarray
    b24_quote: np.ndarray
    r3_trade: np.ndarray
    b24_trade: np.ndarray
    activity_last6_quote: np.ndarray
    activity_prev24_quote: np.ndarray
    activity_last6_trade: np.ndarray
    activity_prev24_trade: np.ndarray
    cumulative_quote_volume: np.ndarray
    pre_high_24h: np.ndarray
    pre_high_1h: np.ndarray
    ema_cross_count_1h: np.ndarray
    atr_pre_14: np.ndarray
    pre_quote_median_24: np.ndarray
    pre_trade_median_24: np.ndarray


@dataclass(slots=True)
class Stage1Context:
    start_idx: int
    start_timestamp: int
    pump_start_5m_idx: int
    pump_start_timestamp: int
    current_5m_idx: int
    active_high_idx: int
    active_high_timestamp: int
    active_high: float
    leg_start_idx: int
    leg_start_timestamp: int
    leg_start: float
    leg_size: float
    hold_floor: float
    cumulative_quote_volume: float = 0.0
    pre_pump_ema_crosses_1h: int = 0
    pre_pump_barcode_fraction_1h: float = 0.0
    pre_pump_high_24h: float = 0.0
    pre_pump_high_1h: float = 0.0
    pump_pre_atr: float = 0.0
    pump_impulse_atr_pre: float = 0.0
    pump_peak_bar_tr_atr_pre: float = 0.0
    pump_volume_ratio_start: float = 0.0
    pump_volume_ratio_continue: float = 0.0


@dataclass(slots=True)
class Stage2Context:
    active_high_idx: int
    active_high_timestamp: int
    active_high: float
    red_after_high_idx: int
    pullback_start_idx: int
    pullback_low_idx: int
    pullback_low_timestamp: int
    pullback_low: float
    pullback_depth: float
    pullback_age_bars: int


@dataclass(slots=True)
class Stage3Context:
    active_high_idx: int
    active_high_timestamp: int
    active_high: float
    pullback_start_idx: int
    pullback_low_idx: int
    pullback_low_timestamp: int
    pullback_low: float
    pullback_depth: float
    pullback_age_bars: int
    validation_timestamp: int


@dataclass(slots=True)
class Stage4Context:
    active_high_idx: int
    active_high_timestamp: int
    active_high: float
    pullback_low_idx: int
    pullback_low_timestamp: int
    pullback_low: float
    pullback_depth: float
    cluster_indices: tuple[int, ...]
    cluster_prices: tuple[float, ...]
    level: float
    level_pos: float
    touches: int
    cluster_first_idx: int
    cluster_last_idx: int
    level_valid_idx: int
    level_valid_timestamp: int
    level_low: float
    level_low_minor_break: bool
    level_low_major_break: bool
    penalty_level_low_break: int
    penalty_untested_highs: int
    base_bonus: int
    pno_index: int
    maturity_penalty: int
    pno_order_adj: int
    score_a: int
    score_b: int
    score_c: int
    score_d: int
    score_e: int
    score_tp2: int
    final_score: float
    entry_plan: float
    sl_plan: float
    low_last_red_plan: float
    tp1: float
    tp2: float
    stage4_ready: bool
    hard_block: bool
    is_valid_setup: bool
    hard_block_reason: str | None
    entry_pos: float = 0.0
    level_maturity_fraction: float = 0.0
    level_age_bars: int = 0


@dataclass(slots=True)
class ArmedContext:
    entry_idx: int
    stage1: Stage1Context
    stage3: Stage3Context
    stage4: Stage4Context


class GenerationDiagnostics(TypedDict, total=False):
    trades_generated: int
    blocked_cycles: int
    skipped_insufficient_data: int
    trade_count_proxy_used: bool
    stage_hits: dict[str, int]
    stage_events: list[dict[str, object]]
    stage_rejections: list[dict[str, object]]
    context: dict[str, object]


class PnoEngine:
    REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
    _EPSILON = 1e-12

    def __init__(self) -> None:
        self._last_generation_diagnostics = self._empty_diagnostics()

    def consume_last_generation_diagnostics(self) -> GenerationDiagnostics:
        diagnostics = dict(self._last_generation_diagnostics)
        stage_hits = diagnostics.get("stage_hits")
        if isinstance(stage_hits, dict):
            diagnostics["stage_hits"] = dict(stage_hits)
        stage_events = diagnostics.get("stage_events")
        if isinstance(stage_events, list):
            diagnostics["stage_events"] = [dict(item) for item in stage_events]
        stage_rejections = diagnostics.get("stage_rejections")
        if isinstance(stage_rejections, list):
            diagnostics["stage_rejections"] = [dict(item) for item in stage_rejections]
        context = diagnostics.get("context")
        if isinstance(context, dict):
            diagnostics["context"] = dict(context)
        self._last_generation_diagnostics = self._empty_diagnostics()
        return diagnostics

    def _empty_diagnostics(self) -> GenerationDiagnostics:
        return {
            "trades_generated": 0,
            "blocked_cycles": 0,
            "skipped_insufficient_data": 0,
            "trade_count_proxy_used": True,
            "stage_hits": {stage_id: 0 for stage_id in PNO_STAGE_SEQUENCE},
            "stage_events": [],
            "stage_rejections": [],
            "context": {
                "stage_order": list(PNO_STAGE_SEQUENCE),
                "trade_count_proxy": "volume",
                "quote_volume_proxy": "close*volume",
            },
        }

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
        prepared = self.prepare_data(frame)
        timeframe_ms = self._infer_timeframe_ms(prepared)
        if timeframe_ms != 60_000:
            self._last_generation_diagnostics = self._empty_diagnostics()
            return []
        levels_frame = self._aggregate_to_5m(prepared)
        return self.generate_events_multi_tf(levels_frame=levels_frame, entry_frame=prepared, params=params)

    def generate_events_multi_tf(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
    ) -> list[TradeResult]:
        prepared_levels = self.prepare_data(levels_frame)
        prepared_entry = self.prepare_data(entry_frame)
        diagnostics = self._empty_diagnostics()
        diagnostics["context"] = {
            **diagnostics["context"],
            "symbol": params.symbol,
            "stage_order": list(PNO_STAGE_SEQUENCE),
        }
        if len(prepared_levels) < params.min_data_5m or len(prepared_entry) < params.min_data_1m:
            diagnostics["skipped_insufficient_data"] = 1
            self._last_generation_diagnostics = diagnostics
            return []

        five = self._prepare_5m_frame(prepared_levels)
        one = self._prepare_1m_frame(prepared_entry)
        trades = self._run(one=one, five=five, params=params, diagnostics=diagnostics)
        diagnostics["trades_generated"] = len(trades)
        self._last_generation_diagnostics = diagnostics
        return trades

    def _prepare_1m_frame(self, frame: pd.DataFrame) -> OneMinuteFrame:
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
        work["tr"] = tr
        work["v1"] = tr.rolling(window=30, min_periods=30).median()
        work["quote_volume"] = work["close"] * work["volume"]
        work["cumulative_quote_volume"] = work["quote_volume"].cumsum()
        work["red"] = work["close"] < work["open"]
        highs = work["high"].astype("float64").to_numpy()
        lows = work["low"].astype("float64").to_numpy()
        v1 = work["v1"].astype("float64").to_numpy()
        confirmed_high_indices, confirmed_high_confirmed_at = self._build_confirmed_high_map(
            highs=highs,
            lows=lows,
            v1=v1,
        )
        confirmed_low_indices, confirmed_low_confirmed_at = self._build_confirmed_low_map(
            highs=highs,
            lows=lows,
            v1=v1,
        )
        return OneMinuteFrame(
            frame=work,
            timestamps=work["timestamp"].astype("int64").to_numpy(),
            opens=work["open"].astype("float64").to_numpy(),
            highs=highs,
            lows=lows,
            closes=work["close"].astype("float64").to_numpy(),
            volumes=work["volume"].astype("float64").to_numpy(),
            quote_volume=work["quote_volume"].astype("float64").to_numpy(),
            cumulative_quote_volume=work["cumulative_quote_volume"].astype("float64").to_numpy(),
            tr=work["tr"].astype("float64").to_numpy(),
            v1=v1,
            red=work["red"].astype("bool").to_numpy(),
            confirmed_high_indices=confirmed_high_indices,
            confirmed_high_confirmed_at=confirmed_high_confirmed_at,
            confirmed_low_indices=confirmed_low_indices,
            confirmed_low_confirmed_at=confirmed_low_confirmed_at,
        )

    def _prepare_5m_frame(self, frame: pd.DataFrame) -> FiveMinuteFrame:
        work = frame.copy()
        work["quote_volume"] = work["close"] * work["volume"]
        work["trade_activity"] = work["volume"]
        prev_close = work["close"].shift(1).fillna(work["close"])
        tr = pd.concat(
            [
                work["high"] - work["low"],
                (work["high"] - prev_close).abs(),
                (work["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        work["tr"] = tr
        work["v5"] = tr.rolling(window=24, min_periods=24).median()
        work["ema9"] = work["close"].ewm(span=9, adjust=False).mean()
        work["ema20"] = work["close"].ewm(span=20, adjust=False).mean()
        work["ema50"] = work["close"].ewm(span=50, adjust=False).mean()
        work["ema100"] = work["close"].ewm(span=100, adjust=False).mean()
        work["ema200"] = work["close"].ewm(span=200, adjust=False).mean()
        ema_sign = work["ema9"] > work["ema20"]
        ema_cross = (
            ema_sign.ne(ema_sign.shift(1))
            & work["ema9"].shift(1).notna()
            & work["ema20"].shift(1).notna()
        )
        work["ema_cross_count_1h"] = ema_cross.shift(1).rolling(window=12, min_periods=12).sum()
        work["r3_quote"] = work["quote_volume"].rolling(window=3, min_periods=3).median()
        work["b24_quote"] = work["quote_volume"].shift(3).rolling(window=24, min_periods=24).median()
        work["l96_quote"] = work["quote_volume"].shift(27).rolling(window=96, min_periods=96).median()
        work["r3_trade"] = work["trade_activity"].rolling(window=3, min_periods=3).median()
        work["b24_trade"] = work["trade_activity"].shift(3).rolling(window=24, min_periods=24).median()
        work["l96_trade"] = work["trade_activity"].shift(27).rolling(window=96, min_periods=96).median()
        work["r3_tr"] = work["tr"].rolling(window=3, min_periods=3).median()
        work["b24_tr"] = work["tr"].shift(3).rolling(window=24, min_periods=24).median()
        work["l96_tr"] = work["tr"].shift(27).rolling(window=96, min_periods=96).median()
        work["activity_last6_quote"] = work["quote_volume"].rolling(window=6, min_periods=6).median()
        work["activity_prev24_quote"] = work["quote_volume"].shift(6).rolling(window=24, min_periods=24).median()
        work["activity_last6_trade"] = work["trade_activity"].rolling(window=6, min_periods=6).median()
        work["activity_prev24_trade"] = work["trade_activity"].shift(6).rolling(window=24, min_periods=24).median()
        work["cumulative_quote_volume"] = work["quote_volume"].cumsum()
        work["pre_high_24h"] = work["high"].shift(1).rolling(window=288, min_periods=288).max()
        work["pre_high_1h"] = work["high"].shift(1).rolling(window=12, min_periods=12).max()
        work["atr_pre_14"] = work["tr"].shift(1).rolling(window=14, min_periods=14).mean()
        work["pre_quote_median_24"] = work["quote_volume"].shift(1).rolling(window=24, min_periods=24).median()
        work["pre_trade_median_24"] = work["trade_activity"].shift(1).rolling(window=24, min_periods=24).median()
        work["close_above_ema20_count12"] = (work["close"] > work["ema20"]).rolling(window=12, min_periods=12).sum()
        work["r3_close_above_ema20_all"] = (work["close"] > work["ema20"]).rolling(window=3, min_periods=3).sum() == 3
        work["r3_close_above_ema9_count"] = (work["close"] > work["ema9"]).rolling(window=3, min_periods=3).sum()
        work["sleep"] = (
            (work["b24_tr"] <= (0.75 * work["l96_tr"]))
            & (work["b24_quote"] <= (0.75 * work["l96_quote"]))
            & (work["b24_trade"] <= (0.75 * work["l96_trade"]))
        )
        work["wake"] = (
            (work["r3_quote"] >= (2.0 * work["b24_quote"]))
            & (work["r3_trade"] >= (2.0 * work["b24_trade"]))
            & work["r3_close_above_ema20_all"]
            & (work["r3_close_above_ema9_count"] >= 2)
            & (work["ema9"] > work["ema20"])
        )
        inplay = np.zeros(len(work), dtype=bool)
        pump_start_idx = np.full(len(work), -1, dtype=np.int64)
        stacked = (
            (work["ema20"] > work["ema50"])
            & (work["ema50"] > work["ema100"])
            & (work["ema100"] > work["ema200"])
        )
        stack_start_idx: int | None = None
        inplay_start_idx: int | None = None
        last_local_12_high_idx: int | None = None
        for idx in range(len(work)):
            if bool(stacked.iloc[idx]):
                if idx == 0 or not bool(stacked.iloc[idx - 1]):
                    stack_start_idx = idx
            else:
                stack_start_idx = None
            current_high = float(work["high"].iloc[idx])
            local_high = float(work["high"].iloc[max(0, idx - 11) : idx + 1].max())
            if current_high >= (local_high - self._EPSILON):
                last_local_12_high_idx = idx
            self_sustain = bool(
                (work["close_above_ema20_count12"].iloc[idx] >= 8)
                and (float(work["close"].iloc[idx]) > float(work["ema20"].iloc[idx]))
                and (float(work["ema9"].iloc[idx]) > float(work["ema20"].iloc[idx]))
                and (
                    (
                        np.isfinite(work["r3_quote"].iloc[idx])
                        and np.isfinite(work["b24_quote"].iloc[idx])
                        and float(work["r3_quote"].iloc[idx]) >= (1.5 * float(work["b24_quote"].iloc[idx]))
                    )
                    or (
                        np.isfinite(work["r3_trade"].iloc[idx])
                        and np.isfinite(work["b24_trade"].iloc[idx])
                        and float(work["r3_trade"].iloc[idx]) >= (1.5 * float(work["b24_trade"].iloc[idx]))
                    )
                )
            )
            wake_transition = bool(work["wake"].iloc[idx]) and idx > 0 and bool(work["sleep"].iloc[idx - 1])
            prev_inplay = bool(inplay[idx - 1]) if idx > 0 else False
            if prev_inplay:
                below_ema20_twice = (
                    idx > 0
                    and float(work["close"].iloc[idx]) < float(work["ema20"].iloc[idx])
                    and float(work["close"].iloc[idx - 1]) < float(work["ema20"].iloc[idx - 1])
                )
                no_new_high_for_24 = (
                    last_local_12_high_idx is not None
                    and (idx - last_local_12_high_idx) >= 24
                    and (
                        self._safe_divide(float(work["activity_last6_quote"].iloc[idx]), float(work["activity_prev24_quote"].iloc[idx])) <= 1.0
                        or self._safe_divide(float(work["activity_last6_trade"].iloc[idx]), float(work["activity_prev24_trade"].iloc[idx])) <= 1.0
                    )
                )
                activity_not_expanding = (
                    float(work["close"].iloc[idx]) < float(work["ema20"].iloc[idx])
                    and self._safe_divide(float(work["activity_last6_quote"].iloc[idx]), float(work["activity_prev24_quote"].iloc[idx])) <= 1.0
                    and self._safe_divide(float(work["activity_last6_trade"].iloc[idx]), float(work["activity_prev24_trade"].iloc[idx])) <= 1.0
                )
                inplay[idx] = not (below_ema20_twice or no_new_high_for_24 or activity_not_expanding)
            else:
                inplay[idx] = wake_transition or self_sustain
                if inplay[idx]:
                    inplay_start_idx = idx
            if bool(inplay[idx]):
                if idx > 0 and not bool(inplay[idx - 1]):
                    inplay_start_idx = idx
                pump_start_idx[idx] = (
                    stack_start_idx
                    if stack_start_idx is not None
                    else (inplay_start_idx if inplay_start_idx is not None else idx)
                )
        work["inplay"] = inplay
        work["pump_start_idx"] = pump_start_idx
        return FiveMinuteFrame(
            frame=work,
            timestamps=work["timestamp"].astype("int64").to_numpy(),
            opens=work["open"].astype("float64").to_numpy(),
            highs=work["high"].astype("float64").to_numpy(),
            lows=work["low"].astype("float64").to_numpy(),
            closes=work["close"].astype("float64").to_numpy(),
            volumes=work["volume"].astype("float64").to_numpy(),
            quote_volume=work["quote_volume"].astype("float64").to_numpy(),
            trade_activity=work["trade_activity"].astype("float64").to_numpy(),
            tr=work["tr"].astype("float64").to_numpy(),
            v5=work["v5"].astype("float64").to_numpy(),
            ema9=work["ema9"].astype("float64").to_numpy(),
            ema20=work["ema20"].astype("float64").to_numpy(),
            ema50=work["ema50"].astype("float64").to_numpy(),
            ema100=work["ema100"].astype("float64").to_numpy(),
            ema200=work["ema200"].astype("float64").to_numpy(),
            sleep=work["sleep"].astype("bool").to_numpy(),
            wake=work["wake"].astype("bool").to_numpy(),
            inplay=work["inplay"].astype("bool").to_numpy(),
            pump_start_idx=work["pump_start_idx"].astype("int64").to_numpy(),
            r3_quote=work["r3_quote"].astype("float64").to_numpy(),
            b24_quote=work["b24_quote"].astype("float64").to_numpy(),
            r3_trade=work["r3_trade"].astype("float64").to_numpy(),
            b24_trade=work["b24_trade"].astype("float64").to_numpy(),
            activity_last6_quote=work["activity_last6_quote"].astype("float64").to_numpy(),
            activity_prev24_quote=work["activity_prev24_quote"].astype("float64").to_numpy(),
            activity_last6_trade=work["activity_last6_trade"].astype("float64").to_numpy(),
            activity_prev24_trade=work["activity_prev24_trade"].astype("float64").to_numpy(),
            cumulative_quote_volume=work["cumulative_quote_volume"].astype("float64").to_numpy(),
            pre_high_24h=work["pre_high_24h"].astype("float64").to_numpy(),
            pre_high_1h=work["pre_high_1h"].astype("float64").to_numpy(),
            ema_cross_count_1h=work["ema_cross_count_1h"].fillna(0.0).astype("float64").to_numpy(),
            atr_pre_14=work["atr_pre_14"].astype("float64").to_numpy(),
            pre_quote_median_24=work["pre_quote_median_24"].astype("float64").to_numpy(),
            pre_trade_median_24=work["pre_trade_median_24"].astype("float64").to_numpy(),
        )

    def _run(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        params: PnoParams,
        diagnostics: GenerationDiagnostics,
    ) -> list[TradeResult]:
        trades: list[TradeResult] = []
        stage_keys: dict[str, tuple[object, ...]] = {}
        one_to_five_idx = np.searchsorted(five.timestamps, one.timestamps, side="right") - 1
        stage1: Stage1Context | None = None
        stage2: Stage2Context | None = None
        stage3: Stage3Context | None = None
        stage4: Stage4Context | None = None
        armed: ArmedContext | None = None
        blocked_active_high_idx: int | None = None
        retired_clusters: list[RetiredCluster] = []
        current_pno_index = 0
        active_pump_start_idx = -1
        rejection_keys: dict[str, tuple[object, ...]] = {}

        def _reject_stage(stage_id: str, *, key: tuple[object, ...], timestamp_ms: int, reason: str, extra: dict[str, object] | None = None) -> None:
            self._mark_stage_rejection(
                diagnostics,
                rejection_keys,
                stage_id,
                key=key,
                timestamp_ms=timestamp_ms,
                reason=reason,
                extra=extra,
            )

        i = 0
        while i < len(one.timestamps):
            five_idx = int(one_to_five_idx[i])
            if five_idx < 0:
                i += 1
                continue

            if armed is not None and armed.entry_idx == i:
                trade, exit_idx = self._try_enter_and_simulate(one=one, params=params, armed=armed)
                if trade is not None:
                    trades.append(trade)
                    retired_clusters.append(
                        RetiredCluster(
                            active_high_idx=armed.stage4.active_high_idx,
                            cluster_first_idx=armed.stage4.cluster_first_idx,
                            cluster_last_idx=armed.stage4.cluster_last_idx,
                            level=float(armed.stage4.level),
                            pullback_low_idx=armed.stage3.pullback_low_idx,
                            pullback_low=float(armed.stage3.pullback_low),
                        )
                    )
                    self._mark_stage(
                        diagnostics,
                        stage_keys,
                        PNO_STAGE_5_TRADE,
                        key=(trade.entry_timestamp_ms, trade.exit_timestamp_ms),
                        timestamp_ms=int(trade.entry_timestamp_ms),
                        extra={
                            "entry_price": round(float(trade.entry_price.value), 8),
                            "exit_price": round(float(trade.exit_price.value), 8),
                            "result_type": trade.result_type.value,
                            "score": armed.stage4.final_score,
                        },
                    )
                    stage1 = None
                    stage2 = None
                    stage3 = None
                    stage4 = None
                    armed = None
                    blocked_active_high_idx = None
                    active_pump_start_idx = -1
                    i = max(i + 1, exit_idx + 1)
                    continue
                _reject_stage(
                    PNO_STAGE_5_TRADE,
                    key=(armed.stage4.active_high_idx, armed.stage4.cluster_first_idx, armed.stage4.cluster_last_idx, armed.entry_idx),
                    timestamp_ms=int(one.timestamps[min(armed.entry_idx, len(one.timestamps) - 1)]),
                    reason="entry_not_triggered",
                    extra={
                        "active_high": round(float(armed.stage4.active_high), 8),
                        "pullback_low": round(float(armed.stage3.pullback_low), 8),
                        "level": round(float(armed.stage4.level), 8),
                        "entry_confirmation_mode": str(getattr(params, "entry_confirmation_mode", "cross")),
                    },
                )
                armed = None

            if (
                i < params.min_data_1m - 1
                or five_idx < params.min_data_5m - 1
                or not np.isfinite(one.v1[i])
                or not np.isfinite(five.v5[five_idx])
                or one.v1[i] <= 0.0
                or five.v5[five_idx] <= 0.0
            ):
                prev_stage1 = stage1
                prev_stage2 = stage2
                prev_stage3 = stage3
                stage1 = None
                stage2 = None
                stage3 = None
                stage4 = None
                armed = None
                blocked_active_high_idx = None
                if prev_stage2 is None and prev_stage1 is not None:
                    _reject_stage(
                        PNO_STAGE_2_HIGH_PULLBACK,
                        key=(prev_stage1.pump_start_5m_idx, prev_stage1.active_high_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="pump_reset_before_pullback",
                        extra={"active_high": round(prev_stage1.active_high, 8)},
                    )
                elif prev_stage3 is None and prev_stage2 is not None:
                    _reject_stage(
                        PNO_STAGE_3_VALID_PULLBACK,
                        key=(prev_stage2.active_high_idx, prev_stage2.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="pullback_reset_before_validation",
                        extra={"pullback_low": round(prev_stage2.pullback_low, 8)},
                    )
                elif prev_stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(prev_stage3.active_high_idx, prev_stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="reset_before_level_found",
                        extra={"pullback_low": round(prev_stage3.pullback_low, 8)},
                    )
                i += 1
                continue

            if not bool(five.inplay[five_idx]):
                prev_stage1 = stage1
                prev_stage2 = stage2
                prev_stage3 = stage3
                stage1 = None
                stage2 = None
                stage3 = None
                stage4 = None
                armed = None
                blocked_active_high_idx = None
                active_pump_start_idx = -1
                if prev_stage2 is None and prev_stage1 is not None:
                    _reject_stage(
                        PNO_STAGE_2_HIGH_PULLBACK,
                        key=(prev_stage1.pump_start_5m_idx, prev_stage1.active_high_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="inplay_ended_before_pullback",
                        extra={"active_high": round(prev_stage1.active_high, 8)},
                    )
                elif prev_stage3 is None and prev_stage2 is not None:
                    _reject_stage(
                        PNO_STAGE_3_VALID_PULLBACK,
                        key=(prev_stage2.active_high_idx, prev_stage2.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="inplay_ended_before_validation",
                        extra={"pullback_low": round(prev_stage2.pullback_low, 8)},
                    )
                elif prev_stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(prev_stage3.active_high_idx, prev_stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="inplay_ended_before_level_found",
                        extra={"pullback_low": round(prev_stage3.pullback_low, 8)},
                    )
                i += 1
                continue

            next_stage1 = self._resolve_stage1_context(one=one, five=five, idx=i, five_idx=five_idx, params=params)
            if next_stage1 is None:
                prev_stage1 = stage1
                prev_stage2 = stage2
                prev_stage3 = stage3
                stage1 = None
                stage2 = None
                stage3 = None
                stage4 = None
                armed = None
                blocked_active_high_idx = None
                active_pump_start_idx = -1
                if prev_stage2 is None and prev_stage1 is not None:
                    _reject_stage(
                        PNO_STAGE_2_HIGH_PULLBACK,
                        key=(prev_stage1.pump_start_5m_idx, prev_stage1.active_high_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="stage1_lost_before_pullback",
                        extra={"active_high": round(prev_stage1.active_high, 8)},
                    )
                elif prev_stage3 is None and prev_stage2 is not None:
                    _reject_stage(
                        PNO_STAGE_3_VALID_PULLBACK,
                        key=(prev_stage2.active_high_idx, prev_stage2.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="stage1_lost_before_validation",
                        extra={"pullback_low": round(prev_stage2.pullback_low, 8)},
                    )
                elif prev_stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(prev_stage3.active_high_idx, prev_stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="stage1_lost_before_level_found",
                        extra={"pullback_low": round(prev_stage3.pullback_low, 8)},
                    )
                i += 1
                continue

            if next_stage1.pump_start_5m_idx != active_pump_start_idx:
                active_pump_start_idx = next_stage1.pump_start_5m_idx
                current_pno_index = 0
                blocked_active_high_idx = None
                retired_clusters.clear()
                if stage2 is None and stage1 is not None:
                    _reject_stage(
                        PNO_STAGE_2_HIGH_PULLBACK,
                        key=(stage1.pump_start_5m_idx, stage1.active_high_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="new_pump_started_before_pullback",
                        extra={"active_high": round(stage1.active_high, 8)},
                    )
                elif stage3 is None and stage2 is not None:
                    _reject_stage(
                        PNO_STAGE_3_VALID_PULLBACK,
                        key=(stage2.active_high_idx, stage2.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="new_pump_started_before_validation",
                        extra={"pullback_low": round(stage2.pullback_low, 8)},
                    )
                elif stage4 is None and stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(stage3.active_high_idx, stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="new_pump_started_before_level_found",
                        extra={"pullback_low": round(stage3.pullback_low, 8)},
                    )
                stage2 = None
                stage3 = None
                stage4 = None
                armed = None

            if stage1 is None or next_stage1.active_high_idx != stage1.active_high_idx:
                if stage1 is not None and next_stage1.active_high_idx != stage1.active_high_idx:
                    if stage2 is None:
                        _reject_stage(
                            PNO_STAGE_2_HIGH_PULLBACK,
                            key=(stage1.pump_start_5m_idx, stage1.active_high_idx),
                            timestamp_ms=int(one.timestamps[i]),
                            reason="new_main_high_before_pullback",
                            extra={"active_high": round(stage1.active_high, 8)},
                        )
                    elif stage3 is None:
                        _reject_stage(
                            PNO_STAGE_3_VALID_PULLBACK,
                            key=(stage2.active_high_idx, stage2.pullback_low_idx),
                            timestamp_ms=int(one.timestamps[i]),
                            reason="new_main_high_before_validation",
                            extra={"pullback_low": round(stage2.pullback_low, 8)},
                        )
                    elif stage4 is None:
                        _reject_stage(
                            PNO_STAGE_4_LEVEL,
                            key=(stage3.active_high_idx, stage3.pullback_low_idx),
                            timestamp_ms=int(one.timestamps[i]),
                            reason="new_main_high_before_level_found",
                            extra={"pullback_low": round(stage3.pullback_low, 8)},
                        )
                    stage2 = None
                    stage3 = None
                    stage4 = None
                    armed = None
                    blocked_active_high_idx = None
                stage1 = next_stage1
                self._mark_stage(
                    diagnostics,
                    stage_keys,
                    PNO_STAGE_1_PUMP,
                    key=(stage1.pump_start_5m_idx, stage1.active_high_idx),
                    timestamp_ms=stage1.active_high_timestamp,
                    extra={
                        "active_high": round(stage1.active_high, 8),
                        "leg_start": round(stage1.leg_start, 8),
                        "leg_size": round(stage1.leg_size, 8),
                        "hold_floor": round(stage1.hold_floor, 8),
                        "cumulative_quote_volume": round(stage1.cumulative_quote_volume, 2),
                        "pre_pump_ema_crosses_1h": int(stage1.pre_pump_ema_crosses_1h),
                        "pre_pump_barcode_fraction_1h": round(stage1.pre_pump_barcode_fraction_1h, 4),
                        "pump_impulse_atr_pre": round(stage1.pump_impulse_atr_pre, 4),
                        "pump_peak_bar_tr_atr_pre": round(stage1.pump_peak_bar_tr_atr_pre, 4),
                        "pump_volume_ratio_start": round(stage1.pump_volume_ratio_start, 4),
                        "pump_volume_ratio_continue": round(stage1.pump_volume_ratio_continue, 4),
                    },
                )
            else:
                stage1 = next_stage1

            if blocked_active_high_idx is not None and blocked_active_high_idx == stage1.active_high_idx:
                i += 1
                continue

            next_stage2 = self._resolve_stage2_context(one=one, idx=i, stage1=stage1, params=params)
            if next_stage2 is None:
                stage2 = None
                stage3 = None
                stage4 = None
                i += 1
                continue
            if stage2 is None or next_stage2.active_high_idx != stage2.active_high_idx:
                stage2 = next_stage2
                self._mark_stage(
                    diagnostics,
                    stage_keys,
                    PNO_STAGE_2_HIGH_PULLBACK,
                    key=(stage2.active_high_idx, stage2.pullback_start_idx),
                    timestamp_ms=stage2.pullback_low_timestamp,
                    extra={
                        "pullback_low": round(stage2.pullback_low, 8),
                        "pullback_depth": round(stage2.pullback_depth, 8),
                    },
                )
            else:
                stage2 = next_stage2

            next_stage3, dead_reason = self._resolve_stage3_context(
                one=one,
                five=five,
                idx=i,
                five_idx=five_idx,
                stage1=stage1,
                stage2=stage2,
                params=params,
            )
            if dead_reason is not None:
                diagnostics["blocked_cycles"] = int(diagnostics.get("blocked_cycles", 0)) + 1
                _reject_stage(
                    PNO_STAGE_3_VALID_PULLBACK,
                    key=(stage2.active_high_idx, stage2.pullback_low_idx),
                    timestamp_ms=int(one.timestamps[i]),
                    reason=str(dead_reason),
                    extra={
                        "active_high": round(stage1.active_high, 8),
                        "pullback_low": round(stage2.pullback_low, 8),
                        "pullback_depth": round(stage2.pullback_depth, 8),
                    },
                )
                blocked_active_high_idx = stage1.active_high_idx
                stage3 = None
                stage4 = None
                armed = None
                i += 1
                continue
            if next_stage3 is None:
                stage3 = None
                stage4 = None
                i += 1
                continue
            if stage3 is None or next_stage3.active_high_idx != stage3.active_high_idx:
                stage3 = next_stage3
                self._mark_stage(
                    diagnostics,
                    stage_keys,
                    PNO_STAGE_3_VALID_PULLBACK,
                    key=(stage3.active_high_idx, stage3.pullback_low_idx),
                    timestamp_ms=stage3.validation_timestamp,
                    extra={
                        "pullback_low": round(stage3.pullback_low, 8),
                        "pullback_depth": round(stage3.pullback_depth, 8),
                    },
                )
            else:
                stage3 = next_stage3

            next_stage4 = self._resolve_stage4_context(
                one=one,
                five=five,
                idx=i,
                five_idx=five_idx,
                stage1=stage1,
                stage3=stage3,
                previous=stage4,
                params=params,
                pno_index=max(current_pno_index, 1),
                retired_clusters=retired_clusters,
            )
            if next_stage4 is None:
                if stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(stage3.active_high_idx, stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="no_level_found",
                        extra={
                            "active_high": round(stage3.active_high, 8),
                            "pullback_low": round(stage3.pullback_low, 8),
                        },
                    )
                stage4 = None
                i += 1
                continue

            cycle_key = (
                next_stage4.active_high_idx,
                next_stage4.cluster_first_idx,
                next_stage4.cluster_last_idx,
                next_stage4.level_valid_idx,
            )
            if stage_keys.get(PNO_STAGE_4_LEVEL) != cycle_key:
                current_pno_index += 1
                next_stage4 = replace(next_stage4, pno_index=current_pno_index)
                next_stage4 = self._rebuild_stage4_scores(
                    one=one,
                    five=five,
                    idx=i,
                    five_idx=five_idx,
                    stage1=stage1,
                    stage3=stage3,
                    stage4=next_stage4,
                    params=params,
                )

            if next_stage4.hard_block:
                diagnostics["blocked_cycles"] = int(diagnostics.get("blocked_cycles", 0)) + 1
                _reject_stage(
                    PNO_STAGE_4_LEVEL,
                    key=cycle_key,
                    timestamp_ms=int(next_stage4.level_valid_timestamp),
                    reason=str(next_stage4.hard_block_reason or "hard_block"),
                    extra={
                        "level": round(next_stage4.level, 8),
                        "touches": int(next_stage4.touches),
                        "entry_pos": round(float(next_stage4.entry_pos), 4),
                    },
                )
                retired_clusters.append(
                    RetiredCluster(
                        active_high_idx=next_stage4.active_high_idx,
                        cluster_first_idx=next_stage4.cluster_first_idx,
                        cluster_last_idx=next_stage4.cluster_last_idx,
                        level=float(next_stage4.level),
                        pullback_low_idx=stage3.pullback_low_idx,
                        pullback_low=float(stage3.pullback_low),
                    )
                )
                stage4 = None
                armed = None
                i += 1
                continue

            stage4 = next_stage4
            self._mark_stage(
                diagnostics,
                stage_keys,
                PNO_STAGE_4_LEVEL,
                key=cycle_key,
                timestamp_ms=stage4.level_valid_timestamp,
                extra={
                    "level": round(stage4.level, 8),
                    "touches": stage4.touches,
                    "score": round(stage4.final_score, 4),
                    "pno_index": stage4.pno_index,
                    "level_maturity_fraction": round(stage4.level_maturity_fraction, 4),
                    "entry_pos": round(stage4.entry_pos, 4),
                },
            )
            if stage4.is_valid_setup and i + 1 < len(one.timestamps):
                armed = ArmedContext(entry_idx=i + 1, stage1=stage1, stage3=stage3, stage4=stage4)

            i += 1
        return trades

    @staticmethod
    def _range_sum(cumulative: np.ndarray, start_idx: int, end_idx: int) -> float:
        if start_idx > end_idx or start_idx < 0 or end_idx >= cumulative.shape[0]:
            return 0.0
        start_value = float(cumulative[start_idx - 1]) if start_idx > 0 else 0.0
        return float(cumulative[end_idx]) - start_value

    @staticmethod
    def _resolve_level_maturity_fraction(*, active_high_idx: int, cluster_first_idx: int, current_idx: int) -> float:
        total_age = max(current_idx - active_high_idx, 0)
        if total_age <= 0:
            return 0.0
        level_age = max(current_idx - cluster_first_idx, 0)
        return max(min(level_age / total_age, 1.0), 0.0)

    @staticmethod
    def _resolve_entry_pullback_fraction(*, pullback_low: float, active_high: float, entry_price: float) -> float:
        span = active_high - pullback_low
        if span <= 0.0:
            return 1.0
        return max(min((entry_price - pullback_low) / span, 1.0), 0.0)

    def _resolve_stage1_quality_metrics(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        pump_start_5m_idx: int,
        start_idx: int,
        active_high_idx: int,
        active_high: float,
        leg_start: float,
        leg_size: float,
        params: PnoParams,
    ) -> dict[str, float | int] | None:
        if pump_start_5m_idx <= 0 or pump_start_5m_idx >= len(five.timestamps):
            return None
        active_high_timestamp = int(one.timestamps[active_high_idx])
        active_high_5m_idx = int(np.searchsorted(five.timestamps, active_high_timestamp, side="right") - 1)
        if active_high_5m_idx < pump_start_5m_idx:
            return None

        pump_pre_atr = float(five.atr_pre_14[pump_start_5m_idx])
        baseline_quote = float(five.pre_quote_median_24[pump_start_5m_idx])
        if not np.isfinite(pump_pre_atr) or pump_pre_atr <= 0.0:
            return None
        if not np.isfinite(baseline_quote) or baseline_quote <= 0.0:
            return None

        pump_highs = five.highs[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_tr = five.tr[pump_start_5m_idx : active_high_5m_idx + 1]
        if pump_highs.size == 0 or pump_tr.size == 0:
            return None

        low_before_pump = float(five.lows[pump_start_5m_idx - 1])
        pump_impulse = float(np.max(pump_highs)) - low_before_pump
        pump_impulse_atr_pre = self._safe_divide(pump_impulse, pump_pre_atr)
        pump_peak_bar_tr_atr_pre = self._safe_divide(float(np.max(pump_tr)), pump_pre_atr)
        if pump_impulse_atr_pre < float(params.stage1_min_impulse_atr_pre):
            return None
        if pump_peak_bar_tr_atr_pre < float(params.stage1_min_peak_bar_tr_atr_pre):
            return None

        start_window_end = min(five_idx, pump_start_5m_idx + 5)
        pump_start_quote = float(np.mean(five.quote_volume[pump_start_5m_idx : start_window_end + 1]))
        pump_continue_quote = float(np.mean(five.quote_volume[pump_start_5m_idx : five_idx + 1]))
        pump_volume_ratio_start = self._safe_divide(pump_start_quote, baseline_quote)
        pump_volume_ratio_continue = self._safe_divide(pump_continue_quote, baseline_quote)
        if pump_volume_ratio_start < float(params.stage1_min_volume_ratio_start):
            return None
        if pump_volume_ratio_continue < float(params.stage1_min_volume_ratio_continue):
            return None

        cumulative_quote_volume = self._range_sum(one.cumulative_quote_volume, start_idx, idx)
        if cumulative_quote_volume < float(params.stage1_min_cumulative_quote_volume):
            return None

        pre_pump_ema_crosses_1h = int(round(float(five.ema_cross_count_1h[pump_start_5m_idx])))
        if pre_pump_ema_crosses_1h < int(params.stage1_pre_pump_ema_crosses_min):
            return None

        pre_start_idx = max(0, pump_start_5m_idx - 12)
        pre_tr = five.tr[pre_start_idx:pump_start_5m_idx]
        pre_closes = five.closes[pre_start_idx:pump_start_5m_idx]
        if pre_tr.size == 0 or pre_closes.size == 0:
            return None
        median_pre_close = float(np.median(pre_closes))
        barcode_threshold = max(
            float(params.stage1_barcode_tr_atr_fraction) * pump_pre_atr,
            float(params.stage1_barcode_tr_price_fraction) * median_pre_close,
        )
        pre_pump_barcode_fraction_1h = float(np.mean(pre_tr <= barcode_threshold))
        if pre_pump_barcode_fraction_1h > float(params.stage1_barcode_max_fraction_1h):
            return None

        pre_pump_high_24h = float(five.pre_high_24h[pump_start_5m_idx])
        if not np.isfinite(pre_pump_high_24h):
            return None
        if pre_pump_high_24h > (active_high + self._EPSILON):
            return None

        pre_pump_high_1h = float(five.pre_high_1h[pump_start_5m_idx])
        if not np.isfinite(pre_pump_high_1h):
            return None
        half_leg_level = leg_start + (float(params.stage1_pre_pump_high_max_fraction_of_leg) * leg_size)
        if pre_pump_high_1h > (half_leg_level + self._EPSILON):
            return None

        return {
            "cumulative_quote_volume": cumulative_quote_volume,
            "pre_pump_ema_crosses_1h": pre_pump_ema_crosses_1h,
            "pre_pump_barcode_fraction_1h": pre_pump_barcode_fraction_1h,
            "pre_pump_high_24h": pre_pump_high_24h,
            "pre_pump_high_1h": pre_pump_high_1h,
            "pump_pre_atr": pump_pre_atr,
            "pump_impulse_atr_pre": pump_impulse_atr_pre,
            "pump_peak_bar_tr_atr_pre": pump_peak_bar_tr_atr_pre,
            "pump_volume_ratio_start": pump_volume_ratio_start,
            "pump_volume_ratio_continue": pump_volume_ratio_continue,
        }

    def _resolve_stage1_context(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        params: PnoParams,
    ) -> Stage1Context | None:
        pump_start_5m_idx = int(five.pump_start_idx[five_idx])
        if pump_start_5m_idx < 0 or pump_start_5m_idx >= len(five.timestamps):
            return None
        start_timestamp = int(five.timestamps[pump_start_5m_idx])
        start_idx = int(np.searchsorted(one.timestamps, start_timestamp, side="left"))
        if start_idx >= idx:
            return None
        current_slice = slice(start_idx, idx + 1)
        local_high_offset = int(np.argmax(one.highs[current_slice]))
        active_high_idx = start_idx + local_high_offset
        active_high = float(one.highs[active_high_idx])
        leg_start_idx, leg_start = self._resolve_leg_start(
            one=one,
            start_idx=start_idx,
            end_idx=active_high_idx,
            required_rebound=float(one.v1[idx]),
        )
        leg_size = active_high - leg_start
        min_leg = max(
            float(params.min_stage1_leg_v1 * one.v1[idx]),
            float(params.min_stage1_leg_v5_fraction * five.v5[five_idx]),
        )
        if leg_size < max(min_leg, self._EPSILON):
            return None
        hold_floor = max(
            float(five.ema20[five_idx]),
            active_high - (float(params.stage1_hold_fraction) * leg_size),
        )
        if float(one.closes[idx]) <= hold_floor:
            return None
        if float(one.lows[idx]) <= leg_start:
            return None
        quality_metrics = self._resolve_stage1_quality_metrics(
            one=one,
            five=five,
            idx=idx,
            five_idx=five_idx,
            pump_start_5m_idx=pump_start_5m_idx,
            start_idx=start_idx,
            active_high_idx=active_high_idx,
            active_high=active_high,
            leg_start=leg_start,
            leg_size=leg_size,
            params=params,
        )
        if quality_metrics is None:
            return None
        return Stage1Context(
            start_idx=start_idx,
            start_timestamp=int(one.timestamps[start_idx]),
            pump_start_5m_idx=pump_start_5m_idx,
            pump_start_timestamp=start_timestamp,
            current_5m_idx=five_idx,
            active_high_idx=active_high_idx,
            active_high_timestamp=int(one.timestamps[active_high_idx]),
            active_high=active_high,
            leg_start_idx=leg_start_idx,
            leg_start_timestamp=int(one.timestamps[leg_start_idx]),
            leg_start=leg_start,
            leg_size=leg_size,
            hold_floor=hold_floor,
            cumulative_quote_volume=float(quality_metrics["cumulative_quote_volume"]),
            pre_pump_ema_crosses_1h=int(quality_metrics["pre_pump_ema_crosses_1h"]),
            pre_pump_barcode_fraction_1h=float(quality_metrics["pre_pump_barcode_fraction_1h"]),
            pre_pump_high_24h=float(quality_metrics["pre_pump_high_24h"]),
            pre_pump_high_1h=float(quality_metrics["pre_pump_high_1h"]),
            pump_pre_atr=float(quality_metrics["pump_pre_atr"]),
            pump_impulse_atr_pre=float(quality_metrics["pump_impulse_atr_pre"]),
            pump_peak_bar_tr_atr_pre=float(quality_metrics["pump_peak_bar_tr_atr_pre"]),
            pump_volume_ratio_start=float(quality_metrics["pump_volume_ratio_start"]),
            pump_volume_ratio_continue=float(quality_metrics["pump_volume_ratio_continue"]),
        )

    def _resolve_stage2_context(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage1: Stage1Context,
        params: PnoParams,
    ) -> Stage2Context | None:
        if idx <= stage1.active_high_idx:
            return None
        start_idx = stage1.active_high_idx + 1
        red_indices = np.where(one.red[start_idx : idx + 1])[0]
        if red_indices.size == 0:
            return None
        red_after_high_idx = start_idx + int(red_indices[0])
        pullback_threshold = stage1.active_high - max(float(params.pullback_min_v1) * float(one.v1[idx]), self._EPSILON)
        pullback_reached = np.where(one.lows[start_idx : idx + 1] <= pullback_threshold)[0]
        if pullback_reached.size == 0:
            return None
        pullback_trigger_idx = start_idx + int(pullback_reached[0])
        post_high_lows = one.lows[start_idx : idx + 1]
        pullback_low_offset = int(np.argmin(post_high_lows))
        pullback_low_idx = start_idx + pullback_low_offset
        pullback_low = float(one.lows[pullback_low_idx])
        pullback_depth = stage1.active_high - pullback_low
        if pullback_depth < max(float(one.v1[idx]), self._EPSILON):
            return None
        return Stage2Context(
            active_high_idx=stage1.active_high_idx,
            active_high_timestamp=stage1.active_high_timestamp,
            active_high=stage1.active_high,
            red_after_high_idx=red_after_high_idx,
            pullback_start_idx=max(red_after_high_idx, pullback_trigger_idx),
            pullback_low_idx=pullback_low_idx,
            pullback_low_timestamp=int(one.timestamps[pullback_low_idx]),
            pullback_low=pullback_low,
            pullback_depth=pullback_depth,
            pullback_age_bars=(idx - stage1.active_high_idx),
        )

    def _resolve_stage3_context(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage1: Stage1Context,
        stage2: Stage2Context,
        params: PnoParams,
    ) -> tuple[Stage3Context | None, str | None]:
        post_high_slice = slice(stage1.active_high_idx + 1, idx + 1)
        if stage2.pullback_age_bars > params.pullback_max_age_bars:
            return None, "pullback_too_old"
        if stage2.pullback_depth > (params.pullback_invalid_max_leg_fraction * stage1.leg_size):
            return None, "pullback_too_deep_vs_leg"
        if stage2.pullback_depth > (params.pullback_invalid_max_v5 * five.v5[five_idx]):
            return None, "pullback_too_deep_vs_v5"
        if np.any(one.closes[post_high_slice] < (stage1.leg_start - self._EPSILON)):
            return None, "close_below_leg_start"
        if float(five.closes[five_idx]) <= float(five.ema20[five_idx]):
            return None, "close_below_ema20"
        valid = (
            stage2.pullback_depth >= (params.pullback_min_v1 * one.v1[idx])
            and stage2.pullback_depth <= (params.pullback_valid_max_leg_fraction * stage1.leg_size)
            and stage2.pullback_depth <= (params.pullback_valid_max_v5 * five.v5[five_idx])
            and stage2.pullback_low > stage1.leg_start
        )
        if not valid:
            return None, None
        return (
            Stage3Context(
                active_high_idx=stage2.active_high_idx,
                active_high_timestamp=stage2.active_high_timestamp,
                active_high=stage2.active_high,
                pullback_start_idx=stage2.pullback_start_idx,
                pullback_low_idx=stage2.pullback_low_idx,
                pullback_low_timestamp=stage2.pullback_low_timestamp,
                pullback_low=stage2.pullback_low,
                pullback_depth=stage2.pullback_depth,
                pullback_age_bars=stage2.pullback_age_bars,
                validation_timestamp=int(one.timestamps[idx]),
            ),
            None,
        )

    def _resolve_stage4_context(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage1: Stage1Context,
        stage3: Stage3Context,
        previous: Stage4Context | None,
        params: PnoParams,
        pno_index: int,
        retired_clusters: list[RetiredCluster],
    ) -> Stage4Context | None:
        del five
        del five_idx
        del stage1
        if idx <= stage3.pullback_start_idx:
            return None

        v1_now = float(one.v1[idx])
        if not np.isfinite(v1_now) or v1_now <= 0.0:
            return None

        confirmed_highs = self._resolve_confirmed_highs(
            one=one,
            start_idx=stage3.pullback_start_idx + 1,
            end_idx=idx,
        )
        confirmed_lows = self._resolve_confirmed_lows(
            one=one,
            start_idx=stage3.pullback_start_idx + 1,
            end_idx=idx,
        )
        tolerance = float(params.level_touch_tolerance_v1) * v1_now

        if previous is not None and previous.active_high_idx == stage3.active_high_idx:
            base_key = (previous.active_high_idx, previous.cluster_first_idx, previous.cluster_last_idx)
            if any(
                retired.active_high_idx == base_key[0]
                and retired.cluster_first_idx == base_key[1]
                and retired.cluster_last_idx == base_key[2]
                for retired in retired_clusters
            ):
                return None
            if len(previous.cluster_indices) < 1:
                return None

            touch_indices = tuple(
                high_idx
                for high_idx in confirmed_highs
                if high_idx >= previous.cluster_first_idx
                and float(one.highs[high_idx]) < (stage3.active_high - self._EPSILON)
                and abs(float(one.highs[high_idx]) - previous.level) <= tolerance
            )
            min_required_touches = max(1, len(previous.cluster_indices))
            if len(touch_indices) < min_required_touches:
                return None
            latest_touch_idx = int(max(touch_indices))
            if (idx - latest_touch_idx) > params.level_latest_high_max_age_bars:
                return None

            current_level_low = float(np.min(one.lows[previous.cluster_first_idx : idx + 1]))
            break_depth = max(previous.level_low - current_level_low, 0.0)
            level_low_minor_break = False
            level_low_major_break = False
            penalty_level_low_break = 0
            hard_block = bool(previous.hard_block)
            hard_block_reason = previous.hard_block_reason
            if break_depth > (float(params.level_low_major_break_v1) * v1_now):
                level_low_major_break = True
                hard_block = True
                hard_block_reason = "level_low_major_break"
            elif break_depth > (float(params.level_low_minor_break_v1) * v1_now):
                level_low_minor_break = True
                penalty_level_low_break = 6

            level_maturity_fraction = self._resolve_level_maturity_fraction(
                active_high_idx=stage3.active_high_idx,
                cluster_first_idx=previous.cluster_first_idx,
                current_idx=idx,
            )
            if level_maturity_fraction < float(params.level_min_maturity_fraction):
                return None

            return Stage4Context(
                active_high_idx=stage3.active_high_idx,
                active_high_timestamp=stage3.active_high_timestamp,
                active_high=stage3.active_high,
                pullback_low_idx=stage3.pullback_low_idx,
                pullback_low_timestamp=stage3.pullback_low_timestamp,
                pullback_low=stage3.pullback_low,
                pullback_depth=stage3.pullback_depth,
                cluster_indices=previous.cluster_indices,
                cluster_prices=previous.cluster_prices,
                level=previous.level,
                level_pos=self._safe_divide(
                    previous.level - stage3.pullback_low,
                    stage3.active_high - stage3.pullback_low,
                ),
                touches=len(touch_indices),
                cluster_first_idx=previous.cluster_first_idx,
                cluster_last_idx=previous.cluster_last_idx,
                level_valid_idx=previous.level_valid_idx,
                level_valid_timestamp=previous.level_valid_timestamp,
                level_low=previous.level_low,
                level_low_minor_break=level_low_minor_break,
                level_low_major_break=level_low_major_break,
                penalty_level_low_break=penalty_level_low_break,
                penalty_untested_highs=0,
                base_bonus=0,
                pno_index=previous.pno_index,
                maturity_penalty=0,
                pno_order_adj=0,
                score_a=0,
                score_b=0,
                score_c=0,
                score_d=0,
                score_e=0,
                score_tp2=0,
                final_score=0.0,
                entry_plan=0.0,
                sl_plan=0.0,
                low_last_red_plan=0.0,
                tp1=stage3.active_high,
                tp2=0.0,
                stage4_ready=not hard_block,
                hard_block=hard_block,
                is_valid_setup=False,
                hard_block_reason=hard_block_reason,
                entry_pos=0.0,
                level_maturity_fraction=level_maturity_fraction,
                level_age_bars=max(idx - previous.cluster_first_idx, 0),
            )

        cluster = self._resolve_level_cluster(
            one=one,
            idx=idx,
            stage3=stage3,
            confirmed_highs=confirmed_highs,
            confirmed_lows=confirmed_lows,
            params=params,
            retired_clusters=retired_clusters,
        )
        if cluster is None:
            return None
        cluster_indices, cluster_prices = cluster
        level = float(np.median(np.asarray(cluster_prices, dtype=np.float64)))
        if level >= (stage3.active_high - self._EPSILON):
            return None

        touch_indices = tuple(
            high_idx
            for high_idx in confirmed_highs
            if high_idx >= cluster_indices[0]
            and float(one.highs[high_idx]) < (stage3.active_high - self._EPSILON)
            and abs(float(one.highs[high_idx]) - level) <= tolerance
        )
        min_required_touches = max(1, len(cluster_indices))
        if len(touch_indices) < min_required_touches:
            return None
        latest_touch_idx = int(max(touch_indices))
        if (idx - latest_touch_idx) > params.level_latest_high_max_age_bars:
            return None

        level_maturity_fraction = self._resolve_level_maturity_fraction(
            active_high_idx=stage3.active_high_idx,
            cluster_first_idx=int(cluster_indices[0]),
            current_idx=idx,
        )
        if level_maturity_fraction < float(params.level_min_maturity_fraction):
            return None

        hard_block = len(touch_indices) > int(params.max_level_touches)
        hard_block_reason = "too_many_touches" if hard_block else None
        return Stage4Context(
            active_high_idx=stage3.active_high_idx,
            active_high_timestamp=stage3.active_high_timestamp,
            active_high=stage3.active_high,
            pullback_low_idx=stage3.pullback_low_idx,
            pullback_low_timestamp=stage3.pullback_low_timestamp,
            pullback_low=stage3.pullback_low,
            pullback_depth=stage3.pullback_depth,
            cluster_indices=cluster_indices,
            cluster_prices=cluster_prices,
            level=level,
            level_pos=self._safe_divide(level - stage3.pullback_low, stage3.active_high - stage3.pullback_low),
            touches=len(touch_indices),
            cluster_first_idx=int(cluster_indices[0]),
            cluster_last_idx=int(cluster_indices[-1]),
            level_valid_idx=idx,
            level_valid_timestamp=int(one.timestamps[idx]),
            level_low=float(np.min(one.lows[int(cluster_indices[0]) : idx + 1])),
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=max(int(pno_index), 1),
            maturity_penalty=0,
            pno_order_adj=0,
            score_a=0,
            score_b=0,
            score_c=0,
            score_d=0,
            score_e=0,
            score_tp2=0,
            final_score=0.0,
            entry_plan=0.0,
            sl_plan=0.0,
            low_last_red_plan=0.0,
            tp1=stage3.active_high,
            tp2=0.0,
            stage4_ready=not hard_block,
            hard_block=hard_block,
            is_valid_setup=False,
            hard_block_reason=hard_block_reason,
            entry_pos=0.0,
            level_maturity_fraction=level_maturity_fraction,
            level_age_bars=max(idx - int(cluster_indices[0]), 0),
        )

    def _rebuild_stage4_scores(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage1: Stage1Context,
        stage3: Stage3Context,
        stage4: Stage4Context,
        params: PnoParams,
    ) -> Stage4Context:
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        v5_now = max(float(five.v5[five_idx]), self._EPSILON)
        slip_plan = max(float(stage4.level) * float(params.min_tick_fraction), float(params.slip_plan_v1_fraction) * v1_now)
        entry_plan = float(stage4.level) + slip_plan
        entry_pos = self._resolve_entry_pullback_fraction(
            pullback_low=float(stage3.pullback_low),
            active_high=float(stage3.active_high),
            entry_price=entry_plan,
        )
        low_last_red_plan = self._resolve_low_last_red_plan(one=one, stage1=stage1, stage3=stage3, idx=idx)
        sl_plan = min(low_last_red_plan, stage3.pullback_low)
        tp1 = float(stage3.active_high)
        tp2 = self._resolve_tp2(active_high=tp1, v1=v1_now)

        activity_ratio = max(
            self._safe_divide(float(five.r3_quote[five_idx]), float(five.b24_quote[five_idx])),
            self._safe_divide(float(five.r3_trade[five_idx]), float(five.b24_trade[five_idx])),
            self._safe_divide(float(five.activity_last6_quote[five_idx]), float(five.activity_prev24_quote[five_idx])),
            self._safe_divide(float(five.activity_last6_trade[five_idx]), float(five.activity_prev24_trade[five_idx])),
        )
        if activity_ratio >= 2.0:
            score_a_activity = 10
        elif activity_ratio >= 1.75:
            score_a_activity = 8
        elif activity_ratio >= 1.5:
            score_a_activity = 6
        elif activity_ratio >= 1.25:
            score_a_activity = 4
        else:
            score_a_activity = 0

        if float(five.closes[five_idx]) > float(five.ema9[five_idx]) > float(five.ema20[five_idx]):
            score_a_hold = 10
        elif float(five.closes[five_idx]) > float(five.ema20[five_idx]) and float(five.ema9[five_idx]) > float(five.ema20[five_idx]):
            score_a_hold = 7
        elif float(five.closes[five_idx]) > float(five.ema20[five_idx]):
            score_a_hold = 4
        else:
            score_a_hold = 0
        score_a = score_a_activity + score_a_hold

        leg_v5 = self._safe_divide(stage1.leg_size, v5_now)
        if leg_v5 >= 4.0:
            score_b_leg = 8
        elif leg_v5 >= 3.0:
            score_b_leg = 6
        elif leg_v5 >= 2.0:
            score_b_leg = 4
        elif leg_v5 >= 1.0:
            score_b_leg = 2
        else:
            score_b_leg = 0
        depth_frac = self._safe_divide(stage3.pullback_depth, stage1.leg_size)
        if 0.12 <= depth_frac <= 0.30:
            score_b_depth = 10
        elif depth_frac <= float(params.pullback_valid_max_leg_fraction):
            score_b_depth = 8
        elif depth_frac <= float(params.pullback_invalid_max_leg_fraction):
            score_b_depth = 4
        else:
            score_b_depth = 0
        if stage3.pullback_age_bars <= 4:
            score_b_freshness = 7
        elif stage3.pullback_age_bars <= 6:
            score_b_freshness = 5
        elif stage3.pullback_age_bars <= 9:
            score_b_freshness = 3
        else:
            score_b_freshness = 1
        score_b = score_b_leg + score_b_depth + score_b_freshness

        confirmed_highs = self._resolve_confirmed_highs(one=one, start_idx=stage1.active_high_idx + 1, end_idx=idx)
        confirmed_lows = self._resolve_confirmed_lows(one=one, start_idx=stage1.active_high_idx + 1, end_idx=idx)
        if len(confirmed_highs) >= 2 and len(confirmed_lows) >= 2:
            zigzag_score = 8
        elif len(confirmed_highs) >= 2 and len(confirmed_lows) >= 1:
            zigzag_score = 6
        elif len(confirmed_highs) >= 1:
            zigzag_score = 4
        else:
            zigzag_score = 0
        base_bonus = self._resolve_base_bonus(one=one, idx=idx, stage1=stage1, stage3=stage3)
        if entry_pos <= 0.50:
            level_pos_score = 7
        elif entry_pos <= 0.60:
            level_pos_score = 4
        else:
            level_pos_score = 0
        score_c = min(20, zigzag_score + base_bonus + level_pos_score)

        cluster_spread = max(stage4.cluster_prices) - min(stage4.cluster_prices)
        if cluster_spread <= (float(params.level_cluster_spread_v1) * v1_now):
            if len(stage4.cluster_indices) >= 3:
                cluster_clarity_score = 8
            elif len(stage4.cluster_indices) == 2:
                cluster_clarity_score = 7
            else:
                cluster_clarity_score = 4
        elif cluster_spread <= (float(params.level_cluster_relaxed_spread_v1) * v1_now):
            cluster_clarity_score = 5 if len(stage4.cluster_indices) >= 2 else 3
        else:
            cluster_clarity_score = 0
        compression_score = self._resolve_compression_score(one=one, stage4=stage4, confirmed_lows=confirmed_lows)
        if stage4.touches <= 1:
            touches_score = 1
        elif stage4.touches <= 3:
            touches_score = 4
        elif stage4.touches == 4:
            touches_score = 2
        else:
            touches_score = 0
        score_d = min(20, cluster_clarity_score + compression_score + touches_score)

        dstop_plan = entry_plan - sl_plan
        net_tp1_move = tp1 - entry_plan - (float(params.fee_rate) * entry_plan) - (0.5 * float(params.fee_rate) * tp1)
        if net_tp1_move >= (1.2 * dstop_plan):
            score_e = 10
        elif net_tp1_move >= (1.0 * dstop_plan):
            score_e = 7
        elif net_tp1_move >= (0.8 * dstop_plan):
            score_e = 4
        else:
            score_e = 0

        score_tp2 = self._resolve_tp2_score(tp2=tp2, active_high=tp1, v1=v1_now)
        penalty_untested_highs = self._resolve_untested_high_penalty(one=one, idx=idx, stage3=stage3, stage4=stage4)
        pno_order_adj = self._resolve_pno_order_adj(stage4.pno_index)
        maturity_penalty = self._resolve_maturity_penalty(stage1=stage1, pno_index=stage4.pno_index)
        final_score = float(
            score_a
            + score_b
            + score_c
            + score_d
            + score_e
            + score_tp2
            + pno_order_adj
            + penalty_untested_highs
            - stage4.penalty_level_low_break
            - maturity_penalty
        )

        hard_block_reason = stage4.hard_block_reason
        hard_block = bool(stage4.hard_block)
        if stage4.level >= stage3.active_high:
            hard_block = True
            hard_block_reason = "level_not_below_active_high"
        elif stage4.touches > int(params.max_level_touches):
            hard_block = True
            hard_block_reason = "too_many_touches"
        elif float(five.closes[five_idx]) <= float(five.ema20[five_idx]):
            hard_block = True
            hard_block_reason = "close_below_ema20"
        elif stage3.pullback_low <= (stage1.leg_start + self._EPSILON):
            hard_block = True
            hard_block_reason = "pullback_below_leg_start"
        elif stage3.pullback_depth > (float(params.pullback_invalid_max_leg_fraction) * stage1.leg_size):
            hard_block = True
            hard_block_reason = "pullback_too_deep_vs_leg"
        elif stage3.pullback_depth > (float(params.pullback_invalid_max_v5) * v5_now):
            hard_block = True
            hard_block_reason = "pullback_too_deep_vs_v5"
        elif entry_pos > float(params.max_entry_pullback_fraction):
            hard_block = True
            hard_block_reason = "entry_above_pullback_half"
        elif stage4.level_maturity_fraction < float(params.level_min_maturity_fraction):
            hard_block = True
            hard_block_reason = "level_not_mature_enough"
        elif dstop_plan <= self._EPSILON:
            hard_block = True
            hard_block_reason = "non_positive_stop_distance"
        elif net_tp1_move <= 0.0:
            hard_block = True
            hard_block_reason = "non_positive_tp1_after_fee"

        stage4_ready = not hard_block
        is_valid_setup = stage4_ready and final_score >= float(params.min_score)
        return replace(
            stage4,
            penalty_untested_highs=penalty_untested_highs,
            base_bonus=base_bonus,
            maturity_penalty=maturity_penalty,
            pno_order_adj=pno_order_adj,
            score_a=score_a,
            score_b=score_b,
            score_c=score_c,
            score_d=score_d,
            score_e=score_e,
            score_tp2=score_tp2,
            final_score=final_score,
            entry_plan=entry_plan,
            sl_plan=sl_plan,
            low_last_red_plan=low_last_red_plan,
            tp1=tp1,
            tp2=tp2,
            stage4_ready=stage4_ready,
            hard_block=hard_block,
            is_valid_setup=is_valid_setup,
            hard_block_reason=hard_block_reason,
            entry_pos=entry_pos,
        )

    def _resolve_leg_start(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
        required_rebound: float,
    ) -> tuple[int, float]:
        confirmed_lows = self._resolve_confirmed_lows(one=one, start_idx=start_idx, end_idx=end_idx)
        active_high = float(one.highs[end_idx])
        for low_idx in reversed(confirmed_lows):
            low_price = float(one.lows[low_idx])
            if (active_high - low_price) < max(required_rebound, self._EPSILON):
                continue
            if float(np.min(one.lows[low_idx : end_idx + 1])) < (low_price - self._EPSILON):
                continue
            return int(low_idx), low_price

        local_slice = one.lows[start_idx : end_idx + 1]
        local_idx = int(np.argmin(local_slice))
        resolved_idx = start_idx + local_idx
        return int(resolved_idx), float(one.lows[resolved_idx])

    def _resolve_level_cluster(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        confirmed_highs: list[int],
        confirmed_lows: list[int],
        params: PnoParams,
        retired_clusters: list[RetiredCluster],
    ) -> tuple[tuple[int, ...], tuple[float, ...]] | None:
        if len(confirmed_highs) < 1:
            return None

        v1_now = max(float(one.v1[idx]), self._EPSILON)
        max_spread = float(params.level_cluster_relaxed_spread_v1) * v1_now
        for end_pos in range(len(confirmed_highs) - 1, -1, -1):
            latest_idx = int(confirmed_highs[end_pos])
            if (idx - latest_idx) > params.level_latest_high_max_age_bars:
                continue
            for cluster_size in (3, 2, 1):
                if (end_pos + 1) < cluster_size:
                    continue
                indices = tuple(int(item) for item in confirmed_highs[end_pos - cluster_size + 1 : end_pos + 1])
                if indices[0] <= stage3.pullback_start_idx:
                    continue
                prices = tuple(float(one.highs[item]) for item in indices)
                if any(price >= (stage3.active_high - self._EPSILON) for price in prices):
                    continue
                if cluster_size == 1 and not self._is_single_touch_level_candidate(
                    one=one,
                    idx=idx,
                    high_idx=indices[0],
                    confirmed_lows=confirmed_lows,
                ):
                    continue
                spread = max(prices) - min(prices)
                if spread > max_spread:
                    continue
                base_key = (stage3.active_high_idx, int(indices[0]), int(indices[-1]))
                if any(
                    retired.active_high_idx == base_key[0]
                    and retired.cluster_first_idx == base_key[1]
                    and retired.cluster_last_idx == base_key[2]
                    for retired in retired_clusters
                ):
                    continue
                candidate_level = float(np.median(np.asarray(prices, dtype=np.float64)))
                if not self._is_cluster_rearm_allowed(
                    active_high_idx=stage3.active_high_idx,
                    candidate_level=candidate_level,
                    stage3=stage3,
                    retired_clusters=retired_clusters,
                    v1_now=v1_now,
                    rearm_min_distance_v1=float(params.level_rearm_min_distance_v1),
                ):
                    continue
                return indices, prices
        return None

    def _is_single_touch_level_candidate(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        high_idx: int,
        confirmed_lows: list[int],
    ) -> bool:
        if high_idx >= idx:
            return False
        return any(low_idx > high_idx and low_idx <= idx for low_idx in confirmed_lows)

    def _is_cluster_rearm_allowed(
        self,
        *,
        active_high_idx: int,
        candidate_level: float,
        stage3: Stage3Context,
        retired_clusters: list[RetiredCluster],
        v1_now: float,
        rearm_min_distance_v1: float = 0.50,
    ) -> bool:
        for retired in retired_clusters:
            if retired.active_high_idx != active_high_idx:
                continue
            if abs(candidate_level - retired.level) >= max(float(v1_now) * rearm_min_distance_v1, self._EPSILON):
                continue
            if (
                stage3.pullback_low_idx > retired.pullback_low_idx
                and stage3.pullback_low < (retired.pullback_low - self._EPSILON)
            ):
                continue
            return False
        return True

    def _resolve_low_last_red_plan(
        self,
        *,
        one: OneMinuteFrame,
        stage1: Stage1Context,
        stage3: Stage3Context,
        idx: int,
    ) -> float:
        search_slice = one.red[stage1.active_high_idx + 1 : idx + 1]
        red_indices = np.where(search_slice)[0]
        if red_indices.size == 0:
            return float(stage3.pullback_low)
        last_red_idx = stage1.active_high_idx + 1 + int(red_indices[-1])
        return float(one.lows[last_red_idx])

    def _resolve_compression_score(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        confirmed_lows: list[int],
    ) -> int:
        if len(stage4.cluster_indices) < 2:
            return 0

        distances: list[float] = []
        for high_idx in stage4.cluster_indices[:2]:
            next_low_idx = next((low_idx for low_idx in confirmed_lows if low_idx > high_idx), None)
            if next_low_idx is None:
                local_slice = one.lows[high_idx + 1 :]
                if local_slice.size == 0:
                    return 0
                next_low_idx = high_idx + 1 + int(np.argmin(local_slice))
            distance = float(one.highs[high_idx]) - float(one.lows[next_low_idx])
            if distance <= 0.0:
                return 0
            distances.append(distance)

        if len(distances) < 2:
            return 0
        a1, a2 = distances[0], distances[1]
        if a2 <= (0.8 * a1):
            return 8
        if a2 < a1:
            return 6
        if abs(a2 - a1) <= (0.1 * a1):
            return 4
        if a2 <= (1.1 * a1):
            return 4
        return 0

    def _resolve_base_bonus(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage1: Stage1Context,
        stage3: Stage3Context,
    ) -> int:
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        best_bonus = 0
        for block_end in range(stage1.active_high_idx - 1, stage1.start_idx + 1, -1):
            for block_size in range(3, 13):
                block_start = block_end - block_size + 1
                if block_start < stage1.start_idx:
                    continue
                block_high = float(np.max(one.highs[block_start : block_end + 1]))
                block_low = float(np.min(one.lows[block_start : block_end + 1]))
                if (block_high - block_low) > (1.5 * v1_now):
                    continue
                if (stage1.active_high - block_high) < (2.0 * v1_now):
                    continue
                if block_low <= stage3.pullback_low <= block_high:
                    return 5
                distance_to_block = min(abs(stage3.pullback_low - block_low), abs(stage3.pullback_low - block_high))
                if distance_to_block <= (0.5 * v1_now):
                    return 5
                if distance_to_block <= (1.0 * v1_now):
                    best_bonus = max(best_bonus, 2)
        return best_bonus

    def _resolve_untested_high_penalty(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        stage4: Stage4Context,
    ) -> int:
        confirmed_highs = self._resolve_confirmed_highs(
            one=one,
            start_idx=stage3.pullback_start_idx + 1,
            end_idx=idx,
        )
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        tolerance = 0.35 * v1_now
        penalty = 0
        cluster_idx_set = set(stage4.cluster_indices)
        for high_idx in confirmed_highs:
            if high_idx in cluster_idx_set:
                continue
            high_price = float(one.highs[high_idx])
            if high_price <= (stage4.level + self._EPSILON) or high_price >= (stage3.active_high - self._EPSILON):
                continue
            if high_idx + 1 > idx:
                continue
            decline = high_price - float(np.min(one.lows[high_idx + 1 : idx + 1]))
            if decline < (1.5 * v1_now):
                continue
            retested = bool(np.any(np.abs(one.highs[high_idx + 1 : idx + 1] - high_price) <= tolerance))
            if retested:
                continue
            distance_above_entry = max(high_price - stage4.entry_plan, 0.0)
            if distance_above_entry <= (1.5 * v1_now) and decline >= (2.0 * v1_now):
                penalty -= 8
            elif distance_above_entry <= (3.0 * v1_now) or decline < (2.0 * v1_now):
                penalty -= 5
            else:
                penalty -= 3
            if penalty <= -20:
                return -20
        return max(penalty, -20)

    @staticmethod
    def _resolve_maturity_penalty(*, stage1: Stage1Context, pno_index: int) -> int:
        age_5m_bars = max(stage1.current_5m_idx - stage1.pump_start_5m_idx + 1, 1)
        if age_5m_bars <= 6:
            time_penalty = 0
        elif age_5m_bars <= 12:
            time_penalty = 4
        elif age_5m_bars <= 18:
            time_penalty = 8
        else:
            time_penalty = 12

        if pno_index <= 2:
            index_penalty = 0
        elif pno_index == 3:
            index_penalty = 4
        elif pno_index == 4:
            index_penalty = 8
        else:
            index_penalty = 12
        return max(time_penalty, index_penalty)

    @staticmethod
    def _resolve_pno_order_adj(pno_index: int) -> int:
        if pno_index <= 1:
            return 8
        if pno_index == 2:
            return 4
        if pno_index == 3:
            return 0
        if pno_index == 4:
            return -6
        return -10

    def _resolve_tp2(self, *, active_high: float, v1: float) -> float:
        target_step = max(4.0 * v1, 0.0005 * active_high)
        round_step = self._round_to_preferred_step(target_step)
        multiples = np.floor(active_high / max(round_step, self._EPSILON)) + 1.0
        tp2 = multiples * round_step
        while tp2 <= (active_high + self._EPSILON):
            tp2 += round_step
        return float(tp2)

    @staticmethod
    def _resolve_tp2_score(*, tp2: float, active_high: float, v1: float) -> int:
        d2 = tp2 - active_high
        if (1.5 * v1) <= d2 <= (6.0 * v1):
            return 5
        if (1.0 * v1) <= d2 < (1.5 * v1):
            return 3
        if (6.0 * v1) < d2 <= (8.0 * v1):
            return 3
        return 0

    def _try_enter_and_simulate(
        self,
        *,
        one: OneMinuteFrame,
        params: PnoParams,
        armed: ArmedContext,
    ) -> tuple[TradeResult | None, int]:
        entry_idx = armed.entry_idx
        if entry_idx >= len(one.timestamps):
            return None, len(one.timestamps) - 1

        open_price = float(one.opens[entry_idx])
        high_price = float(one.highs[entry_idx])
        low_price = float(one.lows[entry_idx])
        close_price = float(one.closes[entry_idx])
        confirmation_mode = str(getattr(params, "entry_confirmation_mode", "cross"))
        actual_entry_idx = entry_idx
        signal_kind = "cross"
        if confirmation_mode == "cross":
            if open_price >= armed.stage4.level or high_price < armed.stage4.level:
                return None, entry_idx
            entry_price = min(high_price, armed.stage4.entry_plan)
            stop_loss = min(low_price, armed.stage4.low_last_red_plan)
        elif confirmation_mode == "close_above":
            if high_price < armed.stage4.level or close_price <= armed.stage4.level:
                return None, entry_idx
            actual_entry_idx = entry_idx + 1
            if actual_entry_idx >= len(one.timestamps):
                return None, entry_idx
            entry_price = float(one.opens[actual_entry_idx])
            stop_loss = min(low_price, armed.stage4.low_last_red_plan)
            signal_kind = "close_above"
        else:
            return None, entry_idx

        position_size = self._resolve_position_size(params=params, entry_price=entry_price, stop_loss=stop_loss)
        if position_size <= 0.0:
            return None, entry_idx

        pump_to_peak_bars = max(armed.stage1.active_high_idx - armed.stage1.start_idx, 1)
        metadata: dict[str, int | float | str | bool | None] = {
            "strategy_id": "pno",
            "symbol": params.symbol,
            "stage_path": PNO_STAGE_PATH,
            "pump_start_timestamp_ms": int(armed.stage1.pump_start_timestamp),
            "active_high_timestamp_ms": int(armed.stage1.active_high_timestamp),
            "active_high": round(float(armed.stage1.active_high), 8),
            "leg_start": round(float(armed.stage1.leg_start), 8),
            "leg_size": round(float(armed.stage1.leg_size), 8),
            "pump_cumulative_quote_volume": round(float(armed.stage1.cumulative_quote_volume), 2),
            "pre_pump_ema_crosses_1h": int(armed.stage1.pre_pump_ema_crosses_1h),
            "pre_pump_barcode_fraction_1h": round(float(armed.stage1.pre_pump_barcode_fraction_1h), 4),
            "pre_pump_high_24h": round(float(armed.stage1.pre_pump_high_24h), 8),
            "pre_pump_high_1h": round(float(armed.stage1.pre_pump_high_1h), 8),
            "pump_pre_atr": round(float(armed.stage1.pump_pre_atr), 8),
            "pump_impulse_atr_pre": round(float(armed.stage1.pump_impulse_atr_pre), 4),
            "pump_peak_bar_tr_atr_pre": round(float(armed.stage1.pump_peak_bar_tr_atr_pre), 4),
            "pump_volume_ratio_start": round(float(armed.stage1.pump_volume_ratio_start), 4),
            "pump_volume_ratio_continue": round(float(armed.stage1.pump_volume_ratio_continue), 4),
            "pullback_low": round(float(armed.stage3.pullback_low), 8),
            "pullback_depth": round(float(armed.stage3.pullback_depth), 8),
            "level": round(float(armed.stage4.level), 8),
            "level_first_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_first_idx]),
            "level_last_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_last_idx]),
            "level_valid_timestamp_ms": int(armed.stage4.level_valid_timestamp),
            "level_maturity_fraction": round(float(armed.stage4.level_maturity_fraction), 4),
            "level_age_bars": int(armed.stage4.level_age_bars),
            "entry_pos": round(float(armed.stage4.entry_pos), 4),
            "touches": int(armed.stage4.touches),
            "pno_index": int(armed.stage4.pno_index),
            "entry_confirmation_mode": confirmation_mode,
            "entry_signal_kind": signal_kind,
            "entry_signal_timestamp_ms": int(one.timestamps[entry_idx]),
            "entry_plan": round(float(armed.stage4.entry_plan), 8),
            "sl_plan": round(float(armed.stage4.sl_plan), 8),
            "tp1": round(float(armed.stage4.tp1), 8),
            "tp2": round(float(armed.stage4.tp2), 8),
            "score_a": int(armed.stage4.score_a),
            "score_b": int(armed.stage4.score_b),
            "score_c": int(armed.stage4.score_c),
            "score_d": int(armed.stage4.score_d),
            "score_e": int(armed.stage4.score_e),
            "score_tp2": int(armed.stage4.score_tp2),
            "penalty_untested_highs": int(armed.stage4.penalty_untested_highs),
            "penalty_level_low_break": int(armed.stage4.penalty_level_low_break),
            "maturity_penalty": int(armed.stage4.maturity_penalty),
            "final_score": round(float(armed.stage4.final_score), 4),
            "low_last_red_plan": round(float(armed.stage4.low_last_red_plan), 8),
            "entry_price_planned_slip": round(float(armed.stage4.entry_plan - armed.stage4.level), 8),
            "pump_to_peak_bars": int(pump_to_peak_bars),
            "pump_to_peak_minutes": float(pump_to_peak_bars),
            "entry_price_actual": round(float(entry_price), 8),
            "sl_actual": round(float(stop_loss), 8),
        }

        if confirmation_mode == "cross" and low_price <= armed.stage4.low_last_red_plan:
            pnl = self._net_leg_pnl(
                entry_price=entry_price,
                exit_price=stop_loss,
                quantity=position_size,
                fee_rate=float(params.fee_rate),
            )
            trade = TradeResult(
                entry_price=Price(entry_price),
                exit_price=Price(stop_loss),
                entry_timestamp_ms=int(one.timestamps[entry_idx]),
                exit_timestamp_ms=int(one.timestamps[entry_idx]),
                result_type=TradeResultType.SL,
                pnl=pnl,
                pnl_percent=Percentage(self._to_percent(pnl, entry_price * position_size)),
                breakout_timestamp_ms=int(one.timestamps[entry_idx]),
                retest_timestamp_ms=None,
                pump_to_peak_bars=int(pump_to_peak_bars),
                pump_to_peak_minutes=float(pump_to_peak_bars),
                metadata={**metadata, "category": "sl_entry"},
            )
            return trade, entry_idx

        return self._simulate_trade_path(
            one=one,
            params=params,
            armed=armed,
            entry_idx=actual_entry_idx,
            entry_price=entry_price,
            stop_loss=stop_loss,
            position_size=position_size,
            metadata=metadata,
        )

    def _simulate_trade_path(
        self,
        *,
        one: OneMinuteFrame,
        params: PnoParams,
        armed: ArmedContext,
        entry_idx: int,
        entry_price: float,
        stop_loss: float,
        position_size: float,
        metadata: dict[str, int | float | str | bool | None],
    ) -> tuple[TradeResult | None, int]:
        tp1 = float(armed.stage4.tp1)
        tp2 = float(armed.stage4.tp2)
        fee_rate = float(params.fee_rate)
        be_fee = entry_price * (1.0 + fee_rate) / max(1.0 - fee_rate, self._EPSILON)
        tp1_share = 0.5
        remainder_share = 0.5
        tp1_hit = False
        tp1_hit_idx: int | None = None
        exit_idx = len(one.timestamps) - 1
        exit_price = float(one.closes[exit_idx])
        result_type = TradeResultType.BE
        realized_pnl = 0.0
        category = "incomplete"
        max_favorable = 0.0
        max_adverse = 0.0

        for idx in range(entry_idx + 1, len(one.timestamps)):
            low = float(one.lows[idx])
            high = float(one.highs[idx])
            close = float(one.closes[idx])
            max_favorable = max(max_favorable, high - entry_price)
            max_adverse = max(max_adverse, entry_price - low)

            if not tp1_hit:
                if low <= stop_loss:
                    exit_price = stop_loss
                    exit_idx = idx
                    result_type = TradeResultType.SL
                    realized_pnl = self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=stop_loss,
                        quantity=position_size,
                        fee_rate=fee_rate,
                    )
                    category = "sl_non_entry"
                    break
                if high >= tp1:
                    tp1_hit = True
                    tp1_hit_idx = idx
                    realized_pnl += self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=tp1,
                        quantity=position_size * tp1_share,
                        fee_rate=fee_rate,
                    )
                    if high >= tp2:
                        exit_price = tp2
                        exit_idx = idx
                        result_type = TradeResultType.TP2
                        realized_pnl += self._net_leg_pnl(
                            entry_price=entry_price,
                            exit_price=tp2,
                            quantity=position_size * remainder_share,
                            fee_rate=fee_rate,
                        )
                        category = "tp2"
                        break
                    continue
            elif tp1_hit_idx is not None and idx > tp1_hit_idx:
                if low <= be_fee:
                    exit_price = be_fee
                    exit_idx = idx
                    result_type = TradeResultType.TP1_BE
                    realized_pnl += self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=be_fee,
                        quantity=position_size * remainder_share,
                        fee_rate=fee_rate,
                    )
                    category = "tp1_be"
                    break
                if high >= tp2:
                    exit_price = tp2
                    exit_idx = idx
                    result_type = TradeResultType.TP2
                    realized_pnl += self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=tp2,
                        quantity=position_size * remainder_share,
                        fee_rate=fee_rate,
                    )
                    category = "tp2"
                    break

            if idx == (len(one.timestamps) - 1):
                exit_idx = idx
                exit_price = close

        if result_type not in {TradeResultType.SL, TradeResultType.TP1_BE, TradeResultType.TP2}:
            return None, entry_idx

        trade_metadata = dict(metadata)
        trade_metadata.update(
            {
                "category": category,
                "be_fee": round(float(be_fee), 8),
                "holding_bars": int(exit_idx - entry_idx),
                "mfe_r": round(self._safe_divide(max_favorable, entry_price - stop_loss), 6),
                "mae_r": round(self._safe_divide(max_adverse, entry_price - stop_loss), 6),
                "mfe_pct": round(self._to_percent(max_favorable, entry_price), 6),
                "mae_pct": round(self._to_percent(max_adverse, entry_price), 6),
                "tp1_hit": tp1_hit,
                "tp2_hit": result_type == TradeResultType.TP2,
                "trade_complete": True,
            }
        )
        trade = TradeResult(
            entry_price=Price(entry_price),
            exit_price=Price(exit_price),
            entry_timestamp_ms=int(one.timestamps[entry_idx]),
            exit_timestamp_ms=int(one.timestamps[exit_idx]),
            result_type=result_type,
            pnl=realized_pnl,
            pnl_percent=Percentage(self._to_percent(realized_pnl, entry_price * position_size)),
            breakout_timestamp_ms=int(one.timestamps[entry_idx]),
            retest_timestamp_ms=None,
            pump_to_peak_bars=int(metadata["pump_to_peak_bars"]),
            pump_to_peak_minutes=float(metadata["pump_to_peak_minutes"]),
            metadata=trade_metadata,
        )
        return trade, exit_idx

    def _resolve_position_size(
        self,
        *,
        params: PnoParams,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        trade_risk = params.pno_r_trade if params.pno_r_trade is not None else (params.pno_deposit * params.pno_risk_pct)
        stop_distance = entry_price - stop_loss
        if trade_risk <= 0.0 or stop_distance <= self._EPSILON:
            return 0.0
        return float(trade_risk) / float(stop_distance)

    def _build_confirmed_high_map(
        self,
        *,
        highs: np.ndarray,
        lows: np.ndarray,
        v1: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        local_high_candidates = self._resolve_local_high_candidates(highs)
        if local_high_candidates.size == 0:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

        next_greater_high = self._resolve_next_strictly_greater_indices(highs)
        low_tree = self._build_range_tree(lows, is_min_tree=True)
        confirmed_indices: list[int] = []
        confirmed_at: list[int] = []
        for candidate_idx in local_high_candidates:
            if not np.isfinite(v1[candidate_idx]) or float(v1[candidate_idx]) <= 0.0:
                continue
            threshold = float(highs[candidate_idx]) - max(float(v1[candidate_idx]), self._EPSILON)
            right_bound = int(next_greater_high[candidate_idx]) - 1
            if right_bound <= candidate_idx:
                continue
            confirmation_idx = self._range_tree_first_leq(
                low_tree,
                left=int(candidate_idx) + 1,
                right=right_bound,
                threshold=threshold,
            )
            if confirmation_idx < 0:
                continue
            confirmed_indices.append(int(candidate_idx))
            confirmed_at.append(int(confirmation_idx))
        return (
            np.asarray(confirmed_indices, dtype=np.int64),
            np.asarray(confirmed_at, dtype=np.int64),
        )

    def _build_confirmed_low_map(
        self,
        *,
        highs: np.ndarray,
        lows: np.ndarray,
        v1: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        local_low_candidates = self._resolve_local_low_candidates(lows)
        if local_low_candidates.size == 0:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

        next_lower_low = self._resolve_next_strictly_lower_indices(lows)
        high_tree = self._build_range_tree(highs, is_min_tree=False)
        confirmed_indices: list[int] = []
        confirmed_at: list[int] = []
        for candidate_idx in local_low_candidates:
            if not np.isfinite(v1[candidate_idx]) or float(v1[candidate_idx]) <= 0.0:
                continue
            threshold = float(lows[candidate_idx]) + max(float(v1[candidate_idx]), self._EPSILON)
            right_bound = int(next_lower_low[candidate_idx]) - 1
            if right_bound <= candidate_idx:
                continue
            confirmation_idx = self._range_tree_first_geq(
                high_tree,
                left=int(candidate_idx) + 1,
                right=right_bound,
                threshold=threshold,
            )
            if confirmation_idx < 0:
                continue
            confirmed_indices.append(int(candidate_idx))
            confirmed_at.append(int(confirmation_idx))
        return (
            np.asarray(confirmed_indices, dtype=np.int64),
            np.asarray(confirmed_at, dtype=np.int64),
        )

    @staticmethod
    def _resolve_local_high_candidates(highs: np.ndarray) -> np.ndarray:
        if highs.size < 3:
            return np.empty(0, dtype=np.int64)
        mask = (highs[1:-1] >= highs[:-2]) & (highs[1:-1] >= highs[2:])
        return np.nonzero(mask)[0].astype(np.int64) + 1

    @staticmethod
    def _resolve_local_low_candidates(lows: np.ndarray) -> np.ndarray:
        if lows.size < 3:
            return np.empty(0, dtype=np.int64)
        mask = (lows[1:-1] <= lows[:-2]) & (lows[1:-1] <= lows[2:])
        return np.nonzero(mask)[0].astype(np.int64) + 1

    @staticmethod
    def _resolve_next_strictly_greater_indices(values: np.ndarray) -> np.ndarray:
        next_indices = np.full(values.shape[0], values.shape[0], dtype=np.int64)
        stack: list[int] = []
        for idx in range(values.shape[0] - 1, -1, -1):
            current_value = float(values[idx])
            while stack and float(values[stack[-1]]) <= current_value:
                stack.pop()
            if stack:
                next_indices[idx] = stack[-1]
            stack.append(idx)
        return next_indices

    @staticmethod
    def _resolve_next_strictly_lower_indices(values: np.ndarray) -> np.ndarray:
        next_indices = np.full(values.shape[0], values.shape[0], dtype=np.int64)
        stack: list[int] = []
        for idx in range(values.shape[0] - 1, -1, -1):
            current_value = float(values[idx])
            while stack and float(values[stack[-1]]) >= current_value:
                stack.pop()
            if stack:
                next_indices[idx] = stack[-1]
            stack.append(idx)
        return next_indices

    @staticmethod
    def _build_range_tree(values: np.ndarray, *, is_min_tree: bool) -> RangeSearchTree:
        values_count = int(values.shape[0])
        size = 1
        while size < values_count:
            size <<= 1
        fill_value = np.inf if is_min_tree else -np.inf
        tree = np.full(size * 2, fill_value, dtype=np.float64)
        tree[size : size + values_count] = values.astype(np.float64, copy=False)
        for idx in range(size - 1, 0, -1):
            if is_min_tree:
                tree[idx] = min(tree[idx * 2], tree[idx * 2 + 1])
            else:
                tree[idx] = max(tree[idx * 2], tree[idx * 2 + 1])
        return RangeSearchTree(size=size, tree=tree, is_min_tree=is_min_tree)

    def _range_tree_first_leq(
        self,
        tree: RangeSearchTree,
        *,
        left: int,
        right: int,
        threshold: float,
    ) -> int:
        if left > right:
            return -1
        return self._range_tree_first_match(
            tree=tree,
            node=1,
            node_left=0,
            node_right=tree.size - 1,
            query_left=left,
            query_right=right,
            threshold=threshold,
        )

    def _range_tree_first_geq(
        self,
        tree: RangeSearchTree,
        *,
        left: int,
        right: int,
        threshold: float,
    ) -> int:
        if left > right:
            return -1
        return self._range_tree_first_match(
            tree=tree,
            node=1,
            node_left=0,
            node_right=tree.size - 1,
            query_left=left,
            query_right=right,
            threshold=threshold,
        )

    def _range_tree_first_match(
        self,
        *,
        tree: RangeSearchTree,
        node: int,
        node_left: int,
        node_right: int,
        query_left: int,
        query_right: int,
        threshold: float,
    ) -> int:
        if query_right < node_left or node_right < query_left:
            return -1
        node_value = float(tree.tree[node])
        if tree.is_min_tree:
            if node_value > threshold:
                return -1
        elif node_value < threshold:
            return -1
        if node_left == node_right:
            return node_left
        mid = (node_left + node_right) // 2
        left_result = self._range_tree_first_match(
            tree=tree,
            node=node * 2,
            node_left=node_left,
            node_right=mid,
            query_left=query_left,
            query_right=query_right,
            threshold=threshold,
        )
        if left_result >= 0:
            return left_result
        return self._range_tree_first_match(
            tree=tree,
            node=node * 2 + 1,
            node_left=mid + 1,
            node_right=node_right,
            query_left=query_left,
            query_right=query_right,
            threshold=threshold,
        )

    @staticmethod
    def _mark_stage(
        diagnostics: GenerationDiagnostics,
        stage_keys: dict[str, tuple[object, ...]],
        stage_id: str,
        *,
        key: tuple[object, ...],
        timestamp_ms: int,
        extra: dict[str, object] | None = None,
    ) -> None:
        if stage_keys.get(stage_id) == key:
            return
        stage_keys[stage_id] = key
        stage_hits = diagnostics.setdefault("stage_hits", {stage: 0 for stage in PNO_STAGE_SEQUENCE})
        if isinstance(stage_hits, dict):
            stage_hits[stage_id] = int(stage_hits.get(stage_id, 0)) + 1
        stage_events = diagnostics.get("stage_events")
        if not isinstance(stage_events, list):
            return
        payload: dict[str, object] = {
            "stage_id": stage_id,
            "timestamp_ms": int(timestamp_ms),
        }
        if extra:
            payload.update(extra)
        stage_events.append(payload)

    @staticmethod
    def _mark_stage_rejection(
        diagnostics: GenerationDiagnostics,
        rejection_keys: dict[str, tuple[object, ...]],
        stage_id: str,
        *,
        key: tuple[object, ...],
        timestamp_ms: int,
        reason: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        dedupe_key = (stage_id, reason, *key)
        if rejection_keys.get(stage_id) == dedupe_key:
            return
        rejection_keys[stage_id] = dedupe_key
        stage_rejections = diagnostics.get("stage_rejections")
        if not isinstance(stage_rejections, list):
            return
        payload: dict[str, object] = {
            "stage_id": stage_id,
            "timestamp_ms": int(timestamp_ms),
            "reason": str(reason),
        }
        if extra:
            payload.update(extra)
        stage_rejections.append(payload)

    def _resolve_confirmed_highs(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
    ) -> list[int]:
        if end_idx - start_idx < 2 or one.confirmed_high_indices.size == 0:
            return []
        upper_bound = min(end_idx - 1, len(one.timestamps) - 2)
        left = int(np.searchsorted(one.confirmed_high_indices, max(start_idx, 1), side="left"))
        right = int(np.searchsorted(one.confirmed_high_indices, upper_bound, side="right"))
        if left >= right:
            return []
        indices = one.confirmed_high_indices[left:right]
        confirmed_at = one.confirmed_high_confirmed_at[left:right]
        mask = confirmed_at <= end_idx
        if not np.any(mask):
            return []
        return [int(item) for item in indices[mask]]

    def _resolve_confirmed_lows(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
    ) -> list[int]:
        if end_idx - start_idx < 2 or one.confirmed_low_indices.size == 0:
            return []
        upper_bound = min(end_idx - 1, len(one.timestamps) - 2)
        left = int(np.searchsorted(one.confirmed_low_indices, max(start_idx, 1), side="left"))
        right = int(np.searchsorted(one.confirmed_low_indices, upper_bound, side="right"))
        if left >= right:
            return []
        indices = one.confirmed_low_indices[left:right]
        confirmed_at = one.confirmed_low_confirmed_at[left:right]
        mask = confirmed_at <= end_idx
        if not np.any(mask):
            return []
        return [int(item) for item in indices[mask]]

    @staticmethod
    def _round_to_preferred_step(value: float) -> float:
        if not np.isfinite(value) or value <= 0.0:
            return 1.0
        exponent = int(np.floor(np.log10(value)))
        candidates: list[float] = []
        for power in range(exponent - 1, exponent + 2):
            scale = float(10.0 ** power)
            for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
                candidates.append(factor * scale)
        return min(candidates, key=lambda candidate: (abs(candidate - value), candidate))

    @staticmethod
    def _net_leg_pnl(*, entry_price: float, exit_price: float, quantity: float, fee_rate: float) -> float:
        gross = (exit_price - entry_price) * quantity
        fees = fee_rate * quantity * (entry_price + exit_price)
        return gross - fees

    @staticmethod
    def _classify_time_exit_result(pnl: float) -> TradeResultType:
        epsilon = 1e-9
        if pnl < -epsilon:
            return TradeResultType.SL
        if pnl > epsilon:
            return TradeResultType.TIME_EXIT_PROFIT
        return TradeResultType.BE

    @staticmethod
    def _to_percent(value: float, base: float) -> float:
        if not np.isfinite(value) or not np.isfinite(base) or abs(base) <= 0.0:
            return 0.0
        return (value / base) * 100.0

    @staticmethod
    def _infer_timeframe_ms(frame: pd.DataFrame) -> int | None:
        if len(frame) < 2:
            return None
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().astype("int64")
        if len(timestamps) < 2:
            return None
        diffs = np.diff(timestamps.to_numpy())
        if diffs.size == 0:
            return None
        return int(pd.Series(diffs).mode().iloc[0])

    @staticmethod
    def _aggregate_to_5m(frame: pd.DataFrame) -> pd.DataFrame:
        work = frame.copy()
        timeframe_ms = 5 * 60_000
        work["bucket"] = (work["timestamp"] // timeframe_ms) * timeframe_ms
        aggregated = (
            work.groupby("bucket", as_index=False)
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .rename(columns={"bucket": "timestamp"})
        )
        return aggregated.reset_index(drop=True)

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator <= 0.0:
            return 0.0
        return numerator / denominator
