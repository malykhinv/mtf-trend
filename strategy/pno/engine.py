"""Signal engine for PNO."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

from domain.enums.timeframe import Timeframe
from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
from strategy.pno.config import PNO_DEFAULT_RISK_PCT, PnoParams

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
    low_range_tree: RangeSearchTree


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
    sleep_start_idx: np.ndarray
    sleep_end_idx: np.ndarray
    stage1_confirm_idx: np.ndarray
    stage1_hold_price: np.ndarray
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
    reference_high: float
    leg_start_idx: int
    leg_start_timestamp: int
    leg_start: float
    leg_size: float
    reference_leg_size: float
    hold_floor: float
    pump_range_5m: float = 0.0
    levels_timeframe_ms: int = 5 * 60_000
    sleep_start_timestamp: int = 0
    sleep_end_timestamp: int = 0
    stage1_confirm_timestamp: int = 0
    current_levels_timestamp: int = 0
    stage1_hold_price: float = 0.0
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
    pump_path_efficiency: float = 0.0
    pump_wick_share: float = 0.0
    pump_body_share_mean: float = 0.0
    pump_flat_body_share: float = 0.0
    pump_body_wick_edge: float = 0.0
    pump_micro_flat_bar_share: float = 0.0
    active_high_bar_body_share: float = 0.0
    active_high_bar_upper_wick_share: float = 0.0
    active_high_bar_close_position: float = 0.0
    pump_max_red_body_share_5m: float = 0.0
    pump_counterflow_ratio_5m: float = 0.0
    pump_max_red_body_share_1m: float = 0.0
    pump_counterflow_ratio_1m: float = 0.0
    pre_pump_range_1h: float = 0.0
    pre_pump_range_2h: float = 0.0
    pump_vs_pre_1h_ratio: float = 0.0
    pump_vs_pre_2h_ratio: float = 0.0
    reference_high_weight: float = 1.0
    hold_status_at_validation: str = "held_above_hold"
    leg_start_status_at_validation: str = "held_above_leg_start"
    active_high_5m_idx: int = -1
    htf_pullback_start_5m_idx: int = -1
    htf_pullback_end_5m_idx: int = -1
    htf_pullback_low_5m_idx: int = -1
    htf_pullback_low: float = 0.0
    htf_level: float = 0.0
    htf_level_timestamp: int = 0
    htf_pump_bars: int = 0
    htf_pullback_bars: int = 0
    htf_min_allowed_low: float = 0.0


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
    structure_high_idx: int = -1
    structure_high_timestamp: int = 0
    structure_high: float = 0.0
    structure_low_idx: int = -1
    structure_low_timestamp: int = 0
    structure_low: float = 0.0
    structure_pivot_indices: tuple[int, ...] = ()
    structure_pivot_prices: tuple[float, ...] = ()
    structure_pivot_kinds: tuple[str, ...] = ()


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
    post_high_ema20_pierce_count: int = 0
    post_high_close_below_ema20_count: int = 0
    post_high_wick_share: float = 0.0
    post_high_body_overlap_rate: float = 0.0
    post_high_max_red_body_share: float = 0.0
    post_high_chop_alternation_rate: float = 0.0
    pullback_wick_broke_leg_start: bool = False
    pullback_close_broke_leg_start: bool = False
    structure_high_idx: int = -1
    structure_high_timestamp: int = 0
    structure_high: float = 0.0
    structure_low_idx: int = -1
    structure_low_timestamp: int = 0
    structure_low: float = 0.0
    structure_break_idx: int = -1
    structure_break_timestamp: int = 0
    structure_break_close: float = 0.0
    structure_pivot_indices: tuple[int, ...] = ()
    structure_pivot_prices: tuple[float, ...] = ()
    structure_pivot_kinds: tuple[str, ...] = ()


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
    pullback_base_start_idx: int | None = None
    pullback_base_end_idx: int | None = None
    pullback_base_low: float | None = None
    pullback_base_high: float | None = None
    pullback_base_quality: float = 0.0
    pullback_base_left_vacuum: float = 0.0
    overhead_resistance_score: float = 0.0
    overhead_resistance_penalty: int = 0
    overhead_red_body_share: float = 0.0
    overhead_red_count: int = 0
    dominant_overhead_red_timestamp: int | None = None
    dominant_overhead_red_high: float | None = None
    dominant_overhead_red_body: float = 0.0
    level_life_ema_spread_growth_share: float = 0.0
    structure_source: str = ""


@dataclass(slots=True)
class ArmedContext:
    entry_idx: int
    stage1: Stage1Context
    stage3: Stage3Context
    stage4: Stage4Context


@dataclass(slots=True)
class Stage1StateArrays:
    inplay: np.ndarray
    pump_start_idx: np.ndarray
    sleep_start_idx: np.ndarray
    sleep_end_idx: np.ndarray
    stage1_confirm_idx: np.ndarray
    stage1_hold_price: np.ndarray

    @property
    def has_inplay(self) -> bool:
        return bool(self.inplay.size and np.any(self.inplay))


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
    _STAGE1_CACHE_VERSION = 3

    def __init__(self, *, cache_dir: str | Path | None = None) -> None:
        self._last_generation_diagnostics = self._empty_diagnostics()
        base_cache_dir = Path(cache_dir) if cache_dir is not None else Path("./.output/cache")
        self._stage1_cache_dir = base_cache_dir / "_derived" / "pno_stage1_state"
        self._stage1_cache_dir.mkdir(parents=True, exist_ok=True)
        self._stage1_state_memory_cache: dict[str, Stage1StateArrays] = {}
        self._runtime_reference_high_cache: dict[tuple[int, int], tuple[float, float]] = {}
        self._runtime_batch_depth = 0
        self._runtime_prepared_data_cache: dict[tuple[int, int, tuple[str, ...]], pd.DataFrame] = {}
        self._runtime_prepared_1m_cache: dict[tuple[int, int, int, int], OneMinuteFrame] = {}
        self._runtime_prepared_5m_cache: dict[tuple[int, int, int, int, str | None], FiveMinuteFrame] = {}

    def begin_runtime_batch(self) -> None:
        self._runtime_batch_depth += 1
        if self._runtime_batch_depth == 1:
            self._clear_runtime_frame_caches()

    def end_runtime_batch(self) -> None:
        if self._runtime_batch_depth <= 0:
            self._clear_runtime_frame_caches()
            self._runtime_batch_depth = 0
            return
        self._runtime_batch_depth -= 1
        if self._runtime_batch_depth == 0:
            self._clear_runtime_frame_caches()

    def _clear_runtime_frame_caches(self) -> None:
        self._runtime_prepared_data_cache.clear()
        self._runtime_prepared_1m_cache.clear()
        self._runtime_prepared_5m_cache.clear()

    @staticmethod
    def _runtime_frame_signature(frame: pd.DataFrame) -> tuple[int, int, tuple[str, ...]]:
        return (id(frame), len(frame), tuple(str(col) for col in frame.columns))

    def _prepare_data_cached(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self._runtime_batch_depth <= 0:
            return self.prepare_data(frame)
        cache_key = self._runtime_frame_signature(frame)
        cached = self._runtime_prepared_data_cache.get(cache_key)
        if cached is not None:
            return cached
        prepared = self.prepare_data(frame)
        self._runtime_prepared_data_cache[cache_key] = prepared
        return prepared

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

    def _build_stage1_cache_metadata(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
        levels_timeframe_ms: int,
        entry_timeframe_ms: int,
    ) -> dict[str, int | float | str]:
        levels_timestamps = pd.to_numeric(levels_frame["timestamp"], errors="coerce").dropna().astype("int64")
        entry_timestamps = pd.to_numeric(entry_frame["timestamp"], errors="coerce").dropna().astype("int64")
        return {
            "version": self._STAGE1_CACHE_VERSION,
            "symbol": str(params.symbol),
            "levels_timeframe_ms": int(levels_timeframe_ms),
            "entry_timeframe_ms": int(entry_timeframe_ms),
            "levels_rows": int(len(levels_frame)),
            "entry_rows": int(len(entry_frame)),
            "levels_start_ts": int(levels_timestamps.iloc[0]) if not levels_timestamps.empty else -1,
            "levels_end_ts": int(levels_timestamps.iloc[-1]) if not levels_timestamps.empty else -1,
            "entry_start_ts": int(entry_timestamps.iloc[0]) if not entry_timestamps.empty else -1,
            "entry_end_ts": int(entry_timestamps.iloc[-1]) if not entry_timestamps.empty else -1,
            "stage1_min_pump_pct": float(params.stage1_min_pump_pct),
            "stage1_min_pretrend_range_ratio_2h": float(params.stage1_min_pretrend_range_ratio_2h),
            "stage1_min_volume_ratio_start": float(params.stage1_min_volume_ratio_start),
        }

    @staticmethod
    def _stage1_cache_key(metadata: dict[str, int | float | str]) -> str:
        payload = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _stage1_cache_path(self, cache_key: str) -> Path:
        return self._stage1_cache_dir / f"{cache_key}.npz"

    def _load_stage1_state_cache(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
        levels_timeframe_ms: int,
        entry_timeframe_ms: int,
    ) -> tuple[str, Stage1StateArrays | None]:
        metadata = self._build_stage1_cache_metadata(
            levels_frame=levels_frame,
            entry_frame=entry_frame,
            params=params,
            levels_timeframe_ms=levels_timeframe_ms,
            entry_timeframe_ms=entry_timeframe_ms,
        )
        cache_key = self._stage1_cache_key(metadata)
        cached = self._stage1_state_memory_cache.get(cache_key)
        if cached is not None:
            return cache_key, cached

        cache_path = self._stage1_cache_path(cache_key)
        if not cache_path.exists():
            return cache_key, None

        try:
            with np.load(cache_path, allow_pickle=False) as payload:
                state = Stage1StateArrays(
                    inplay=payload["inplay"].astype(bool, copy=False),
                    pump_start_idx=payload["pump_start_idx"].astype(np.int64, copy=False),
                    sleep_start_idx=payload["sleep_start_idx"].astype(np.int64, copy=False),
                    sleep_end_idx=payload["sleep_end_idx"].astype(np.int64, copy=False),
                    stage1_confirm_idx=payload["stage1_confirm_idx"].astype(np.int64, copy=False),
                    stage1_hold_price=payload["stage1_hold_price"].astype(np.float64, copy=False),
                )
        except Exception:
            return cache_key, None

        self._stage1_state_memory_cache[cache_key] = state
        return cache_key, state

    def _store_stage1_state_cache(
        self,
        *,
        cache_key: str,
        state: Stage1StateArrays,
    ) -> None:
        self._stage1_state_memory_cache[cache_key] = state
        cache_path = self._stage1_cache_path(cache_key)
        tmp_path = cache_path.with_suffix(".tmp")
        try:
            with tmp_path.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    inplay=state.inplay.astype(bool, copy=False),
                    pump_start_idx=state.pump_start_idx.astype(np.int64, copy=False),
                    sleep_start_idx=state.sleep_start_idx.astype(np.int64, copy=False),
                    sleep_end_idx=state.sleep_end_idx.astype(np.int64, copy=False),
                    stage1_confirm_idx=state.stage1_confirm_idx.astype(np.int64, copy=False),
                    stage1_hold_price=state.stage1_hold_price.astype(np.float64, copy=False),
                )
            tmp_path.replace(cache_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _fast_stage1_candidate_count(
        self,
        *,
        levels_frame: pd.DataFrame,
        params: PnoParams,
        levels_timeframe_ms: int,
    ) -> int:
        if levels_frame.empty:
            return 0

        five = levels_frame.reset_index(drop=True)
        highs = pd.to_numeric(five["high"], errors="coerce")
        lows = pd.to_numeric(five["low"], errors="coerce")
        closes = pd.to_numeric(five["close"], errors="coerce")
        volumes = pd.to_numeric(five["volume"], errors="coerce")
        if highs.empty or lows.empty or closes.empty or volumes.empty:
            return 0

        prev_close = closes.shift(1).fillna(closes)
        tr = pd.concat(
            [
                highs - lows,
                (highs - prev_close).abs(),
                (lows - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        ema9 = closes.ewm(span=9, adjust=False).mean()
        ema20 = closes.ewm(span=20, adjust=False).mean()
        quote_volume = closes * volumes
        short_window = self._bars_for_duration(levels_timeframe_ms, 15 * 60_000)
        baseline_window = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        pretrend_1h_bars = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        pretrend_2h_bars = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        pump_window = max(pretrend_2h_bars, self._bars_for_duration(levels_timeframe_ms, 30 * 60_000))

        r3_quote = quote_volume.rolling(window=short_window, min_periods=short_window).median()
        b24_quote = quote_volume.shift(short_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        rolling_peak = highs.rolling(window=pump_window, min_periods=1).max()
        rolling_base = lows.rolling(window=pump_window, min_periods=1).min()
        pump_range = rolling_peak - rolling_base
        pump_pct = pump_range / rolling_base.replace(0.0, np.nan)
        pre_max_1h = highs.shift(1).rolling(window=pretrend_1h_bars, min_periods=1).max()
        pre_min_1h = lows.shift(1).rolling(window=pretrend_1h_bars, min_periods=1).min()
        pre_range_1h = pre_max_1h - pre_min_1h
        pre_max_2h = highs.shift(1).rolling(window=pretrend_2h_bars, min_periods=1).max()
        pre_min_2h = lows.shift(1).rolling(window=pretrend_2h_bars, min_periods=1).min()
        pre_range_2h = pre_max_2h - pre_min_2h
        quote_expansion = r3_quote / b24_quote.replace(0.0, np.nan)
        trend_ok = closes.gt(ema20) & ema9.gt(ema20)
        pretrend_ratio_2h = pump_range / pre_range_2h.replace(0.0, np.nan)

        candidate_mask = (
            trend_ok.fillna(False)
            & quote_expansion.ge(float(params.stage1_min_volume_ratio_start)).fillna(False)
            & pump_pct.ge(float(params.stage1_min_pump_pct)).fillna(False)
            & pump_range.gt(pre_range_1h).fillna(False)
            & pretrend_ratio_2h.ge(float(params.stage1_min_pretrend_range_ratio_2h)).fillna(False)
        )
        if bool(getattr(params, "ideal_like_impulse_enabled", False)):
            ideal_like_candidate_mask = (
                trend_ok.fillna(False)
                & quote_expansion.ge(max(float(params.stage1_min_volume_ratio_start), 3.0)).fillna(False)
                & pump_pct.ge(max(float(params.stage1_min_pump_pct), 0.10)).fillna(False)
                & pump_range.ge(pre_range_1h.mul(0.70)).fillna(False)
                & pretrend_ratio_2h.ge(max(float(params.stage1_min_pretrend_range_ratio_2h) * 0.60, 1.20)).fillna(False)
            )
            candidate_mask = candidate_mask | ideal_like_candidate_mask
        return int(candidate_mask.sum())

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
        entry_timeframe_ms = params.entry_timeframe.to_milliseconds()
        if timeframe_ms != entry_timeframe_ms:
            self._last_generation_diagnostics = self._empty_diagnostics()
            return []
        levels_timeframe_ms = params.levels_timeframe.to_milliseconds()
        levels_frame = self._aggregate_frame(prepared, target_timeframe_ms=levels_timeframe_ms)
        if levels_frame.empty:
            self._last_generation_diagnostics = self._empty_diagnostics()
            return []
        return self.generate_events_multi_tf(levels_frame=levels_frame, entry_frame=prepared, params=params)

    def generate_events_multi_tf(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
        seconds_frame_provider: object | None = None,
    ) -> list[TradeResult]:
        self._runtime_reference_high_cache.clear()
        prepared_levels = self._prepare_data_cached(levels_frame)
        prepared_entry = self._prepare_data_cached(entry_frame)
        diagnostics = self._empty_diagnostics()
        diagnostics["context"] = {
            **diagnostics["context"],
            "symbol": params.symbol,
            "stage_order": list(PNO_STAGE_SEQUENCE),
        }
        levels_required_bars = self._scale_required_bars(
            base_bars=params.min_data_5m,
            base_timeframe_ms=Timeframe.M5.to_milliseconds(),
            timeframe_ms=params.levels_timeframe.to_milliseconds(),
        )
        entry_timeframe_ms = params.entry_timeframe.to_milliseconds()
        source_entry_timeframe_ms = self._infer_timeframe_ms(prepared_entry) or entry_timeframe_ms
        uses_local_seconds_materialization = (
            entry_timeframe_ms < source_entry_timeframe_ms and seconds_frame_provider is not None
        )
        entry_required_bars = (
            max(
                12,
                self._bars_for_duration(
                    entry_timeframe_ms,
                    (params.stage1_pump_max_bars + params.stage1_pullback_max_bars + params.stage1_fetch_post_bars)
                    * params.levels_timeframe.to_milliseconds(),
                ),
            )
            if uses_local_seconds_materialization
            else self._scale_required_bars(
                base_bars=params.min_data_1m,
                base_timeframe_ms=Timeframe.M1.to_milliseconds(),
                timeframe_ms=entry_timeframe_ms,
            )
        )
        source_entry_required_bars = 2 if uses_local_seconds_materialization else entry_required_bars
        if len(prepared_levels) < levels_required_bars or len(prepared_entry) < source_entry_required_bars:
            diagnostics["skipped_insufficient_data"] = 1
            self._last_generation_diagnostics = diagnostics
            return []

        levels_timeframe_ms = params.levels_timeframe.to_milliseconds()
        stage1_cache_entry = self._load_stage1_state_cache(
            levels_frame=prepared_levels,
            entry_frame=prepared_entry,
            params=params,
            levels_timeframe_ms=levels_timeframe_ms,
            entry_timeframe_ms=entry_timeframe_ms,
        )
        cache_key, cached_stage1_state = stage1_cache_entry
        diagnostics["context"]["stage1_cache"] = "hit" if cached_stage1_state is not None else "miss"
        if cached_stage1_state is None:
            fast_candidate_count = self._fast_stage1_candidate_count(
                levels_frame=prepared_levels,
                params=params,
                levels_timeframe_ms=levels_timeframe_ms,
            )
            diagnostics["context"]["stage1_fast_candidates"] = int(fast_candidate_count)
            if fast_candidate_count <= 0:
                empty_state = Stage1StateArrays(
                    inplay=np.zeros(len(prepared_levels), dtype=bool),
                    pump_start_idx=np.full(len(prepared_levels), -1, dtype=np.int64),
                    sleep_start_idx=np.full(len(prepared_levels), -1, dtype=np.int64),
                    sleep_end_idx=np.full(len(prepared_levels), -1, dtype=np.int64),
                    stage1_confirm_idx=np.full(len(prepared_levels), -1, dtype=np.int64),
                    stage1_hold_price=np.full(len(prepared_levels), np.nan, dtype=np.float64),
                )
                self._store_stage1_state_cache(cache_key=cache_key, state=empty_state)
                diagnostics["context"]["stage1_fast_reject"] = "no_5m_candidates"
                self._last_generation_diagnostics = diagnostics
                return []
        else:
            diagnostics["context"]["stage1_fast_candidates"] = int(np.sum(cached_stage1_state.inplay))
            if not cached_stage1_state.has_inplay:
                diagnostics["context"]["stage1_fast_reject"] = "cached_empty_stage1"
                self._last_generation_diagnostics = diagnostics
                return []

        actual_entry_frame = prepared_entry
        if uses_local_seconds_materialization:
            actual_entry_frame = self._materialize_sparse_entry_frame(
                levels_frame=prepared_levels,
                source_entry_frame=prepared_entry,
                params=params,
                stage1_state=cached_stage1_state,
                seconds_frame_provider=seconds_frame_provider,
            )
            diagnostics["context"]["seconds_materialized"] = not actual_entry_frame.empty
            diagnostics["context"]["seconds_source_timeframe_ms"] = int(source_entry_timeframe_ms)
            if len(actual_entry_frame) < entry_required_bars:
                diagnostics["skipped_insufficient_data"] = 1
                diagnostics["context"]["seconds_materialized_bars"] = int(len(actual_entry_frame))
                self._last_generation_diagnostics = diagnostics
                return []

        one_cache_key = (
            id(actual_entry_frame),
            len(actual_entry_frame),
            int(entry_timeframe_ms),
            self._runtime_batch_depth,
        )
        one = self._runtime_prepared_1m_cache.get(one_cache_key)
        if one is None:
            one = self._prepare_1m_frame(
                actual_entry_frame,
                timeframe_ms=entry_timeframe_ms,
            )
            if self._runtime_batch_depth > 0:
                self._runtime_prepared_1m_cache[one_cache_key] = one
        five_cache_key = (
            id(prepared_levels),
            len(prepared_levels),
            int(levels_timeframe_ms),
            int(entry_timeframe_ms),
            cache_key,
        )
        five = self._runtime_prepared_5m_cache.get(five_cache_key)
        if five is None:
            five = self._prepare_5m_frame(
                prepared_levels,
                prepared_entry,
                params,
                levels_timeframe_ms=levels_timeframe_ms,
                entry_timeframe_ms=entry_timeframe_ms,
                stage1_cache_key=cache_key,
                cached_stage1_state=cached_stage1_state,
            )
            if self._runtime_batch_depth > 0:
                self._runtime_prepared_5m_cache[five_cache_key] = five
        trades = self._run(one=one, five=five, params=params, diagnostics=diagnostics)
        diagnostics["trades_generated"] = len(trades)
        self._last_generation_diagnostics = diagnostics
        return trades

    def _prepare_1m_frame(self, frame: pd.DataFrame, *, timeframe_ms: int = 60_000) -> OneMinuteFrame:
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
        volatility_window = self._bars_for_duration(timeframe_ms, 30 * 60_000)
        work["v1"] = tr.rolling(window=volatility_window, min_periods=volatility_window).median()
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
        low_range_tree = self._build_range_tree(lows, is_min_tree=True)
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
            low_range_tree=low_range_tree,
        )

    def _prepare_5m_frame(
        self,
        frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        params: PnoParams,
        *,
        levels_timeframe_ms: int,
        entry_timeframe_ms: int,
        stage1_cache_key: str | None = None,
        cached_stage1_state: Stage1StateArrays | None = None,
    ) -> FiveMinuteFrame:
        work = frame.copy()
        v_window = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        short_window = self._bars_for_duration(levels_timeframe_ms, 15 * 60_000)
        baseline_window = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        sleep_window = self._bars_for_duration(levels_timeframe_ms, 8 * 60 * 60_000)
        activity_window = self._bars_for_duration(levels_timeframe_ms, 30 * 60_000)
        activity_baseline_window = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        ema_cross_window = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        pre_high_24h_window = self._bars_for_duration(levels_timeframe_ms, 24 * 60 * 60_000)
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
        work["v5"] = tr.rolling(window=v_window, min_periods=v_window).median()
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
        work["ema_cross_count_1h"] = ema_cross.shift(1).rolling(window=ema_cross_window, min_periods=ema_cross_window).sum()
        work["r3_quote"] = work["quote_volume"].rolling(window=short_window, min_periods=short_window).median()
        work["b24_quote"] = work["quote_volume"].shift(short_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        work["l96_quote"] = work["quote_volume"].shift(short_window + baseline_window).rolling(window=sleep_window, min_periods=sleep_window).median()
        work["r3_trade"] = work["trade_activity"].rolling(window=short_window, min_periods=short_window).median()
        work["b24_trade"] = work["trade_activity"].shift(short_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        work["l96_trade"] = work["trade_activity"].shift(short_window + baseline_window).rolling(window=sleep_window, min_periods=sleep_window).median()
        work["r3_tr"] = work["tr"].rolling(window=short_window, min_periods=short_window).median()
        work["b24_tr"] = work["tr"].shift(short_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        work["l96_tr"] = work["tr"].shift(short_window + baseline_window).rolling(window=sleep_window, min_periods=sleep_window).median()
        work["activity_last6_quote"] = work["quote_volume"].rolling(window=activity_window, min_periods=activity_window).median()
        work["activity_prev24_quote"] = work["quote_volume"].shift(activity_window).rolling(window=activity_baseline_window, min_periods=activity_baseline_window).median()
        work["activity_last6_trade"] = work["trade_activity"].rolling(window=activity_window, min_periods=activity_window).median()
        work["activity_prev24_trade"] = work["trade_activity"].shift(activity_window).rolling(window=activity_baseline_window, min_periods=activity_baseline_window).median()
        work["cumulative_quote_volume"] = work["quote_volume"].cumsum()
        work["pre_high_24h"] = work["high"].shift(1).rolling(window=pre_high_24h_window, min_periods=pre_high_24h_window).max()
        work["pre_high_1h"] = work["high"].shift(1).rolling(window=ema_cross_window, min_periods=ema_cross_window).max()
        work["atr_pre_14"] = work["tr"].shift(1).rolling(window=14, min_periods=14).mean()
        work["pre_quote_median_24"] = work["quote_volume"].shift(1).rolling(window=baseline_window, min_periods=baseline_window).median()
        work["pre_trade_median_24"] = work["trade_activity"].shift(1).rolling(window=baseline_window, min_periods=baseline_window).median()
        work["close_above_ema20_count12"] = (work["close"] > work["ema20"]).rolling(window=ema_cross_window, min_periods=ema_cross_window).sum()
        work["r3_close_above_ema20_all"] = (work["close"] > work["ema20"]).rolling(window=short_window, min_periods=short_window).sum() == short_window
        work["r3_close_above_ema9_count"] = (work["close"] > work["ema9"]).rolling(window=short_window, min_periods=short_window).sum()
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
        if cached_stage1_state is None:
            cached_stage1_state = self._build_stage1_state(
                levels_frame=work,
                entry_frame=entry_frame,
                timestamps=work["timestamp"].astype("int64").to_numpy(),
                ema20=work["ema20"].astype("float64").to_numpy(),
                params=params,
                levels_timeframe_ms=levels_timeframe_ms,
                entry_timeframe_ms=entry_timeframe_ms,
            )
            if stage1_cache_key is not None:
                self._store_stage1_state_cache(cache_key=stage1_cache_key, state=cached_stage1_state)
        inplay = cached_stage1_state.inplay
        pump_start_idx = cached_stage1_state.pump_start_idx
        sleep_start_idx = cached_stage1_state.sleep_start_idx
        sleep_end_idx = cached_stage1_state.sleep_end_idx
        stage1_confirm_idx = cached_stage1_state.stage1_confirm_idx
        stage1_hold_price = cached_stage1_state.stage1_hold_price
        work["inplay"] = inplay
        work["pump_start_idx"] = pump_start_idx
        work["sleep_start_idx"] = sleep_start_idx
        work["sleep_end_idx"] = sleep_end_idx
        work["stage1_confirm_idx"] = stage1_confirm_idx
        work["stage1_hold_price"] = stage1_hold_price
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
            sleep_start_idx=work["sleep_start_idx"].astype("int64").to_numpy(),
            sleep_end_idx=work["sleep_end_idx"].astype("int64").to_numpy(),
            stage1_confirm_idx=work["stage1_confirm_idx"].astype("int64").to_numpy(),
            stage1_hold_price=work["stage1_hold_price"].astype("float64").to_numpy(),
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

    def _build_stage1_state(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        timestamps: np.ndarray,
        ema20: np.ndarray,
        params: PnoParams,
        levels_timeframe_ms: int,
        entry_timeframe_ms: int,
    ) -> Stage1StateArrays:
        return self._build_fallback_stage1_state(
            levels_frame=levels_frame,
            entry_frame=entry_frame,
            timestamps=timestamps,
            ema20=ema20,
            params=params,
            levels_timeframe_ms=levels_timeframe_ms,
            entry_timeframe_ms=entry_timeframe_ms,
        )

    def _build_fallback_stage1_state(
        self,
        *,
        levels_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        timestamps: np.ndarray,
        ema20: np.ndarray,
        params: PnoParams,
        levels_timeframe_ms: int,
        entry_timeframe_ms: int,
    ) -> Stage1StateArrays:
        bars_count = int(len(timestamps))
        inplay = np.zeros(bars_count, dtype=bool)
        pump_start_idx = np.full(bars_count, -1, dtype=np.int64)
        sleep_start_idx = np.full(bars_count, -1, dtype=np.int64)
        sleep_end_idx = np.full(bars_count, -1, dtype=np.int64)
        stage1_confirm_idx = np.full(bars_count, -1, dtype=np.int64)
        stage1_hold_price = np.full(bars_count, np.nan, dtype=np.float64)
        if bars_count == 0:
            return Stage1StateArrays(
                inplay=inplay,
                pump_start_idx=pump_start_idx,
                sleep_start_idx=sleep_start_idx,
                sleep_end_idx=sleep_end_idx,
                stage1_confirm_idx=stage1_confirm_idx,
                stage1_hold_price=stage1_hold_price,
            )

        five = levels_frame.reset_index(drop=True)
        local_breakout_window = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        recent_support_window = self._bars_for_duration(levels_timeframe_ms, 10 * 60_000)
        below_ema20_limit = self._bars_for_duration(levels_timeframe_ms, 10 * 60_000)
        pump_start_lookback = self._bars_for_duration(levels_timeframe_ms, 30 * 60_000)
        pump_start_shift_lookback = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        pump_start_precursor_extension = self._bars_for_duration(levels_timeframe_ms, 15 * 60_000)
        sleep_lookback = self._bars_for_duration(levels_timeframe_ms, 7 * 60 * 60_000)
        pretrend_1h_bars = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        pretrend_2h_bars = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        one_support = self._build_one_minute_stage1_support(
            entry_frame=entry_frame,
            five_timestamps=timestamps,
            entry_timeframe_ms=entry_timeframe_ms,
        )
        sleep = five["sleep"].astype("bool").to_numpy() if "sleep" in five.columns else np.zeros(bars_count, dtype=bool)
        wake = five["wake"].astype("bool").to_numpy() if "wake" in five.columns else np.zeros(bars_count, dtype=bool)
        highs = pd.to_numeric(five["high"], errors="coerce").to_numpy(dtype=np.float64)
        lows = pd.to_numeric(five["low"], errors="coerce").to_numpy(dtype=np.float64)
        closes = pd.to_numeric(five["close"], errors="coerce").to_numpy(dtype=np.float64)
        ema9 = pd.to_numeric(five["ema9"], errors="coerce").to_numpy(dtype=np.float64)
        r3_quote = pd.to_numeric(five["r3_quote"], errors="coerce").to_numpy(dtype=np.float64)
        b24_quote = pd.to_numeric(five["b24_quote"], errors="coerce").to_numpy(dtype=np.float64)
        r3_trade = pd.to_numeric(five["r3_trade"], errors="coerce").to_numpy(dtype=np.float64)
        b24_trade = pd.to_numeric(five["b24_trade"], errors="coerce").to_numpy(dtype=np.float64)
        activity_last6_quote = pd.to_numeric(five["activity_last6_quote"], errors="coerce").to_numpy(dtype=np.float64)
        activity_prev24_quote = pd.to_numeric(five["activity_prev24_quote"], errors="coerce").to_numpy(dtype=np.float64)
        activity_last6_trade = pd.to_numeric(five["activity_last6_trade"], errors="coerce").to_numpy(dtype=np.float64)
        activity_prev24_trade = pd.to_numeric(five["activity_prev24_trade"], errors="coerce").to_numpy(dtype=np.float64)
        prior_highs = (
            pd.Series(highs, dtype="float64")
            .shift(1)
            .rolling(window=local_breakout_window, min_periods=1)
            .max()
            .to_numpy(dtype=np.float64)
        )
        local_breakout = np.isfinite(prior_highs) & (highs >= (prior_highs - self._EPSILON))
        if local_breakout.size:
            local_breakout[0] = False

        recent_support = (
            pd.Series(one_support.astype(np.int8), dtype="int8")
            .rolling(window=recent_support_window, min_periods=1)
            .max()
            .fillna(0)
            .to_numpy(dtype=np.int8)
            > 0
        )

        active_start_idx: int | None = None
        below_ema20_count = 0
        for idx in range(bars_count):
            quote_expansion = self._safe_divide(float(r3_quote[idx]), float(b24_quote[idx]))
            trade_expansion = self._safe_divide(float(r3_trade[idx]), float(b24_trade[idx]))
            sustain_quote = self._safe_divide(float(activity_last6_quote[idx]), float(activity_prev24_quote[idx]))
            sustain_trade = self._safe_divide(float(activity_last6_trade[idx]), float(activity_prev24_trade[idx]))
            wake_transition = bool(
                idx > 0
                and sleep[idx - 1]
                and wake[idx]
                and recent_support[idx]
                and local_breakout[idx]
                and quote_expansion >= 2.0
                and trade_expansion >= 2.0
            )
            self_sustain = bool(
                recent_support[idx]
                and local_breakout[idx]
                and np.isfinite(closes[idx])
                and np.isfinite(ema20[idx])
                and np.isfinite(ema9[idx])
                and closes[idx] > ema20[idx]
                and ema9[idx] > ema20[idx]
                and (sustain_quote >= 1.35 or sustain_trade >= 1.35)
            )
            prev_inplay = bool(inplay[idx - 1]) if idx > 0 else False
            stale_anchor_rearmed = False
            if prev_inplay and active_start_idx is not None:
                rearm_start_idx = self._resolve_stale_pump_rearm_start_idx(
                    active_start_idx=active_start_idx,
                    idx=idx,
                    levels_timeframe_ms=levels_timeframe_ms,
                    pump_start_lookback=pump_start_lookback,
                    wake_transition=wake_transition,
                    self_sustain=self_sustain,
                    wake=wake,
                    support=recent_support,
                    local_breakout=local_breakout,
                    ema9=ema9,
                    ema20=ema20,
                    pump_start_shift_lookback=pump_start_shift_lookback,
                    pump_start_precursor_extension=pump_start_precursor_extension,
                    closes=closes,
                )
                if rearm_start_idx is not None:
                    active_start_idx = int(rearm_start_idx)
                    below_ema20_count = 0
                    stale_anchor_rearmed = True
            stage1_already_confirmed = prev_inplay and (idx > 0 and pump_start_idx[idx - 1] != -1) and not stale_anchor_rearmed
            if prev_inplay:
                if np.isfinite(ema20[idx]) and closes[idx] < (ema20[idx] - self._EPSILON):
                    below_ema20_count += 1
                else:
                    below_ema20_count = 0
                # После подтверждения Stage 1 снижение активности не прерывает inplay
                if not stage1_already_confirmed:
                    if below_ema20_count >= below_ema20_limit or (sustain_quote < 1.0 and sustain_trade < 1.0):
                        active_start_idx = None
                        continue
                inplay[idx] = True
            elif wake_transition or self_sustain:
                inplay[idx] = True
                below_ema20_count = 0
                active_start_idx = self._resolve_fallback_pump_start_idx(
                    wake=wake,
                    support=recent_support,
                    local_breakout=local_breakout,
                    idx=idx,
                    lookback_bars=pump_start_lookback,
                )
                active_start_idx = self._extend_pump_start_to_precursor_breakout(
                    local_breakout=local_breakout,
                    closes=closes,
                    ema20=ema20,
                    pump_start_idx=active_start_idx,
                    max_extension_bars=pump_start_precursor_extension,
                )
                active_start_idx = self._shift_pump_start_left_to_ema_reset(
                    ema9=ema9,
                    ema20=ema20,
                    pump_start_idx=active_start_idx,
                    max_shift_bars=pump_start_shift_lookback,
                )
            if not inplay[idx] or active_start_idx is None or active_start_idx >= idx:
                continue
            # Если Stage 1 уже подтвержден, пропускаем проверки и устанавливаем индексы
            if stage1_already_confirmed:
                pump_start_idx[idx] = int(active_start_idx)
                sleep_start_idx[idx] = int(max(active_start_idx - sleep_lookback, 0))
                sleep_end_idx[idx] = int(max(active_start_idx - 1, sleep_start_idx[idx]))
                stage1_confirm_idx[idx] = int(idx)
                base_price = float(np.nanmin(lows[active_start_idx : idx + 1]))
                peak_price = float(np.nanmax(highs[active_start_idx : idx + 1]))
                stage1_hold_price[idx] = base_price + (0.50 * max(peak_price - base_price, 0.0))
                continue
            pump_range = float(np.nanmax(highs[active_start_idx : idx + 1]) - np.nanmin(lows[active_start_idx : idx + 1]))
            pump_pct = self._safe_divide(pump_range, float(np.nanmin(lows[active_start_idx : idx + 1])))
            pre_range_1h = self._resolve_window_range(
                highs=highs,
                lows=lows,
                start_idx=max(0, active_start_idx - pretrend_1h_bars),
                end_idx=active_start_idx - 1,
            )
            pre_range_2h = self._resolve_window_range(
                highs=highs,
                lows=lows,
                start_idx=max(0, active_start_idx - pretrend_2h_bars),
                end_idx=active_start_idx - 1,
            )
            pretrend_ratio_2h = self._safe_divide(pump_range, pre_range_2h)
            strong_wake_override = bool(
                local_breakout[idx]
                and recent_support[idx]
                and quote_expansion >= max(float(params.stage1_min_volume_ratio_start), 5.0)
                and (sustain_quote >= 3.0 or sustain_trade >= 3.0)
                and pump_range > max(pre_range_1h, self._EPSILON)
                and pump_pct >= max(float(params.stage1_min_pump_pct), 0.08)
            )
            if pump_pct < float(params.stage1_min_pump_pct):
                inplay[idx] = False
                continue
            if pump_range <= max(pre_range_1h, pre_range_2h, self._EPSILON) and not strong_wake_override:
                inplay[idx] = False
                continue
            if pretrend_ratio_2h < float(params.stage1_min_pretrend_range_ratio_2h) and not strong_wake_override:
                inplay[idx] = False
                continue
            if quote_expansion < float(params.stage1_min_volume_ratio_start):
                inplay[idx] = False
                continue
            pump_start_idx[idx] = int(active_start_idx)
            sleep_start_idx[idx] = int(max(active_start_idx - sleep_lookback, 0))
            sleep_end_idx[idx] = int(max(active_start_idx - 1, sleep_start_idx[idx]))
            stage1_confirm_idx[idx] = int(idx)
            base_price = float(np.nanmin(lows[active_start_idx : idx + 1]))
            peak_price = float(np.nanmax(highs[active_start_idx : idx + 1]))
            stage1_hold_price[idx] = base_price + (0.50 * max(peak_price - base_price, 0.0))

        return Stage1StateArrays(
            inplay=inplay,
            pump_start_idx=pump_start_idx,
            sleep_start_idx=sleep_start_idx,
            sleep_end_idx=sleep_end_idx,
            stage1_confirm_idx=stage1_confirm_idx,
            stage1_hold_price=stage1_hold_price,
        )

    def _build_one_minute_stage1_support(
        self,
        *,
        entry_frame: pd.DataFrame,
        five_timestamps: np.ndarray,
        entry_timeframe_ms: int,
    ) -> np.ndarray:
        support = np.zeros(len(five_timestamps), dtype=bool)
        if entry_frame.empty or len(five_timestamps) == 0:
            return support
        recent_window = self._bars_for_duration(entry_timeframe_ms, 15 * 60_000)
        baseline_window = self._bars_for_duration(entry_timeframe_ms, 60 * 60_000)
        close_above_ema20_threshold = self._threshold_count(recent_window, numerator=10, denominator=15)
        close_above_ema9_threshold = self._threshold_count(recent_window, numerator=8, denominator=15)
        one = entry_frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        one["quote_volume"] = pd.to_numeric(one["close"], errors="coerce") * pd.to_numeric(one["volume"], errors="coerce")
        prev_close = pd.to_numeric(one["close"], errors="coerce").shift(1).fillna(one["close"])
        tr = pd.concat(
            [
                pd.to_numeric(one["high"], errors="coerce") - pd.to_numeric(one["low"], errors="coerce"),
                (pd.to_numeric(one["high"], errors="coerce") - prev_close).abs(),
                (pd.to_numeric(one["low"], errors="coerce") - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        one["tr"] = tr
        one["ema9"] = pd.to_numeric(one["close"], errors="coerce").ewm(span=9, adjust=False).mean()
        one["ema20"] = pd.to_numeric(one["close"], errors="coerce").ewm(span=20, adjust=False).mean()
        one["r15_quote"] = one["quote_volume"].rolling(window=recent_window, min_periods=recent_window).median()
        one["b60_quote"] = one["quote_volume"].shift(recent_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        one["r15_trade"] = pd.to_numeric(one["volume"], errors="coerce").rolling(window=recent_window, min_periods=recent_window).median()
        one["b60_trade"] = pd.to_numeric(one["volume"], errors="coerce").shift(recent_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        one["r15_tr"] = one["tr"].rolling(window=recent_window, min_periods=recent_window).median()
        one["b60_tr"] = one["tr"].shift(recent_window).rolling(window=baseline_window, min_periods=baseline_window).median()
        one["close_above_ema20"] = (pd.to_numeric(one["close"], errors="coerce") > one["ema20"]).rolling(window=recent_window, min_periods=recent_window).sum()
        one["close_above_ema9"] = (pd.to_numeric(one["close"], errors="coerce") > one["ema9"]).rolling(window=recent_window, min_periods=recent_window).sum()
        support_mask = (
            (one["r15_quote"] >= (1.8 * one["b60_quote"]))
            & (one["r15_trade"] >= (1.8 * one["b60_trade"]))
            & (one["r15_tr"] >= (1.5 * one["b60_tr"]))
            & (one["close_above_ema20"] >= close_above_ema20_threshold)
            & (one["close_above_ema9"] >= close_above_ema9_threshold)
        ).fillna(False)
        one_timestamps = pd.to_numeric(one["timestamp"], errors="coerce").to_numpy(dtype=np.int64)
        support_indices = np.where(support_mask.to_numpy(dtype=bool))[0]
        if support_indices.size == 0:
            return support
        five_indices = np.searchsorted(five_timestamps, one_timestamps[support_indices], side="right") - 1
        valid_five_indices = five_indices[(five_indices >= 0) & (five_indices < len(support))]
        if valid_five_indices.size > 0:
            support[valid_five_indices.astype(np.int64, copy=False)] = True
        return support

    @staticmethod
    def _resolve_fallback_pump_start_idx(
        *,
        wake: np.ndarray,
        support: np.ndarray,
        local_breakout: np.ndarray,
        idx: int,
        lookback_bars: int,
    ) -> int:
        start_idx = idx
        for probe_idx in range(max(0, idx - lookback_bars), idx + 1):
            if bool(wake[probe_idx]) and bool(support[probe_idx]) and bool(local_breakout[probe_idx]):
                start_idx = probe_idx
                break
        return int(start_idx)

    @staticmethod
    def _extend_pump_start_to_precursor_breakout(
        *,
        local_breakout: np.ndarray,
        closes: np.ndarray,
        ema20: np.ndarray,
        pump_start_idx: int,
        max_extension_bars: int,
    ) -> int:
        extended_idx = int(pump_start_idx)
        lower_bound = max(0, int(pump_start_idx) - max(0, int(max_extension_bars)))
        for probe_idx in range(int(pump_start_idx) - 1, lower_bound - 1, -1):
            if not bool(local_breakout[probe_idx]):
                break
            close_value = float(closes[probe_idx])
            ema20_value = float(ema20[probe_idx])
            if not np.isfinite(close_value) or not np.isfinite(ema20_value):
                break
            if close_value <= (ema20_value + PnoEngine._EPSILON):
                break
            extended_idx = int(probe_idx)
        return extended_idx

    @staticmethod
    def _shift_pump_start_left_to_ema_reset(
        *,
        ema9: np.ndarray,
        ema20: np.ndarray,
        pump_start_idx: int,
        max_shift_bars: int | None = None,
    ) -> int:
        shifted_idx = int(pump_start_idx)
        lower_bound = 0
        if max_shift_bars is not None and max_shift_bars >= 0:
            lower_bound = max(0, int(pump_start_idx) - int(max_shift_bars))
        found_reset = False
        for idx in range(int(pump_start_idx), lower_bound - 1, -1):
            ema9_value = float(ema9[idx])
            ema20_value = float(ema20[idx])
            if not np.isfinite(ema9_value) or not np.isfinite(ema20_value):
                continue
            if ema9_value < ema20_value:
                shifted_idx = int(idx)
                found_reset = True
                break
            if max_shift_bars is None:
                shifted_idx = int(idx)
        if max_shift_bars is not None and not found_reset:
            return int(pump_start_idx)
        return shifted_idx

    def _resolve_stale_pump_rearm_start_idx(
        self,
        *,
        active_start_idx: int,
        idx: int,
        levels_timeframe_ms: int,
        pump_start_lookback: int,
        wake_transition: bool,
        self_sustain: bool,
        wake: np.ndarray,
        support: np.ndarray,
        local_breakout: np.ndarray,
        ema9: np.ndarray,
        ema20: np.ndarray,
        pump_start_shift_lookback: int,
        pump_start_precursor_extension: int,
        closes: np.ndarray,
    ) -> int | None:
        stale_anchor_age_bars = self._bars_for_duration(levels_timeframe_ms, 12 * 60 * 60_000)
        fresh_anchor_max_age_bars = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)
        stale_anchor_min_gap_bars = self._bars_for_duration(levels_timeframe_ms, 3 * 60 * 60_000)
        if idx <= active_start_idx or (idx - active_start_idx) < stale_anchor_age_bars:
            return None
        if not (wake_transition or self_sustain):
            return None
        search_start_idx = max(0, idx - pump_start_lookback)
        candidate_idx: int | None = None
        for probe_idx in range(search_start_idx, idx + 1):
            if not bool(support[probe_idx]) or not bool(local_breakout[probe_idx]):
                continue
            if wake_transition and not bool(wake[probe_idx]):
                continue
            candidate_idx = int(probe_idx)
            break
        if candidate_idx is None:
            return None
        candidate_idx = self._extend_pump_start_to_precursor_breakout(
            local_breakout=local_breakout,
            closes=closes,
            ema20=ema20,
            pump_start_idx=candidate_idx,
            max_extension_bars=pump_start_precursor_extension,
        )
        shifted_candidate_idx = self._shift_pump_start_left_to_ema_reset(
            ema9=ema9,
            ema20=ema20,
            pump_start_idx=candidate_idx,
            max_shift_bars=pump_start_shift_lookback,
        )
        if (idx - shifted_candidate_idx) <= fresh_anchor_max_age_bars:
            candidate_idx = shifted_candidate_idx
        if candidate_idx <= active_start_idx:
            return None
        if (candidate_idx - active_start_idx) < stale_anchor_min_gap_bars:
            return None
        if (idx - candidate_idx) > fresh_anchor_max_age_bars:
            return None
        return int(candidate_idx)

    @staticmethod
    def _resolve_window_range(*, highs: np.ndarray, lows: np.ndarray, start_idx: int, end_idx: int) -> float:
        if end_idx < start_idx or start_idx < 0 or end_idx >= len(highs):
            return 0.0
        window_highs = highs[start_idx : end_idx + 1]
        window_lows = lows[start_idx : end_idx + 1]
        if window_highs.size == 0 or window_lows.size == 0:
            return 0.0
        if not np.isfinite(window_highs).any() or not np.isfinite(window_lows).any():
            return 0.0
        return float(np.nanmax(window_highs) - np.nanmin(window_lows))

    def _resolve_stage1_pump_candidate_rejection(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        params: PnoParams,
    ) -> dict[str, object] | None:
        if idx < 0 or five_idx <= 0 or five_idx >= len(five.timestamps):
            return None
        if not bool(five.inplay[five_idx]):
            return None

        pump_start_5m_idx = int(five.pump_start_idx[five_idx])
        confirm_5m_idx = int(five.stage1_confirm_idx[five_idx])
        if pump_start_5m_idx < 0 or pump_start_5m_idx >= five_idx or confirm_5m_idx < pump_start_5m_idx:
            return None

        levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
        pre_pump_1h_bars = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        pre_pump_2h_bars = self._bars_for_duration(levels_timeframe_ms, 2 * 60 * 60_000)

        highs_after_start = five.highs[pump_start_5m_idx : five_idx + 1]
        if highs_after_start.size < 2 or not np.isfinite(highs_after_start).any():
            return None
        active_high_5m_idx = pump_start_5m_idx + int(np.nanargmax(highs_after_start))
        if active_high_5m_idx <= pump_start_5m_idx:
            return None

        pump_start_price = float(five.lows[pump_start_5m_idx])
        active_high = float(five.highs[active_high_5m_idx])
        leg_size = active_high - pump_start_price
        min_leg = max(
            float(params.min_stage1_leg_v1 * one.v1[idx]),
            float(params.min_stage1_leg_v5_fraction * five.v5[five_idx]),
            self._EPSILON,
        )
        if leg_size < min_leg:
            return None
        pump_pct = self._safe_divide(leg_size, pump_start_price)
        if pump_pct < float(params.stage1_min_pump_pct):
            return None

        pump_pre_atr = float(five.atr_pre_14[pump_start_5m_idx])
        pump_tr = five.tr[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_opens = five.opens[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_closes = five.closes[pump_start_5m_idx : active_high_5m_idx + 1]
        if pump_tr.size == 0 or pump_opens.size == 0 or pump_closes.size == 0:
            return None
        pump_net_move = max(float(pump_closes[-1]) - float(pump_opens[0]), 0.0)
        pump_path_efficiency = self._safe_divide(pump_net_move, float(np.sum(pump_tr)))

        pre_start_idx = max(0, pump_start_5m_idx - pre_pump_1h_bars)
        pre_tr = five.tr[pre_start_idx:pump_start_5m_idx]
        pre_closes = five.closes[pre_start_idx:pump_start_5m_idx]
        pre_pump_barcode_fraction_1h = np.nan
        if (
            pre_tr.size > 0
            and pre_closes.size > 0
            and np.isfinite(pump_pre_atr)
            and pump_pre_atr > 0.0
        ):
            median_pre_close = float(np.nanmedian(pre_closes))
            barcode_threshold = max(
                float(params.stage1_barcode_tr_atr_fraction) * pump_pre_atr,
                float(params.stage1_barcode_tr_price_fraction) * median_pre_close,
            )
            pre_pump_barcode_fraction_1h = float(np.mean(pre_tr <= barcode_threshold))

        current_close = float(five.closes[five_idx])
        current_ema20 = float(five.ema20[five_idx])
        reason: str | None = None
        if np.isfinite(current_close) and np.isfinite(current_ema20) and current_close <= (current_ema20 + self._EPSILON):
            reason = "pump_candidate_below_ema20"
        elif (
            np.isfinite(pre_pump_barcode_fraction_1h)
            and pre_pump_barcode_fraction_1h > float(params.stage1_barcode_max_fraction_1h)
        ):
            reason = "pump_candidate_barcode"
        elif pump_path_efficiency < float(params.stage1_min_path_efficiency):
            reason = "pump_candidate_jerky"
        if reason is None:
            return None

        pre_pump_range_1h = self._resolve_window_range(
            highs=five.highs,
            lows=five.lows,
            start_idx=max(0, pump_start_5m_idx - pre_pump_1h_bars),
            end_idx=pump_start_5m_idx - 1,
        )
        pre_pump_range_2h = self._resolve_window_range(
            highs=five.highs,
            lows=five.lows,
            start_idx=max(0, pump_start_5m_idx - pre_pump_2h_bars),
            end_idx=pump_start_5m_idx - 1,
        )
        return {
            "key": (int(pump_start_5m_idx),),
            "timestamp_ms": int(one.timestamps[idx]),
            "reason": reason,
            "extra": {
                "current_timestamp_ms": int(one.timestamps[idx]),
                "pump_start_timestamp_ms": int(five.timestamps[pump_start_5m_idx]),
                "active_high_timestamp_ms": int(five.timestamps[active_high_5m_idx]),
                "active_high": round(active_high, 8),
                "leg_start": round(pump_start_price, 8),
                "leg_size": round(leg_size, 8),
                "stage1_min_leg": round(min_leg, 8),
                "pump_pct": round(pump_pct, 4),
                "stage1_min_pump_pct": round(float(params.stage1_min_pump_pct), 4),
                "pump_path_efficiency": round(pump_path_efficiency, 4),
                "stage1_min_path_efficiency": round(float(params.stage1_min_path_efficiency), 4),
                "pre_pump_barcode_fraction_1h": (
                    round(pre_pump_barcode_fraction_1h, 4) if np.isfinite(pre_pump_barcode_fraction_1h) else None
                ),
                "stage1_barcode_max_fraction_1h": round(float(params.stage1_barcode_max_fraction_1h), 4),
                "pre_pump_range_1h": round(pre_pump_range_1h, 8),
                "pre_pump_range_2h": round(pre_pump_range_2h, 8),
                "current_close_5m": round(current_close, 8) if np.isfinite(current_close) else None,
                "current_ema20_5m": round(current_ema20, 8) if np.isfinite(current_ema20) else None,
            },
        }

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
        min_entry_bars = self._scale_required_bars(
            base_bars=params.min_data_1m,
            base_timeframe_ms=Timeframe.M1.to_milliseconds(),
            timeframe_ms=params.entry_timeframe.to_milliseconds(),
        )
        min_levels_bars = self._scale_required_bars(
            base_bars=params.min_data_5m,
            base_timeframe_ms=Timeframe.M5.to_milliseconds(),
            timeframe_ms=params.levels_timeframe.to_milliseconds(),
        )

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

            if armed is not None and armed.entry_idx <= i:
                levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
                confirmation_mode = str(getattr(params, "entry_confirmation_mode", "cross"))
                entry_ts_idx = i + 1 if confirmation_mode == "close_above" else i
                if entry_ts_idx < len(one.timestamps):
                    bars_since_active_high = int(
                        max(
                            (int(one.timestamps[entry_ts_idx]) - int(armed.stage4.active_high_timestamp))
                            // max(levels_timeframe_ms, 1),
                            0,
                        )
                    )
                    if bars_since_active_high > int(params.max_htf_bars_since_active_high):
                        _reject_stage(
                            PNO_STAGE_5_TRADE,
                            key=(
                                armed.stage4.active_high_idx,
                                armed.stage4.cluster_first_idx,
                                armed.stage4.cluster_last_idx,
                                armed.entry_idx,
                            ),
                            timestamp_ms=int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                            reason="too_many_htf_bars_since_active_high",
                            extra={
                                "htf_bars_since_active_high": bars_since_active_high,
                                "max_htf_bars_since_active_high": int(params.max_htf_bars_since_active_high),
                                "active_high_timestamp_ms": int(armed.stage4.active_high_timestamp),
                                "entry_signal_timestamp_ms": int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                                "entry_timestamp_ms": int(one.timestamps[entry_ts_idx]),
                            },
                        )
                        armed = None
                        i += 1
                        continue
                live_armed = armed if armed.entry_idx == i else replace(armed, entry_idx=i)
                trade, exit_idx = self._try_enter_and_simulate(one=one, params=params, armed=live_armed)
                if trade is not None:
                    trades.append(trade)
                    retired_clusters.append(
                        RetiredCluster(
                            active_high_idx=live_armed.stage4.active_high_idx,
                            cluster_first_idx=live_armed.stage4.cluster_first_idx,
                            cluster_last_idx=live_armed.stage4.cluster_last_idx,
                            level=float(live_armed.stage4.level),
                            pullback_low_idx=live_armed.stage3.pullback_low_idx,
                            pullback_low=float(live_armed.stage3.pullback_low),
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
                            "score": float(trade.metadata.get("final_score", live_armed.stage4.final_score)),
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
                if self._is_armed_entry_invalidated_before_trigger(
                    one=one,
                    idx=i,
                    armed=live_armed,
                    params=params,
                ):
                    _reject_stage(
                        PNO_STAGE_5_TRADE,
                        key=(armed.stage4.active_high_idx, armed.stage4.cluster_first_idx, armed.stage4.cluster_last_idx, armed.entry_idx),
                        timestamp_ms=int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                        reason="entry_invalidated_before_trigger",
                        extra={
                            "active_high": round(float(live_armed.stage4.active_high), 8),
                            "pullback_low": round(float(live_armed.stage3.pullback_low), 8),
                            "level": round(float(live_armed.stage4.level), 8),
                            "score": round(float(live_armed.stage4.final_score), 4),
                            "touches": int(live_armed.stage4.touches),
                            "entry_pos": round(float(live_armed.stage4.entry_pos), 4),
                            "level_maturity_fraction": round(float(live_armed.stage4.level_maturity_fraction), 4),
                            "pump_start_timestamp_ms": int(live_armed.stage1.pump_start_timestamp),
                            "active_high_timestamp_ms": int(live_armed.stage4.active_high_timestamp),
                            "pullback_low_timestamp_ms": int(live_armed.stage3.pullback_low_timestamp),
                            "level_first_local_high_timestamp_ms": int(one.timestamps[live_armed.stage4.cluster_first_idx]),
                            "level_last_local_high_timestamp_ms": int(one.timestamps[live_armed.stage4.cluster_last_idx]),
                            "level_valid_timestamp_ms": int(live_armed.stage4.level_valid_timestamp),
                            "entry_confirmation_mode": str(getattr(params, "entry_confirmation_mode", "cross")),
                            "entry_signal_timestamp_ms": int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                            "entry_plan": round(float(live_armed.stage4.entry_plan), 8),
                            "sl_plan": round(float(live_armed.stage4.sl_plan), 8),
                            "tp1": round(float(live_armed.stage4.tp1), 8),
                            "tp2": round(float(live_armed.stage4.tp2), 8),
                        },
                    )
                    # Неблокирующее отклонение: продолжаем искать entry trigger
                    i += 1
                    continue

            if (
                i < min_entry_bars - 1
                or five_idx < min_levels_bars - 1
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

            allow_pullback_search_after_inplay_end = (
                stage1 is not None
                and stage2 is None
                and stage3 is None
                and self._allows_pullback_search_after_inplay_end(
                    one=one,
                    five=five,
                    idx=i,
                    five_idx=five_idx,
                    stage1=stage1,
                )
            )
            if not bool(five.inplay[five_idx]) and not allow_pullback_search_after_inplay_end:
                prev_stage1 = stage1
                prev_stage2 = stage2
                prev_stage3 = stage3
                if prev_stage1 is not None and (prev_stage2 is not None or prev_stage3 is not None):
                    i += 1
                    continue
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

            next_stage1, stage1_rejection = self._resolve_stage1_context(
                one=one,
                five=five,
                idx=i,
                five_idx=five_idx,
                params=params,
            )
            if next_stage1 is None:
                prev_stage1 = stage1
                prev_stage2 = stage2
                prev_stage3 = stage3
                if prev_stage1 is None and stage1_rejection is not None:
                    rejection_key = stage1_rejection.get("key")
                    rejection_timestamp = stage1_rejection.get("timestamp_ms")
                    rejection_reason = stage1_rejection.get("reason")
                    rejection_extra = stage1_rejection.get("extra")
                    if (
                        isinstance(rejection_key, tuple)
                        and rejection_timestamp is not None
                        and rejection_reason is not None
                    ):
                        _reject_stage(
                            PNO_STAGE_1_PUMP,
                            key=rejection_key,
                            timestamp_ms=int(rejection_timestamp),
                            reason=str(rejection_reason),
                            extra=rejection_extra if isinstance(rejection_extra, dict) else None,
                        )
                elif prev_stage1 is None:
                    stage1_candidate_rejection = self._resolve_stage1_pump_candidate_rejection(
                        one=one,
                        five=five,
                        idx=i,
                        five_idx=five_idx,
                        params=params,
                    )
                    if stage1_candidate_rejection is not None:
                        rejection_key = stage1_candidate_rejection.get("key")
                        rejection_timestamp = stage1_candidate_rejection.get("timestamp_ms")
                        rejection_reason = stage1_candidate_rejection.get("reason")
                        rejection_extra = stage1_candidate_rejection.get("extra")
                        if (
                            isinstance(rejection_key, tuple)
                            and rejection_timestamp is not None
                            and rejection_reason is not None
                        ):
                            _reject_stage(
                                PNO_STAGE_1_PUMP,
                                key=rejection_key,
                                timestamp_ms=int(rejection_timestamp),
                                reason=str(rejection_reason),
                                extra=rejection_extra if isinstance(rejection_extra, dict) else None,
                            )
                hold_status = self._resolve_stage1_hold_status(one=one, idx=i, stage1=prev_stage1)
                if prev_stage3 is None and prev_stage2 is not None and prev_stage1 is not None:
                    stage1 = replace(prev_stage1, hold_status_at_validation=hold_status)
                    stage2 = prev_stage2
                    stage3 = None
                    stage4 = None
                    armed = None
                    next_stage1 = stage1
                elif prev_stage3 is not None and prev_stage1 is not None:
                    stage1 = replace(prev_stage1, hold_status_at_validation=hold_status)
                    stage2 = prev_stage2
                    stage3 = prev_stage3
                    stage4 = None
                    armed = None
                    next_stage1 = stage1
                elif prev_stage2 is None and prev_stage1 is not None and hold_status == "held_above_hold" and bool(five.inplay[five_idx]):
                    # Once stage1 is confirmed, don't retroactively kill it on a transient rebuild miss
                    # while price is still holding and the in-play regime is intact.
                    stage1 = replace(prev_stage1, hold_status_at_validation=hold_status)
                    stage2 = None
                    stage3 = None
                    stage4 = None
                    armed = None
                    next_stage1 = stage1
                else:
                    stage1 = None
                    stage2 = None
                    stage3 = None
                    stage4 = None
                    armed = None
                    blocked_active_high_idx = None
                    active_pump_start_idx = -1
                    if prev_stage2 is None and prev_stage1 is not None:
                        hold_status = self._resolve_stage1_hold_status(one=one, idx=i, stage1=prev_stage1)
                        if hold_status == "closed_below_hold":
                            rejection_reason = "hold_floor_lost_before_stage2"
                        elif hold_status == "wick_below_hold":
                            rejection_reason = "hold_floor_wick_before_stage2"
                        else:
                            rejection_reason = "stage1_lost_before_stage2"
                        _reject_stage(
                            PNO_STAGE_1_PUMP,
                            key=(prev_stage1.pump_start_5m_idx, prev_stage1.active_high_idx),
                            timestamp_ms=int(one.timestamps[i]),
                            reason=rejection_reason,
                            extra={"active_high": round(prev_stage1.active_high, 8), "hold_status": hold_status},
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

            hold_status = self._resolve_stage1_hold_status(one=one, idx=i, stage1=stage1)
            leg_start_status = self._resolve_leg_start_status(five=five, five_idx=five_idx, stage1=stage1)

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
                    timestamp_ms=int(one.timestamps[i]),
                    extra={
                        "sleep_start_timestamp_ms": stage1.sleep_start_timestamp,
                        "sleep_end_timestamp_ms": stage1.sleep_end_timestamp,
                        "pump_start_timestamp_ms": stage1.pump_start_timestamp,
                        "stage1_confirm_timestamp_ms": stage1.stage1_confirm_timestamp,
                        "active_high_timestamp_ms": stage1.active_high_timestamp,
                        "current_timestamp_ms": int(one.timestamps[i]),
                        "active_high": round(stage1.active_high, 8),
                        "leg_start": round(stage1.leg_start, 8),
                        "leg_size": round(stage1.leg_size, 8),
                        "stage1_hold_price": round(stage1.stage1_hold_price, 8),
                        "hold_floor": round(stage1.hold_floor, 8),
                        "cumulative_quote_volume": round(stage1.cumulative_quote_volume, 2),
                        "pre_pump_ema_crosses_1h": int(stage1.pre_pump_ema_crosses_1h),
                        "pre_pump_barcode_fraction_1h": round(stage1.pre_pump_barcode_fraction_1h, 4),
                        "pump_impulse_atr_pre": round(stage1.pump_impulse_atr_pre, 4),
                        "pump_peak_bar_tr_atr_pre": round(stage1.pump_peak_bar_tr_atr_pre, 4),
                        "pump_volume_ratio_start": round(stage1.pump_volume_ratio_start, 4),
                        "pump_volume_ratio_continue": round(stage1.pump_volume_ratio_continue, 4),
                        "pump_path_efficiency": round(stage1.pump_path_efficiency, 4),
                        "pump_wick_share": round(stage1.pump_wick_share, 4),
                        "pump_body_share_mean": round(stage1.pump_body_share_mean, 4),
                        "pump_flat_body_share": round(stage1.pump_flat_body_share, 4),
                        "pump_body_wick_edge": round(stage1.pump_body_wick_edge, 4),
                        "pump_micro_flat_bar_share": round(stage1.pump_micro_flat_bar_share, 4),
                        "active_high_bar_body_share": round(stage1.active_high_bar_body_share, 4),
                        "active_high_bar_upper_wick_share": round(stage1.active_high_bar_upper_wick_share, 4),
                        "active_high_bar_close_position": round(stage1.active_high_bar_close_position, 4),
                        "pump_max_red_body_share_5m": round(stage1.pump_max_red_body_share_5m, 4),
                        "pump_counterflow_ratio_5m": round(stage1.pump_counterflow_ratio_5m, 4),
                        "pump_max_red_body_share_1m": round(stage1.pump_max_red_body_share_1m, 4),
                        "pump_counterflow_ratio_1m": round(stage1.pump_counterflow_ratio_1m, 4),
                        "pre_pump_range_1h": round(stage1.pre_pump_range_1h, 8),
                        "pre_pump_range_2h": round(stage1.pre_pump_range_2h, 8),
                        "pump_vs_pre_1h_ratio": round(stage1.pump_vs_pre_1h_ratio, 4),
                        "pump_vs_pre_2h_ratio": round(stage1.pump_vs_pre_2h_ratio, 4),
                    },
                )
            else:
                stage1 = next_stage1

            if blocked_active_high_idx is not None and blocked_active_high_idx == stage1.active_high_idx:
                i += 1
                continue

            next_stage2 = self._resolve_stage2_context(
                one=one,
                five=five,
                idx=i,
                five_idx=five_idx,
                stage1=stage1,
                params=params,
            )
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
                        "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                        "active_high_timestamp_ms": int(stage2.active_high_timestamp),
                        "pullback_low_timestamp_ms": int(stage2.pullback_low_timestamp),
                        "active_high": round(stage1.active_high, 8),
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
                        "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                        "active_high_timestamp_ms": int(stage2.active_high_timestamp),
                        "pullback_low_timestamp_ms": int(stage2.pullback_low_timestamp),
                        "active_high": round(stage1.active_high, 8),
                        "pullback_low": round(stage2.pullback_low, 8),
                        "pullback_depth": round(stage2.pullback_depth, 8),
                        "pullback_age_bars": int(stage2.pullback_age_bars),
                        "hold_status_at_validation": hold_status,
                        "leg_start_status_at_validation": leg_start_status,
                        "hold_floor": round(stage1.hold_floor, 8),
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
                stage1 = replace(
                    stage1,
                    hold_status_at_validation=hold_status,
                    leg_start_status_at_validation=leg_start_status,
                )
                self._mark_stage(
                    diagnostics,
                    stage_keys,
                    PNO_STAGE_3_VALID_PULLBACK,
                    key=(stage3.active_high_idx, stage3.pullback_low_idx),
                    timestamp_ms=stage3.validation_timestamp,
                    extra={
                        "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                        "active_high_timestamp_ms": int(stage3.active_high_timestamp),
                        "pullback_low_timestamp_ms": int(stage3.pullback_low_timestamp),
                        "active_high": round(stage1.active_high, 8),
                        "pullback_low": round(stage3.pullback_low, 8),
                        "pullback_depth": round(stage3.pullback_depth, 8),
                        "pullback_age_bars": int(stage3.pullback_age_bars),
                        "hold_status_at_validation": hold_status,
                        "leg_start_status_at_validation": leg_start_status,
                        "hold_floor": round(stage1.hold_floor, 8),
                        "post_high_ema20_pierce_count": int(stage3.post_high_ema20_pierce_count),
                        "post_high_close_below_ema20_count": int(stage3.post_high_close_below_ema20_count),
                        "post_high_wick_share": round(stage3.post_high_wick_share, 4),
                        "post_high_body_overlap_rate": round(stage3.post_high_body_overlap_rate, 4),
                        "post_high_max_red_body_share": round(stage3.post_high_max_red_body_share, 4),
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
                if armed is not None:
                    _reject_stage(
                        PNO_STAGE_5_TRADE,
                        key=(armed.stage4.active_high_idx, armed.stage4.cluster_first_idx, armed.stage4.cluster_last_idx, armed.entry_idx),
                        timestamp_ms=int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                        reason="entry_not_triggered",
                        extra={
                            "active_high": round(float(armed.stage4.active_high), 8),
                            "pullback_low": round(float(armed.stage3.pullback_low), 8),
                            "level": round(float(armed.stage4.level), 8),
                            "score": round(float(armed.stage4.final_score), 4),
                            "touches": int(armed.stage4.touches),
                            "entry_pos": round(float(armed.stage4.entry_pos), 4),
                            "level_maturity_fraction": round(float(armed.stage4.level_maturity_fraction), 4),
                            "pump_start_timestamp_ms": int(armed.stage1.pump_start_timestamp),
                            "active_high_timestamp_ms": int(armed.stage4.active_high_timestamp),
                            "pullback_low_timestamp_ms": int(armed.stage3.pullback_low_timestamp),
                            "level_first_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_first_idx]),
                            "level_last_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_last_idx]),
                            "level_valid_timestamp_ms": int(armed.stage4.level_valid_timestamp),
                            "level_life_ema_spread_growth_share": round(float(armed.stage4.level_life_ema_spread_growth_share), 4),
                            "entry_confirmation_mode": str(getattr(params, "entry_confirmation_mode", "cross")),
                            "entry_signal_timestamp_ms": int(one.timestamps[min(i, len(one.timestamps) - 1)]),
                            "entry_plan": round(float(armed.stage4.entry_plan), 8),
                            "sl_plan": round(float(armed.stage4.sl_plan), 8),
                            "tp1": round(float(armed.stage4.tp1), 8),
                            "tp2": round(float(armed.stage4.tp2), 8),
                        },
                    )
                    armed = None
                if i <= (stage3.pullback_low_idx + 2):
                    stage4 = None
                    i += 1
                    continue
                if stage3 is not None:
                    _reject_stage(
                        PNO_STAGE_4_LEVEL,
                        key=(stage3.active_high_idx, stage3.pullback_low_idx),
                        timestamp_ms=int(one.timestamps[i]),
                        reason="no_level_found",
                        extra={
                            "active_high": round(stage3.active_high, 8),
                            "pullback_low": round(stage3.pullback_low, 8),
                            "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                            "active_high_timestamp_ms": int(stage3.active_high_timestamp),
                            "pullback_low_timestamp_ms": int(stage3.pullback_low_timestamp),
                            "stage1_hold_price": round(float(stage1.stage1_hold_price), 8),
                            "hold_floor": round(float(stage1.hold_floor), 8),
                            "hold_status_at_level_search": str(stage1.hold_status_at_validation),
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
                            "active_high": round(float(next_stage4.active_high), 8),
                            "pullback_low": round(float(stage3.pullback_low), 8),
                            "level": round(next_stage4.level, 8),
                            "touches": int(next_stage4.touches),
                            "entry_pos": round(float(next_stage4.entry_pos), 4),
                            "score": round(float(next_stage4.final_score), 4),
                            "pullback_base_low": round(float(next_stage4.pullback_base_low), 8) if next_stage4.pullback_base_low is not None else None,
                            "pullback_base_high": round(float(next_stage4.pullback_base_high), 8) if next_stage4.pullback_base_high is not None else None,
                            "pullback_base_start_timestamp_ms": int(one.timestamps[next_stage4.pullback_base_start_idx]) if next_stage4.pullback_base_start_idx is not None else None,
                            "pullback_base_end_timestamp_ms": int(one.timestamps[next_stage4.pullback_base_end_idx]) if next_stage4.pullback_base_end_idx is not None else None,
                            "pullback_base_quality": round(float(next_stage4.pullback_base_quality), 4),
                            "overhead_resistance_score": round(float(next_stage4.overhead_resistance_score), 4),
                            "overhead_resistance_penalty": int(next_stage4.overhead_resistance_penalty),
                            "overhead_red_body_share": round(float(next_stage4.overhead_red_body_share), 4),
                            "overhead_red_count": int(next_stage4.overhead_red_count),
                            "dominant_overhead_red_timestamp_ms": int(next_stage4.dominant_overhead_red_timestamp) if next_stage4.dominant_overhead_red_timestamp is not None else None,
                            "dominant_overhead_red_high": round(float(next_stage4.dominant_overhead_red_high), 8) if next_stage4.dominant_overhead_red_high is not None else None,
                            "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                            "active_high_timestamp_ms": int(stage3.active_high_timestamp),
                            "pullback_low_timestamp_ms": int(stage3.pullback_low_timestamp),
                        "level_first_local_high_timestamp_ms": int(one.timestamps[next_stage4.cluster_first_idx]),
                        "level_last_local_high_timestamp_ms": int(one.timestamps[next_stage4.cluster_last_idx]),
                        "level_valid_timestamp_ms": int(next_stage4.level_valid_timestamp),
                        "level_life_ema_spread_growth_share": round(float(next_stage4.level_life_ema_spread_growth_share), 4),
                        "stage1_hold_price": round(float(stage1.stage1_hold_price), 8),
                        "hold_floor": round(float(stage1.hold_floor), 8),
                        "hold_status_at_level_search": str(stage1.hold_status_at_validation),
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
                    "active_high": round(float(stage4.active_high), 8),
                    "pullback_low": round(float(stage3.pullback_low), 8),
                    "level": round(stage4.level, 8),
                    "touches": stage4.touches,
                    "score": round(stage4.final_score, 4),
                    "pno_index": stage4.pno_index,
                    "level_maturity_fraction": round(stage4.level_maturity_fraction, 4),
                    "entry_pos": round(stage4.entry_pos, 4),
                    "pullback_base_low": round(float(stage4.pullback_base_low), 8) if stage4.pullback_base_low is not None else None,
                    "pullback_base_high": round(float(stage4.pullback_base_high), 8) if stage4.pullback_base_high is not None else None,
                    "pullback_base_start_timestamp_ms": int(one.timestamps[stage4.pullback_base_start_idx]) if stage4.pullback_base_start_idx is not None else None,
                    "pullback_base_end_timestamp_ms": int(one.timestamps[stage4.pullback_base_end_idx]) if stage4.pullback_base_end_idx is not None else None,
                    "pullback_base_quality": round(float(stage4.pullback_base_quality), 4),
                    "overhead_resistance_score": round(float(stage4.overhead_resistance_score), 4),
                    "overhead_resistance_penalty": int(stage4.overhead_resistance_penalty),
                    "overhead_red_body_share": round(float(stage4.overhead_red_body_share), 4),
                    "overhead_red_count": int(stage4.overhead_red_count),
                    "dominant_overhead_red_timestamp_ms": int(stage4.dominant_overhead_red_timestamp) if stage4.dominant_overhead_red_timestamp is not None else None,
                    "dominant_overhead_red_high": round(float(stage4.dominant_overhead_red_high), 8) if stage4.dominant_overhead_red_high is not None else None,
                    "pump_start_timestamp_ms": int(stage1.pump_start_timestamp),
                    "active_high_timestamp_ms": int(stage3.active_high_timestamp),
                    "pullback_low_timestamp_ms": int(stage3.pullback_low_timestamp),
                    "level_first_local_high_timestamp_ms": int(one.timestamps[stage4.cluster_first_idx]),
                    "level_last_local_high_timestamp_ms": int(one.timestamps[stage4.cluster_last_idx]),
                    "level_valid_timestamp_ms": int(stage4.level_valid_timestamp),
                    "level_life_ema_spread_growth_share": round(float(stage4.level_life_ema_spread_growth_share), 4),
                    "stage1_hold_price": round(float(stage1.stage1_hold_price), 8),
                    "hold_floor": round(float(stage1.hold_floor), 8),
                    "hold_status_at_level_search": str(stage1.hold_status_at_validation),
                },
            )
            if armed is None and stage4.is_valid_setup and i + 1 < len(one.timestamps):
                confirmation_mode = str(getattr(params, "entry_confirmation_mode", "cross"))
                arm_entry_idx = i if confirmation_mode == "close_above" else i + 1
                armed = ArmedContext(entry_idx=arm_entry_idx, stage1=stage1, stage3=stage3, stage4=stage4)
                if confirmation_mode == "close_above":
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
                                "score": float(trade.metadata.get("final_score", armed.stage4.final_score)),
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

    def _resolve_required_level_maturity_fraction(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        cluster_first_idx: int,
        cluster_indices: tuple[int, ...],
        level: float,
        params: PnoParams,
    ) -> float:
        required = float(params.level_min_maturity_fraction)
        if len(cluster_indices) != 1:
            return required
        high_price = float(one.highs[idx])
        low_price = float(one.lows[idx])
        close_price = float(one.closes[idx])
        tolerance = max(float(params.level_touch_tolerance_v1) * max(float(one.v1[idx]), self._EPSILON), self._EPSILON)
        reclaimed_above_level = high_price >= (level - self._EPSILON) and close_price > (level + self._EPSILON)
        held_near_level = low_price >= (level - tolerance)
        if reclaimed_above_level and held_near_level:
            return min(required, 0.05)
        return required

    @staticmethod
    def _is_ideal_like_impulse(*, stage1: Stage1Context, params: PnoParams) -> bool:
        if not bool(getattr(params, "ideal_like_impulse_enabled", False)):
            return False
        return bool(
            float(stage1.pump_impulse_atr_pre) >= float(params.ideal_like_min_impulse_atr_pre)
            and float(stage1.pump_peak_bar_tr_atr_pre) >= float(params.ideal_like_min_peak_bar_tr_atr_pre)
            and float(stage1.pump_volume_ratio_start) >= float(params.ideal_like_min_volume_ratio_start)
            and float(stage1.pump_path_efficiency) >= float(params.ideal_like_min_path_efficiency)
            and float(stage1.pump_wick_share) <= float(params.ideal_like_max_wick_share)
            and float(stage1.pump_body_share_mean) >= float(params.ideal_like_min_body_share_mean)
            and float(stage1.pump_body_wick_edge) >= float(params.ideal_like_min_body_wick_edge)
            and float(stage1.pump_micro_flat_bar_share) <= float(params.ideal_like_max_micro_flat_bar_share)
            and float(stage1.active_high_bar_upper_wick_share) <= float(params.ideal_like_max_active_high_upper_wick_share)
            and float(stage1.pump_counterflow_ratio_5m) <= float(params.ideal_like_max_counterflow_ratio_5m)
        )

    def _has_stale_reclaim_above_level(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage4: Stage4Context,
        params: PnoParams,
    ) -> bool:
        if len(stage4.cluster_indices) != 1:
            return False
        if float(stage4.entry_pos) <= 0.60:
            return False
        if (idx - int(stage4.level_valid_idx)) < 3:
            return False
        tolerance = max(float(params.level_touch_tolerance_v1) * max(float(one.v1[idx]), self._EPSILON), self._EPSILON)
        body_clearance = max(tolerance * 0.5, float(stage4.pullback_depth) * 0.12)
        bars_fully_above = 0
        start_idx = max(stage4.cluster_first_idx + 1, idx - 4)
        for bar_idx in range(start_idx, idx + 1):
            open_price = float(one.opens[bar_idx])
            close_price = float(one.closes[bar_idx])
            low_price = float(one.lows[bar_idx])
            body_low = min(open_price, close_price)
            if body_low > (float(stage4.level) + body_clearance) and low_price > (float(stage4.level) + self._EPSILON):
                bars_fully_above += 1
        return bars_fully_above >= 3

    def _has_deep_stale_break_below_pullback_after_level(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        stage4: Stage4Context,
    ) -> bool:
        if idx <= stage4.level_valid_idx:
            return False
        bars_since_level = idx - stage4.level_valid_idx
        if bars_since_level < 3:
            return False
        deep_break_threshold = float(stage3.pullback_low) - (0.25 * max(float(stage3.pullback_depth), self._EPSILON))
        closes = one.closes[stage4.level_valid_idx + 1 : idx + 1]
        if closes.size < 3:
            return False
        consecutive_bars = 0
        for close_price in closes:
            if float(close_price) < (deep_break_threshold - self._EPSILON):
                consecutive_bars += 1
                if consecutive_bars >= 3:
                    return True
            else:
                consecutive_bars = 0
        return False

    @staticmethod
    def _resolve_entry_pullback_fraction(*, pullback_low: float, active_high: float, entry_price: float) -> float:
        span = active_high - pullback_low
        if span <= 0.0:
            return 1.0
        return max(min((entry_price - pullback_low) / span, 1.0), 0.0)

    def _resolve_level_life_ema_spread_growth_share(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        idx: int,
    ) -> float:
        start_idx = max(int(stage4.cluster_first_idx), 0)
        end_idx = max(int(idx), start_idx)
        closes = one.closes[start_idx : end_idx + 1]
        ema9_series = one.frame["ema9"] if "ema9" in one.frame.columns else None
        ema20_series = one.frame["ema20"] if "ema20" in one.frame.columns else None
        if ema9_series is None or ema20_series is None:
            closes_series = pd.Series(one.closes[: end_idx + 1], dtype="float64")
            if ema9_series is None:
                ema9_series = closes_series.ewm(span=9, adjust=False).mean()
            if ema20_series is None:
                ema20_series = closes_series.ewm(span=20, adjust=False).mean()
        ema9 = np.asarray(ema9_series.iloc[start_idx : end_idx + 1], dtype=np.float64)
        ema20 = np.asarray(ema20_series.iloc[start_idx : end_idx + 1], dtype=np.float64)
        valid_spreads: list[float] = []
        for close_price, ema9_value, ema20_value in zip(closes, ema9, ema20):
            close_float = float(close_price)
            ema9_float = float(ema9_value)
            ema20_float = float(ema20_value)
            if (
                not np.isfinite(close_float)
                or not np.isfinite(ema9_float)
                or not np.isfinite(ema20_float)
                or close_float <= self._EPSILON
                or ema9_float <= ema20_float
            ):
                continue
            valid_spreads.append(((ema9_float - ema20_float) / close_float) * 100.0)
        if len(valid_spreads) <= 1:
            return 0.0
        non_decreasing_steps = 0
        for previous_spread, current_spread in zip(valid_spreads[:-1], valid_spreads[1:]):
            if current_spread >= (previous_spread - 1e-9):
                non_decreasing_steps += 1
        return self._safe_divide(float(non_decreasing_steps), float(len(valid_spreads) - 1))

    def _has_ideal_like_ema_spread_support(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        idx: int,
    ) -> bool:
        start_idx = max(int(stage4.cluster_first_idx), 0)
        end_idx = max(int(idx), start_idx)
        ema9_series = one.frame["ema9"] if "ema9" in one.frame.columns else None
        ema20_series = one.frame["ema20"] if "ema20" in one.frame.columns else None
        if ema9_series is None or ema20_series is None:
            closes_series = pd.Series(one.closes[: end_idx + 1], dtype="float64")
            if ema9_series is None:
                ema9_series = closes_series.ewm(span=9, adjust=False).mean()
            if ema20_series is None:
                ema20_series = closes_series.ewm(span=20, adjust=False).mean()
        spreads: list[float] = []
        for probe_idx in range(start_idx, end_idx + 1):
            ema9_value = float(ema9_series.iloc[probe_idx]) if probe_idx < len(ema9_series) else np.nan
            ema20_value = float(ema20_series.iloc[probe_idx]) if probe_idx < len(ema20_series) else np.nan
            close_value = float(one.closes[probe_idx])
            if (
                not np.isfinite(ema9_value)
                or not np.isfinite(ema20_value)
                or not np.isfinite(close_value)
                or close_value <= self._EPSILON
                or ema9_value <= ema20_value
            ):
                continue
            spreads.append(((ema9_value - ema20_value) / close_value) * 100.0)
        if len(spreads) < 3:
            return False
        growth_share = 0
        for previous_spread, current_spread in zip(spreads[:-1], spreads[1:]):
            if current_spread >= (previous_spread - 1e-9):
                growth_share += 1
        return bool(
            spreads[-1] > (spreads[0] + 1e-9)
            and self._safe_divide(float(growth_share), float(len(spreads) - 1)) >= 0.60
        )

    def _resolve_ideal_like_microstructure_stop(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        stage4: Stage4Context,
    ) -> float | None:
        if idx <= int(stage4.cluster_first_idx):
            return None
        confirmed_lows = self._resolve_confirmed_lows(
            one=one,
            start_idx=max(int(stage4.cluster_first_idx) + 1, int(stage3.pullback_low_idx)),
            end_idx=idx,
        )
        if len(confirmed_lows) == 0:
            return None
        tolerance = max(0.20 * max(float(one.v1[idx]), self._EPSILON), self._EPSILON)
        min_higher_low = float(stage3.pullback_low) + (0.08 * max(float(stage3.pullback_depth), self._EPSILON))
        for position in range(len(confirmed_lows) - 1, -1, -1):
            low_idx = int(confirmed_lows[position])
            low_price = float(one.lows[low_idx])
            previous_low = float(stage3.pullback_low)
            if position > 0:
                previous_low = float(one.lows[int(confirmed_lows[position - 1])])
            if low_price <= max(previous_low + self._EPSILON, min_higher_low):
                continue
            if float(np.max(one.highs[low_idx : idx + 1])) < (float(stage4.level) - tolerance):
                continue
            if float(np.min(one.lows[low_idx : idx + 1])) < (low_price - self._EPSILON):
                continue
            return low_price
        return None

    def _resolve_ideal_like_tp1(
        self,
        *,
        stage3: Stage3Context,
        stage4: Stage4Context,
    ) -> float:
        continuation_projection = float(stage4.level) + max(float(stage3.pullback_depth), self._EPSILON)
        return max(float(stage3.active_high), continuation_projection)

    @staticmethod
    def _is_cat_c_profile(params: PnoParams) -> bool:
        return str(getattr(params, "pno_variant_id", "")).endswith("__cat_c_category_3")

    @staticmethod
    def _is_cat_d_profile(params: PnoParams) -> bool:
        return str(getattr(params, "pno_variant_id", "")).endswith("__cat_d_category_4")

    def _has_cat_c_late_weak_active_high(
        self,
        *,
        five: FiveMinuteFrame,
        pump_start_5m_idx: int,
        active_high_5m_idx: int,
        pump_pre_atr: float,
    ) -> bool:
        if active_high_5m_idx <= (pump_start_5m_idx + 1):
            return False
        probe_slice = slice(pump_start_5m_idx, active_high_5m_idx + 1)
        tr_segment = np.asarray(five.tr[probe_slice], dtype=np.float64)
        open_segment = np.asarray(five.opens[probe_slice], dtype=np.float64)
        close_segment = np.asarray(five.closes[probe_slice], dtype=np.float64)
        high_segment = np.asarray(five.highs[probe_slice], dtype=np.float64)
        low_segment = np.asarray(five.lows[probe_slice], dtype=np.float64)
        quote_segment = np.asarray(five.quote_volume[probe_slice], dtype=np.float64)
        if tr_segment.size < 3:
            return False
        prior_peak_tr = max(float(np.nanmax(tr_segment[:-1])), self._EPSILON)
        prior_peak_quote = max(float(np.nanmax(quote_segment[:-1])), self._EPSILON)
        weak_tail = 0
        for local_idx in range(tr_segment.size - 1, 0, -1):
            bar_tr = float(tr_segment[local_idx])
            bar_range = max(float(high_segment[local_idx] - low_segment[local_idx]), self._EPSILON)
            body = abs(float(close_segment[local_idx]) - float(open_segment[local_idx]))
            body_share = self._safe_divide(body, bar_range)
            close_position = self._safe_divide(float(close_segment[local_idx]) - float(low_segment[local_idx]), bar_range)
            tr_share = self._safe_divide(bar_tr, prior_peak_tr)
            quote_share = self._safe_divide(float(quote_segment[local_idx]), prior_peak_quote)
            weak_bar = bool(
                bar_tr <= max(0.90 * float(pump_pre_atr), self._EPSILON)
                or tr_share <= 0.62
            ) and body_share <= 0.42 and close_position <= 0.82 and quote_share <= 0.72
            if not weak_bar:
                break
            weak_tail += 1
        return weak_tail >= 2

    def _resolve_pump_nonorganic_signature(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        start_idx: int,
        active_high_idx: int,
        pump_start_5m_idx: int,
        active_high_5m_idx: int,
        pump_pre_atr: float,
    ) -> dict[str, float | int]:
        one_opens = np.asarray(one.opens[start_idx : active_high_idx + 1], dtype=np.float64)
        one_closes = np.asarray(one.closes[start_idx : active_high_idx + 1], dtype=np.float64)
        one_highs = np.asarray(one.highs[start_idx : active_high_idx + 1], dtype=np.float64)
        one_lows = np.asarray(one.lows[start_idx : active_high_idx + 1], dtype=np.float64)
        one_tr = np.asarray(one.tr[start_idx : active_high_idx + 1], dtype=np.float64)
        one_v1 = max(float(one.v1[active_high_idx]), self._EPSILON)

        five_opens = np.asarray(five.opens[pump_start_5m_idx : active_high_5m_idx + 1], dtype=np.float64)
        five_closes = np.asarray(five.closes[pump_start_5m_idx : active_high_5m_idx + 1], dtype=np.float64)
        five_highs = np.asarray(five.highs[pump_start_5m_idx : active_high_5m_idx + 1], dtype=np.float64)
        five_lows = np.asarray(five.lows[pump_start_5m_idx : active_high_5m_idx + 1], dtype=np.float64)
        five_tr = np.asarray(five.tr[pump_start_5m_idx : active_high_5m_idx + 1], dtype=np.float64)
        five_v5 = max(float(five.v5[active_high_5m_idx]), self._EPSILON)

        one_gap_count = 0
        if one_opens.size >= 2:
            one_gap_threshold = max(0.60 * one_v1, 0.0030 * float(np.nanmedian(one_closes[:-1])))
            one_gap_count = int(np.sum(np.abs(one_opens[1:] - one_closes[:-1]) > one_gap_threshold))
        five_gap_count = 0
        if five_opens.size >= 2:
            five_gap_threshold = max(0.75 * max(float(pump_pre_atr), self._EPSILON), 0.0040 * float(np.nanmedian(five_closes[:-1])))
            five_gap_count = int(np.sum(np.abs(five_opens[1:] - five_closes[:-1]) > five_gap_threshold))

        one_flat_threshold = max(0.03 * one_v1, 0.00012 * float(np.nanmedian(one_closes)))
        five_flat_threshold = max(0.08 * max(float(pump_pre_atr), self._EPSILON), 0.00035 * float(np.nanmedian(five_closes)))
        one_zero_range_count = int(np.sum((one_highs - one_lows) <= self._EPSILON))
        five_zero_range_count = int(np.sum((five_highs - five_lows) <= self._EPSILON))
        one_flat_tr_share = self._safe_divide(float(np.sum(one_tr <= one_flat_threshold)), float(max(one_tr.size, 1)))
        five_flat_tr_share = self._safe_divide(float(np.sum(five_tr <= five_flat_threshold)), float(max(five_tr.size, 1)))
        return {
            "one_gap_count": one_gap_count,
            "five_gap_count": five_gap_count,
            "one_zero_range_count": one_zero_range_count,
            "five_zero_range_count": five_zero_range_count,
            "one_flat_tr_share": one_flat_tr_share,
            "five_flat_tr_share": five_flat_tr_share,
        }

    def _resolve_ideal_like_upper_tf_level_cluster(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage3: Stage3Context,
        retired_clusters: list[RetiredCluster],
        params: PnoParams,
    ) -> tuple[tuple[int, ...], tuple[float, ...]] | None:
        if five_idx <= 0:
            return None
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        tolerance = max(float(params.level_touch_tolerance_v1) * v1_now, self._EPSILON)
        pullback_span = max(float(stage3.active_high) - float(stage3.pullback_low), self._EPSILON)
        midpoint = float(stage3.pullback_low) + (0.50 * pullback_span)
        active_high_5m_idx = int(np.searchsorted(five.timestamps, int(stage3.active_high_timestamp), side="right") - 1)
        pullback_low_5m_idx = int(np.searchsorted(five.timestamps, int(stage3.pullback_low_timestamp), side="right") - 1)
        if pullback_low_5m_idx < 0 or active_high_5m_idx < 0:
            return None
        latest_available_5m_idx = min(int(five_idx), len(five.timestamps) - 1)
        if latest_available_5m_idx <= active_high_5m_idx:
            return None
        search_start = max(active_high_5m_idx + 1, 0)
        search_end = min(latest_available_5m_idx, pullback_low_5m_idx + 2)
        selected_probe_idx: int | None = None
        selected_probe_high = np.inf
        for probe_five_idx in range(search_start, search_end + 1):
            bucket_start = int(five.timestamps[probe_five_idx])
            bucket_end = bucket_start + int(params.levels_timeframe.to_milliseconds())
            one_start = int(np.searchsorted(one.timestamps, bucket_start, side="left"))
            one_end = int(np.searchsorted(one.timestamps, bucket_end, side="left"))
            if one_end <= one_start:
                continue
            local_end = min(one_end, idx + 1)
            if local_end <= one_start:
                continue
            bucket_highs = np.asarray(one.highs[one_start:local_end], dtype=np.float64)
            bucket_lows = np.asarray(one.lows[one_start:local_end], dtype=np.float64)
            bucket_opens = np.asarray(one.opens[one_start:local_end], dtype=np.float64)
            bucket_closes = np.asarray(one.closes[one_start:local_end], dtype=np.float64)
            if bucket_highs.size == 0:
                continue
            high_price = float(np.max(bucket_highs))
            if high_price >= (float(stage3.active_high) - self._EPSILON):
                continue
            low_price = float(np.min(bucket_lows))
            close_price = float(bucket_closes[-1])
            open_price = float(bucket_opens[0])
            bar_range = max(high_price - low_price, self._EPSILON)
            close_position = self._safe_divide(close_price - low_price, bar_range)
            if close_price <= (float(stage3.pullback_low) - self._EPSILON):
                continue
            if close_price > (midpoint + tolerance):
                continue
            if close_position < 0.20 and close_price <= open_price:
                continue
            if (selected_probe_idx is None) or (high_price < (selected_probe_high - self._EPSILON)):
                selected_probe_idx = probe_five_idx
                selected_probe_high = high_price
        if selected_probe_idx is None:
            return None
        probe_five_idx = int(selected_probe_idx)
        if (
            probe_five_idx == pullback_low_5m_idx
            and latest_available_5m_idx <= pullback_low_5m_idx
            and int(stage3.pullback_age_bars) < 2
        ):
            return None
        bucket_start = int(five.timestamps[probe_five_idx])
        bucket_end = bucket_start + int(params.levels_timeframe.to_milliseconds())
        one_start = int(np.searchsorted(one.timestamps, bucket_start, side="left"))
        one_end = int(np.searchsorted(one.timestamps, bucket_end, side="left"))
        if one_end <= one_start:
            return None
        local_end = min(one_end, idx + 1)
        if local_end <= one_start:
            return None
        bucket_highs = one.highs[one_start:local_end]
        if bucket_highs.size == 0:
            return None
        candidate_idx = one_start + int(np.argmax(bucket_highs))
        candidate_level = float(np.max(np.asarray(bucket_highs, dtype=np.float64)))
        if not self._is_cluster_rearm_allowed(
            active_high_idx=stage3.active_high_idx,
            candidate_level=candidate_level,
            stage3=stage3,
            retired_clusters=retired_clusters,
            v1_now=v1_now,
            rearm_min_distance_v1=float(params.level_rearm_min_distance_v1),
        ):
            return None
        return (candidate_idx,), (candidate_level,)
        return None

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
    ) -> tuple[dict[str, float | int] | None, dict[str, object] | None]:
        def _reject(reason: str, **extra: object) -> tuple[None, dict[str, object]]:
            payload: dict[str, object] = {"reason": reason}
            payload.update(extra)
            return None, payload

        levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
        stage1_start_window = self._bars_for_duration(levels_timeframe_ms, 30 * 60_000)
        pre_pump_1h_window = self._bars_for_duration(levels_timeframe_ms, 60 * 60_000)
        if pump_start_5m_idx <= 0 or pump_start_5m_idx >= len(five.timestamps):
            return None, None
        active_high_timestamp = int(one.timestamps[active_high_idx])
        active_high_5m_idx = int(np.searchsorted(five.timestamps, active_high_timestamp, side="right") - 1)
        if active_high_5m_idx < pump_start_5m_idx:
            return None, None
        one_pump_opens = one.opens[start_idx : active_high_idx + 1]
        one_pump_closes = one.closes[start_idx : active_high_idx + 1]
        # Zero-body 1m candles (open == close) are common as doji/noise even in good pumps.
        # We reject only when they are frequent or form a streak (typical for illiquid "frozen" tape).
        zero_body_mask = np.abs(one_pump_closes - one_pump_opens) <= self._EPSILON
        zero_body_bar_count_1m = int(np.sum(zero_body_mask))
        zero_body_share_1m = float(zero_body_bar_count_1m / max(1, len(zero_body_mask)))
        max_zero_body_streak_1m = 0
        if zero_body_bar_count_1m > 0:
            cur = 0
            for is_zero in zero_body_mask.tolist():
                if is_zero:
                    cur += 1
                    if cur > max_zero_body_streak_1m:
                        max_zero_body_streak_1m = cur
                else:
                    cur = 0
        recent_zero_body_window = min(len(zero_body_mask), 48)
        recent_zero_body_mask = zero_body_mask[-recent_zero_body_window:] if recent_zero_body_window > 0 else zero_body_mask
        recent_zero_body_bar_count_1m = int(np.sum(recent_zero_body_mask))
        recent_zero_body_share_1m = float(recent_zero_body_bar_count_1m / max(1, recent_zero_body_window))
        recent_max_zero_body_streak_1m = 0
        if recent_zero_body_bar_count_1m > 0:
            cur = 0
            for is_zero in recent_zero_body_mask.tolist():
                if is_zero:
                    cur += 1
                    if cur > recent_max_zero_body_streak_1m:
                        recent_max_zero_body_streak_1m = cur
                else:
                    cur = 0
        zero_body_exception = bool(
            getattr(params, "ideal_like_impulse_enabled", False)
            and (
                (
                    one_pump_opens.size <= 48
                    and zero_body_bar_count_1m <= 36
                    and zero_body_share_1m <= 0.60
                    and max_zero_body_streak_1m <= 10
                )
                or (
                    recent_zero_body_window >= 24
                    and recent_zero_body_bar_count_1m <= 36
                    and recent_zero_body_share_1m <= 0.60
                    and recent_max_zero_body_streak_1m <= 10
                )
            )
        )
        if (
            ((max_zero_body_streak_1m >= 3) or (zero_body_bar_count_1m >= 3 and zero_body_share_1m >= 0.15))
            and not zero_body_exception
        ):
            return _reject(
                "zero_body_bar_present_1m",
                zero_body_bar_count_1m=zero_body_bar_count_1m,
                zero_body_share_1m=zero_body_share_1m,
                max_zero_body_streak_1m=max_zero_body_streak_1m,
            )

        reference_high, reference_high_weight = self._resolve_reference_high(
            five=five,
            pump_start_idx=pump_start_5m_idx,
            active_high_idx=active_high_5m_idx,
            params=params,
        )

        pump_pre_atr = float(five.atr_pre_14[pump_start_5m_idx])
        baseline_quote = float(five.pre_quote_median_24[pump_start_5m_idx])
        if not np.isfinite(pump_pre_atr) or pump_pre_atr <= 0.0:
            return None, None
        if not np.isfinite(baseline_quote) or baseline_quote <= 0.0:
            return None, None
        nonorganic_signature = self._resolve_pump_nonorganic_signature(
            one=one,
            five=five,
            start_idx=start_idx,
            active_high_idx=active_high_idx,
            pump_start_5m_idx=pump_start_5m_idx,
            active_high_5m_idx=active_high_5m_idx,
            pump_pre_atr=pump_pre_atr,
        )
        if (
            int(nonorganic_signature["five_gap_count"]) >= 1
            or int(nonorganic_signature["one_gap_count"]) >= 2
            or int(nonorganic_signature["one_zero_range_count"]) >= 2
            or int(nonorganic_signature["five_zero_range_count"]) >= 1
            or float(nonorganic_signature["one_flat_tr_share"]) >= 0.30
            or float(nonorganic_signature["five_flat_tr_share"]) >= 0.40
        ):
            return _reject(
                "pump_nonorganic_tape",
                **{
                    key: round(float(value), 4) if isinstance(value, float) else int(value)
                    for key, value in nonorganic_signature.items()
                },
            )

        if self._is_cat_c_profile(params) and self._has_cat_c_late_weak_active_high(
            five=five,
            pump_start_5m_idx=pump_start_5m_idx,
            active_high_5m_idx=active_high_5m_idx,
            pump_pre_atr=pump_pre_atr,
        ):
            return _reject(
                "active_high_formed_on_weak_tail",
                pump_start_timestamp_ms=int(five.timestamps[pump_start_5m_idx]),
                active_high_timestamp_ms=int(five.timestamps[active_high_5m_idx]),
            )

        pump_highs = five.highs[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_opens = five.opens[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_lows = five.lows[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_closes = five.closes[pump_start_5m_idx : active_high_5m_idx + 1]
        pump_tr = five.tr[pump_start_5m_idx : active_high_5m_idx + 1]
        if pump_highs.size == 0 or pump_tr.size == 0:
            return None, None

        low_before_pump = float(five.lows[pump_start_5m_idx - 1])
        pump_impulse = float(np.max(pump_highs)) - low_before_pump
        pump_impulse_atr_pre = self._safe_divide(pump_impulse, pump_pre_atr)
        pump_peak_bar_tr_atr_pre = self._safe_divide(float(np.max(pump_tr)), pump_pre_atr)
        if pump_impulse_atr_pre < float(params.stage1_min_impulse_atr_pre):
            return _reject(
                "impulse_atr_pre_too_small",
                pump_impulse_atr_pre=round(pump_impulse_atr_pre, 4),
                stage1_min_impulse_atr_pre=round(float(params.stage1_min_impulse_atr_pre), 4),
            )
        if pump_peak_bar_tr_atr_pre < float(params.stage1_min_peak_bar_tr_atr_pre):
            return _reject(
                "peak_bar_tr_atr_pre_too_small",
                pump_peak_bar_tr_atr_pre=round(pump_peak_bar_tr_atr_pre, 4),
                stage1_min_peak_bar_tr_atr_pre=round(float(params.stage1_min_peak_bar_tr_atr_pre), 4),
            )

        start_window_end = min(five_idx, pump_start_5m_idx + stage1_start_window)
        pump_start_quote = float(np.mean(five.quote_volume[pump_start_5m_idx : start_window_end + 1]))
        pump_continue_quote = float(np.mean(five.quote_volume[pump_start_5m_idx : five_idx + 1]))
        pump_volume_ratio_start = self._safe_divide(pump_start_quote, baseline_quote)
        pump_volume_ratio_continue = self._safe_divide(pump_continue_quote, baseline_quote)
        if pump_volume_ratio_start < float(params.stage1_min_volume_ratio_start):
            return _reject(
                "volume_ratio_start_too_small",
                pump_volume_ratio_start=round(pump_volume_ratio_start, 4),
                stage1_min_volume_ratio_start=round(float(params.stage1_min_volume_ratio_start), 4),
            )
        if pump_volume_ratio_continue < float(params.stage1_min_volume_ratio_continue):
            return _reject(
                "volume_ratio_continue_too_small",
                pump_volume_ratio_continue=round(pump_volume_ratio_continue, 4),
                stage1_min_volume_ratio_continue=round(float(params.stage1_min_volume_ratio_continue), 4),
            )

        pump_net_move = max(float(pump_closes[-1]) - float(pump_opens[0]), 0.0)
        pump_path_efficiency = self._safe_divide(pump_net_move, float(np.sum(pump_tr)))
        pump_body_share_mean = float(np.mean(np.abs(pump_closes - pump_opens) / np.maximum(pump_tr, self._EPSILON)))
        pump_wick_share = self._safe_divide(
            float(
                np.sum(
                    (pump_highs - np.maximum(pump_opens, pump_closes))
                    + (np.minimum(pump_opens, pump_closes) - pump_lows)
                )
            ),
            float(np.sum(pump_tr)),
        )
        flat_body_threshold = np.maximum(0.18 * pump_tr, 0.08 * pump_pre_atr)
        pump_flat_body_share = float(np.mean(np.abs(pump_closes - pump_opens) <= flat_body_threshold))
        pump_body_wick_edge = pump_body_share_mean - pump_wick_share
        pump_body_share = np.abs(pump_closes - pump_opens) / np.maximum(pump_tr, self._EPSILON)
        micro_flat_threshold = np.maximum(0.45 * pump_pre_atr, 0.10 * leg_size)
        pump_micro_flat_bar_share = float(
            np.mean((pump_body_share <= 0.12) & (pump_tr <= micro_flat_threshold))
        )
        high_bar_open = float(five.opens[active_high_5m_idx])
        high_bar_high = float(five.highs[active_high_5m_idx])
        high_bar_low = float(five.lows[active_high_5m_idx])
        high_bar_close = float(five.closes[active_high_5m_idx])
        high_bar_range = max(high_bar_high - high_bar_low, self._EPSILON)
        active_high_bar_body_share = abs(high_bar_close - high_bar_open) / high_bar_range
        active_high_bar_upper_wick_share = (high_bar_high - max(high_bar_open, high_bar_close)) / high_bar_range
        active_high_bar_close_position = (high_bar_close - high_bar_low) / high_bar_range
        red_bodies_5m = np.maximum(pump_opens - pump_closes, 0.0)
        green_bodies_5m = np.maximum(pump_closes - pump_opens, 0.0)
        pump_max_red_body_share_5m = self._safe_divide(float(np.max(red_bodies_5m)), leg_size)
        pump_counterflow_ratio_5m = self._safe_divide(
            float(np.sum(red_bodies_5m)),
            float(np.sum(green_bodies_5m)),
        )
        if one_pump_opens.size == 0 or one_pump_closes.size == 0:
            return None
        red_bodies_1m = np.maximum(one_pump_opens - one_pump_closes, 0.0)
        green_bodies_1m = np.maximum(one_pump_closes - one_pump_opens, 0.0)
        pump_max_red_body_share_1m = self._safe_divide(float(np.max(red_bodies_1m)), leg_size)
        pump_counterflow_ratio_1m = self._safe_divide(
            float(np.sum(red_bodies_1m)),
            float(np.sum(green_bodies_1m)),
        )
        if pump_path_efficiency < float(params.stage1_min_path_efficiency):
            return _reject(
                "path_efficiency_too_low",
                pump_path_efficiency=round(pump_path_efficiency, 4),
                stage1_min_path_efficiency=round(float(params.stage1_min_path_efficiency), 4),
            )
        if pump_wick_share > float(params.stage1_max_wick_share):
            return _reject(
                "wick_share_too_high",
                pump_wick_share=round(pump_wick_share, 4),
                stage1_max_wick_share=round(float(params.stage1_max_wick_share), 4),
            )
        if pump_body_share_mean < float(params.stage1_min_body_share_mean):
            return _reject(
                "body_share_mean_too_low",
                pump_body_share_mean=round(pump_body_share_mean, 4),
                stage1_min_body_share_mean=round(float(params.stage1_min_body_share_mean), 4),
            )
        if pump_flat_body_share > float(params.stage1_max_flat_body_share):
            return _reject(
                "flat_body_share_too_high",
                pump_flat_body_share=round(pump_flat_body_share, 4),
                stage1_max_flat_body_share=round(float(params.stage1_max_flat_body_share), 4),
            )
        if pump_body_wick_edge < float(params.stage1_min_body_wick_edge):
            return _reject(
                "body_wick_edge_too_low",
                pump_body_wick_edge=round(pump_body_wick_edge, 4),
                stage1_min_body_wick_edge=round(float(params.stage1_min_body_wick_edge), 4),
            )
        if pump_micro_flat_bar_share > float(params.stage1_max_micro_flat_bar_share):
            return _reject(
                "micro_flat_bar_share_too_high",
                pump_micro_flat_bar_share=round(pump_micro_flat_bar_share, 4),
                stage1_max_micro_flat_bar_share=round(float(params.stage1_max_micro_flat_bar_share), 4),
            )
        if active_high_bar_upper_wick_share > float(params.stage1_max_active_high_upper_wick_share):
            return _reject(
                "active_high_upper_wick_too_high",
                active_high_bar_upper_wick_share=round(active_high_bar_upper_wick_share, 4),
                stage1_max_active_high_upper_wick_share=round(float(params.stage1_max_active_high_upper_wick_share), 4),
            )
        if pump_max_red_body_share_5m > float(params.stage1_max_red_body_share_5m):
            return _reject(
                "red_body_share_5m_too_high",
                pump_max_red_body_share_5m=round(pump_max_red_body_share_5m, 4),
                stage1_max_red_body_share_5m=round(float(params.stage1_max_red_body_share_5m), 4),
            )
        if pump_counterflow_ratio_5m > float(params.stage1_max_counterflow_ratio_5m):
            return _reject(
                "counterflow_ratio_5m_too_high",
                pump_counterflow_ratio_5m=round(pump_counterflow_ratio_5m, 4),
                stage1_max_counterflow_ratio_5m=round(float(params.stage1_max_counterflow_ratio_5m), 4),
            )
        if pump_max_red_body_share_1m > float(params.stage1_max_red_body_share_1m):
            return _reject(
                "red_body_share_1m_too_high",
                pump_max_red_body_share_1m=round(pump_max_red_body_share_1m, 4),
                stage1_max_red_body_share_1m=round(float(params.stage1_max_red_body_share_1m), 4),
            )
        if pump_counterflow_ratio_1m > float(params.stage1_max_counterflow_ratio_1m):
            return _reject(
                "counterflow_ratio_1m_too_high",
                pump_counterflow_ratio_1m=round(pump_counterflow_ratio_1m, 4),
                stage1_max_counterflow_ratio_1m=round(float(params.stage1_max_counterflow_ratio_1m), 4),
            )

        cumulative_quote_volume = self._range_sum(one.cumulative_quote_volume, start_idx, idx)
        if cumulative_quote_volume < float(params.stage1_min_cumulative_quote_volume):
            return _reject(
                "cumulative_quote_volume_too_low",
                cumulative_quote_volume=round(cumulative_quote_volume, 2),
                stage1_min_cumulative_quote_volume=round(float(params.stage1_min_cumulative_quote_volume), 2),
            )

        pre_pump_ema_crosses_1h = int(round(float(five.ema_cross_count_1h[pump_start_5m_idx])))
        allows_zero_pre_pump_ema_crosses = self._allows_zero_pre_pump_ema_crosses(
            five=five,
            pump_start_5m_idx=pump_start_5m_idx,
            current_5m_idx=five_idx,
            params=params,
            pump_volume_ratio_start=pump_volume_ratio_start,
            pump_impulse_atr_pre=pump_impulse_atr_pre,
            pump_path_efficiency=pump_path_efficiency,
            pump_body_wick_edge=pump_body_wick_edge,
        )
        if (
            pre_pump_ema_crosses_1h < int(params.stage1_pre_pump_ema_crosses_min)
            and not allows_zero_pre_pump_ema_crosses
        ):
            return _reject(
                "pre_pump_ema_crosses_too_low",
                pre_pump_ema_crosses_1h=int(pre_pump_ema_crosses_1h),
                stage1_pre_pump_ema_crosses_min=int(params.stage1_pre_pump_ema_crosses_min),
            )

        pre_start_idx = max(0, pump_start_5m_idx - pre_pump_1h_window)
        pre_tr = five.tr[pre_start_idx:pump_start_5m_idx]
        pre_closes = five.closes[pre_start_idx:pump_start_5m_idx]
        if pre_tr.size == 0 or pre_closes.size == 0:
            return None, None
        median_pre_close = float(np.median(pre_closes))
        barcode_threshold = max(
            float(params.stage1_barcode_tr_atr_fraction) * pump_pre_atr,
            float(params.stage1_barcode_tr_price_fraction) * median_pre_close,
        )
        pre_pump_barcode_fraction_1h = float(np.mean(pre_tr <= barcode_threshold))
        if pre_pump_barcode_fraction_1h > float(params.stage1_barcode_max_fraction_1h):
            return _reject(
                "barcode_fraction_too_high",
                pre_pump_barcode_fraction_1h=round(pre_pump_barcode_fraction_1h, 4),
                stage1_barcode_max_fraction_1h=round(float(params.stage1_barcode_max_fraction_1h), 4),
            )

        pre_pump_high_24h = float(five.pre_high_24h[pump_start_5m_idx])
        if not np.isfinite(pre_pump_high_24h):
            return None, None
        if pre_pump_high_24h > (active_high + self._EPSILON):
            return _reject(
                "pre_pump_high_24h_above_active_high",
                pre_pump_high_24h=round(pre_pump_high_24h, 8),
                active_high=round(active_high, 8),
            )

        pre_pump_high_1h = float(five.pre_high_1h[pump_start_5m_idx])
        if not np.isfinite(pre_pump_high_1h):
            return None, None
        half_leg_level = leg_start + (float(params.stage1_pre_pump_high_max_fraction_of_leg) * leg_size)
        if pre_pump_high_1h > (half_leg_level + self._EPSILON):
            return _reject(
                "pre_pump_high_1h_too_high",
                pre_pump_high_1h=round(pre_pump_high_1h, 8),
                stage1_pre_pump_high_cap=round(half_leg_level, 8),
            )

        return (
            {
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
                "pump_path_efficiency": pump_path_efficiency,
                "pump_wick_share": pump_wick_share,
                "pump_body_share_mean": pump_body_share_mean,
                "pump_flat_body_share": pump_flat_body_share,
                "pump_body_wick_edge": pump_body_wick_edge,
                "pump_micro_flat_bar_share": pump_micro_flat_bar_share,
                "active_high_bar_body_share": active_high_bar_body_share,
                "active_high_bar_upper_wick_share": active_high_bar_upper_wick_share,
                "active_high_bar_close_position": active_high_bar_close_position,
                "pump_max_red_body_share_5m": pump_max_red_body_share_5m,
                "pump_counterflow_ratio_5m": pump_counterflow_ratio_5m,
                "pump_max_red_body_share_1m": pump_max_red_body_share_1m,
                "pump_counterflow_ratio_1m": pump_counterflow_ratio_1m,
                "reference_high": reference_high,
                "reference_high_weight": reference_high_weight,
            },
            None,
        )

    @staticmethod
    def _allows_zero_pre_pump_ema_crosses(
        *,
        five: FiveMinuteFrame,
        pump_start_5m_idx: int,
        current_5m_idx: int,
        params: PnoParams,
        pump_volume_ratio_start: float,
        pump_impulse_atr_pre: float,
        pump_path_efficiency: float,
        pump_body_wick_edge: float,
    ) -> bool:
        if int(params.stage1_pre_pump_ema_crosses_min) > 1:
            return False
        if pump_start_5m_idx <= 0 or current_5m_idx < pump_start_5m_idx or current_5m_idx >= len(five.sleep_end_idx):
            return False
        if int(five.sleep_end_idx[current_5m_idx]) != (pump_start_5m_idx - 1):
            return False
        if pump_volume_ratio_start < max(float(params.stage1_min_volume_ratio_start), 5.0):
            return False
        if pump_impulse_atr_pre < max(float(params.stage1_min_impulse_atr_pre), 5.0):
            return False
        if pump_path_efficiency < max(float(params.stage1_min_path_efficiency), 0.5):
            return False
        if pump_body_wick_edge < max(float(params.stage1_min_body_wick_edge), 0.15):
            return False
        return True

    def _resolve_stage1_context(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        params: PnoParams,
    ) -> tuple[Stage1Context | None, dict[str, object] | None]:
        levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
        candidate = self._resolve_htf_stage1_candidate_from_arrays(
            timestamps=five.timestamps,
            highs=five.highs,
            lows=five.lows,
            five_idx=five_idx,
            params=params,
        )
        if candidate is None:
            return None, None

        start_timestamp = int(candidate["pump_start_timestamp"])
        start_idx = int(np.searchsorted(one.timestamps, start_timestamp, side="left"))
        active_high_timestamp = int(candidate["active_high_timestamp"])
        active_high_window_end = active_high_timestamp + levels_timeframe_ms
        active_high_start_idx = int(np.searchsorted(one.timestamps, active_high_timestamp, side="left"))
        active_high_end_idx = int(np.searchsorted(one.timestamps, active_high_window_end, side="left"))
        if active_high_end_idx > active_high_start_idx:
            active_high_idx = int(
                active_high_start_idx
                + np.argmax(one.highs[active_high_start_idx:active_high_end_idx])
            )
        else:
            active_high_idx = int(np.searchsorted(one.timestamps, active_high_timestamp, side="right") - 1)
        if start_idx < 0 or start_idx >= len(one.timestamps) or active_high_idx <= start_idx or active_high_idx >= len(one.timestamps):
            return None, None
        if idx <= active_high_idx:
            return None, None

        leg_start_idx = int(start_idx + np.argmin(one.lows[start_idx : active_high_idx + 1]))
        leg_start = float(one.lows[leg_start_idx])
        active_high = float(candidate["active_high"])
        leg_size = active_high - leg_start
        min_leg = max(
            float(params.min_stage1_leg_v1) * max(float(one.v1[idx]), self._EPSILON),
            float(params.min_stage1_leg_v5_fraction) * max(float(five.v5[five_idx]), self._EPSILON),
        )
        if leg_size < max(min_leg, self._EPSILON):
            return None, None

        htf_pullback_low = float(candidate["pullback_low"])
        min_allowed_low = float(candidate["min_allowed_low"])
        if htf_pullback_low < (min_allowed_low - self._EPSILON):
            return None, None
        if float(one.lows[idx]) < (min_allowed_low - self._EPSILON):
            return (
                None,
                {
                    "reason": "pullback_below_min_allowed_low",
                    "key": (int(candidate["pump_start_5m_idx"]), int(candidate["pullback_end_5m_idx"])),
                    "timestamp_ms": int(one.timestamps[idx]),
                    "extra": {
                        "active_high": round(active_high, 8),
                        "leg_start": round(leg_start, 8),
                        "min_allowed_low": round(min_allowed_low, 8),
                        "current_low": round(float(one.lows[idx]), 8),
                    },
                },
            )

        pump_range = self._resolve_window_range(
            highs=five.highs,
            lows=five.lows,
            start_idx=int(candidate["pump_start_5m_idx"]),
            end_idx=int(candidate["pullback_end_5m_idx"]),
        )
        stage1_hold_price = float(candidate["level"])
        hold_floor = min_allowed_low
        allowed_pullback_end_5m_idx = min(
            len(five.timestamps) - 1,
            int(candidate["active_high_5m_idx"]) + int(params.stage1_pullback_max_bars),
        )
        cumulative_quote_volume = self._range_sum(
            five.cumulative_quote_volume,
            int(candidate["pump_start_5m_idx"]),
            int(candidate["pullback_end_5m_idx"]),
        )
        return (
            Stage1Context(
                start_idx=start_idx,
                start_timestamp=int(one.timestamps[start_idx]),
                levels_timeframe_ms=levels_timeframe_ms,
                sleep_start_timestamp=int(five.timestamps[max(int(candidate["pump_start_5m_idx"]) - 1, 0)]),
                sleep_end_timestamp=int(five.timestamps[max(int(candidate["pump_start_5m_idx"]) - 1, 0)]),
                pump_start_5m_idx=int(candidate["pump_start_5m_idx"]),
                pump_start_timestamp=start_timestamp,
                stage1_confirm_timestamp=int(candidate["level_timestamp"]),
                current_5m_idx=five_idx,
                current_levels_timestamp=int(five.timestamps[five_idx]),
                active_high_idx=active_high_idx,
                active_high_timestamp=int(one.timestamps[active_high_idx]),
                active_high=active_high,
                reference_high=active_high,
                leg_start_idx=leg_start_idx,
                leg_start_timestamp=int(one.timestamps[leg_start_idx]),
                leg_start=leg_start,
                leg_size=leg_size,
                reference_leg_size=leg_size,
                pump_range_5m=pump_range,
                hold_floor=hold_floor,
                stage1_hold_price=stage1_hold_price,
                cumulative_quote_volume=float(cumulative_quote_volume),
                active_high_5m_idx=int(candidate["active_high_5m_idx"]),
                htf_pullback_start_5m_idx=int(candidate["pullback_start_5m_idx"]),
                htf_pullback_end_5m_idx=int(allowed_pullback_end_5m_idx),
                htf_pullback_low_5m_idx=int(candidate["pullback_low_5m_idx"]),
                htf_pullback_low=htf_pullback_low,
                htf_level=float(candidate["level"]),
                htf_level_timestamp=int(candidate["level_timestamp"]),
                htf_pump_bars=int(candidate["pump_bars"]),
                htf_pullback_bars=int(candidate["pullback_bars"]),
                htf_min_allowed_low=min_allowed_low,
            ),
            None,
        )

    def _resolve_stage2_context(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage1: Stage1Context,
        params: PnoParams,
    ) -> Stage2Context | None:
        active_high_5m_idx = int(stage1.active_high_5m_idx)
        if active_high_5m_idx < 0 or five_idx <= active_high_5m_idx:
            return None
        if five_idx > int(stage1.htf_pullback_end_5m_idx):
            return None
        pullback_start_timestamp = int(five.timestamps[active_high_5m_idx + 1])
        pullback_start_idx = int(np.searchsorted(one.timestamps, pullback_start_timestamp, side="left"))
        if pullback_start_idx >= idx:
            return None
        pullback_low_idx = int(pullback_start_idx + np.argmin(one.lows[pullback_start_idx : idx + 1]))
        pullback_low = float(one.lows[pullback_low_idx])
        pullback_depth = stage1.reference_high - pullback_low
        min_pullback_from_pump = float(params.pullback_min_pump_fraction_5m) * max(stage1.leg_size, self._EPSILON)
        if pullback_depth < min_pullback_from_pump:
            return None
        structure = self._resolve_pullback_structure(
            one=one,
            start_idx=stage1.active_high_idx,
            end_idx=idx,
            params=params,
        )
        return Stage2Context(
            active_high_idx=stage1.active_high_idx,
            active_high_timestamp=stage1.active_high_timestamp,
            active_high=stage1.active_high,
            red_after_high_idx=pullback_start_idx,
            pullback_start_idx=pullback_start_idx,
            pullback_low_idx=pullback_low_idx,
            pullback_low_timestamp=int(one.timestamps[pullback_low_idx]),
            pullback_low=pullback_low,
            pullback_depth=pullback_depth,
            pullback_age_bars=(five_idx - active_high_5m_idx),
            structure_high_idx=int(structure["last_high_idx"]) if structure is not None else -1,
            structure_high_timestamp=int(structure["last_high_timestamp"]) if structure is not None else 0,
            structure_high=float(structure["last_high"]) if structure is not None else 0.0,
            structure_low_idx=int(structure["last_low_idx"]) if structure is not None else -1,
            structure_low_timestamp=int(structure["last_low_timestamp"]) if structure is not None else 0,
            structure_low=float(structure["last_low"]) if structure is not None else 0.0,
            structure_pivot_indices=tuple(structure["pivot_indices"]) if structure is not None else (),
            structure_pivot_prices=tuple(structure["pivot_prices"]) if structure is not None else (),
            structure_pivot_kinds=tuple(structure["pivot_kinds"]) if structure is not None else (),
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
        active_high_5m_idx = int(stage1.active_high_5m_idx)
        if active_high_5m_idx < 0 or five_idx <= active_high_5m_idx:
            return None, None
        if five_idx > int(stage1.htf_pullback_end_5m_idx):
            return None, "htf_pullback_limit_reached"
        if float(one.lows[idx]) < (float(stage1.htf_min_allowed_low) - self._EPSILON):
            return None, "price_too_low"
        max_breakout_close = float(stage1.active_high) + (
            float(params.structure_max_breakout_extension_fraction)
            * max(float(stage2.pullback_depth), self._EPSILON)
        )
        if float(one.closes[idx]) > (max_breakout_close + self._EPSILON):
            return None, "price_too_high"
        close_broke_leg_start = bool(
            np.any(
                one.closes[stage2.pullback_start_idx : idx + 1]
                < (stage1.leg_start - self._EPSILON)
            )
        )
        wick_broke_leg_start = bool(stage2.pullback_low <= (stage1.leg_start + self._EPSILON))
        structure = self._resolve_pullback_structure(
            one=one,
            start_idx=stage1.active_high_idx,
            end_idx=idx,
            params=params,
        )
        if structure is None or int(structure["break_idx"]) != idx:
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
                pullback_wick_broke_leg_start=wick_broke_leg_start,
                pullback_close_broke_leg_start=close_broke_leg_start,
                structure_high_idx=int(structure["last_high_idx"]),
                structure_high_timestamp=int(structure["last_high_timestamp"]),
                structure_high=float(structure["last_high"]),
                structure_low_idx=int(structure["last_low_idx"]),
                structure_low_timestamp=int(structure["last_low_timestamp"]),
                structure_low=float(structure["last_low"]),
                structure_break_idx=int(structure["break_idx"]),
                structure_break_timestamp=int(one.timestamps[idx]),
                structure_break_close=float(one.closes[idx]),
                structure_pivot_indices=tuple(structure["pivot_indices"]),
                structure_pivot_prices=tuple(structure["pivot_prices"]),
                structure_pivot_kinds=tuple(structure["pivot_kinds"]),
            ),
            None,
        )

    def _resolve_pullback_structure(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
        params: PnoParams,
    ) -> dict[str, object] | None:
        if end_idx <= start_idx or start_idx < 0 or end_idx >= len(one.timestamps):
            return None

        v1_now = max(float(one.v1[end_idx]), self._EPSILON)
        reversal_threshold = max(
            float(params.structure_reversal_min_v1_fraction) * v1_now,
            float(one.closes[end_idx]) * float(params.min_tick_fraction) * 4.0,
            self._EPSILON,
        )
        min_body_fraction = float(params.structure_reversal_min_body_fraction)
        confirmation_end_idx = int(end_idx - 1)
        pivot_indices: list[int] = [int(start_idx)]
        pivot_prices: list[float] = [float(one.highs[start_idx])]
        pivot_kinds: list[str] = ["H"]
        direction = "down"
        swing_idx = int(start_idx + 1)
        if swing_idx > confirmation_end_idx:
            return None

        def _append_pivot(kind: str, pivot_idx: int, pivot_price: float) -> None:
            if pivot_idx <= pivot_indices[-1]:
                return
            if pivot_kinds[-1] == kind:
                if kind == "H" and pivot_price > pivot_prices[-1]:
                    pivot_indices[-1] = int(pivot_idx)
                    pivot_prices[-1] = float(pivot_price)
                elif kind == "L" and pivot_price < pivot_prices[-1]:
                    pivot_indices[-1] = int(pivot_idx)
                    pivot_prices[-1] = float(pivot_price)
                return
            pivot_indices.append(int(pivot_idx))
            pivot_prices.append(float(pivot_price))
            pivot_kinds.append(kind)

        for probe_idx in range(start_idx + 1, confirmation_end_idx + 1):
            bar_range = max(float(one.highs[probe_idx]) - float(one.lows[probe_idx]), self._EPSILON)
            body_share = self._safe_divide(abs(float(one.closes[probe_idx]) - float(one.opens[probe_idx])), bar_range)
            if direction == "down":
                if float(one.lows[probe_idx]) <= float(one.lows[swing_idx]):
                    swing_idx = int(probe_idx)
                rebound = float(one.highs[probe_idx]) - float(one.lows[swing_idx])
                if probe_idx > swing_idx and rebound >= reversal_threshold and body_share >= min_body_fraction:
                    _append_pivot("L", int(swing_idx), float(one.lows[swing_idx]))
                    direction = "up"
                    swing_idx = int(probe_idx)
            else:
                if float(one.highs[probe_idx]) >= float(one.highs[swing_idx]):
                    swing_idx = int(probe_idx)
                rollback = float(one.highs[swing_idx]) - float(one.lows[probe_idx])
                if probe_idx > swing_idx and rollback >= reversal_threshold and body_share >= min_body_fraction:
                    _append_pivot("H", int(swing_idx), float(one.highs[swing_idx]))
                    direction = "down"
                    swing_idx = int(probe_idx)

        if len(pivot_indices) < 2:
            return None

        last_low_pos = -1
        for pos in range(len(pivot_kinds) - 1, -1, -1):
            if pivot_kinds[pos] == "L":
                last_low_pos = pos
                break
        if last_low_pos <= 0:
            return None
        last_high_pos = -1
        for pos in range(last_low_pos - 1, -1, -1):
            if pivot_kinds[pos] == "H":
                last_high_pos = pos
                break
        if last_high_pos < 0:
            return None

        last_high_idx = int(pivot_indices[last_high_pos])
        last_high = float(pivot_prices[last_high_pos])
        last_low_idx = int(pivot_indices[last_low_pos])
        last_low = float(pivot_prices[last_low_pos])
        breakout_buffer = float(params.structure_break_min_close_v1_fraction) * v1_now
        breakout_close = float(one.closes[end_idx])
        breakout_close_position = self._safe_divide(
            breakout_close - float(one.lows[end_idx]),
            max(float(one.highs[end_idx]) - float(one.lows[end_idx]), self._EPSILON),
        )
        break_idx = -1
        if (
            last_low_pos == (len(pivot_kinds) - 1)
            and
            end_idx > last_low_idx
            and breakout_close > (last_high + breakout_buffer)
            and breakout_close_position >= float(params.structure_break_min_close_position)
        ):
            break_idx = int(end_idx)

        return {
            "pivot_indices": tuple(int(item) for item in pivot_indices),
            "pivot_prices": tuple(float(item) for item in pivot_prices),
            "pivot_kinds": tuple(str(item) for item in pivot_kinds),
            "last_high_idx": int(last_high_idx),
            "last_high_timestamp": int(one.timestamps[last_high_idx]),
            "last_high": float(last_high),
            "last_low_idx": int(last_low_idx),
            "last_low_timestamp": int(one.timestamps[last_low_idx]),
            "last_low": float(last_low),
            "break_idx": int(break_idx),
        }

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
        ideal_like_impulse = self._is_ideal_like_impulse(stage1=stage1, params=params)
        if idx <= stage3.pullback_start_idx:
            return None

        v1_now = float(one.v1[idx])
        if not np.isfinite(v1_now) or v1_now <= 0.0:
            return None

        if stage3.structure_high_idx >= 0 and stage3.structure_low_idx >= 0:
            slip_plan = max(float(stage3.structure_high) * float(params.min_tick_fraction), float(params.slip_plan_v1_fraction) * v1_now)
            entry_plan = float(stage3.structure_high) + slip_plan
            tp1 = float(stage3.active_high)
            tp2 = self._resolve_tp2(
                active_high=tp1,
                entry_price=entry_plan,
                pullback_height=float(stage3.pullback_depth),
                v1=v1_now,
            )
            return Stage4Context(
                active_high_idx=stage3.active_high_idx,
                active_high_timestamp=stage3.active_high_timestamp,
                active_high=stage3.active_high,
                pullback_low_idx=stage3.pullback_low_idx,
                pullback_low_timestamp=stage3.pullback_low_timestamp,
                pullback_low=stage3.pullback_low,
                pullback_depth=stage3.pullback_depth,
                cluster_indices=(int(stage3.structure_high_idx),),
                cluster_prices=(float(stage3.structure_high),),
                level=float(stage3.structure_high),
                level_pos=self._resolve_entry_pullback_fraction(
                    pullback_low=float(stage3.structure_low),
                    active_high=float(stage3.active_high),
                    entry_price=float(stage3.structure_high),
                ),
                touches=1,
                cluster_first_idx=int(stage3.structure_high_idx),
                cluster_last_idx=int(stage3.structure_high_idx),
                level_valid_idx=int(stage3.structure_break_idx),
                level_valid_timestamp=int(stage3.structure_break_timestamp),
                level_low=float(stage3.structure_low),
                level_low_minor_break=False,
                level_low_major_break=False,
                penalty_level_low_break=0,
                penalty_untested_highs=0,
                base_bonus=0,
                pno_index=pno_index,
                maturity_penalty=0,
                pno_order_adj=0,
                score_a=20,
                score_b=20,
                score_c=20,
                score_d=20,
                score_e=20,
                score_tp2=self._resolve_tp2_score(tp2=tp2, active_high=tp1, v1=v1_now),
                final_score=max(float(params.min_score), 90.0),
                entry_plan=entry_plan,
                sl_plan=float(stage3.structure_low),
                low_last_red_plan=float(stage3.structure_low),
                tp1=tp1,
                tp2=tp2,
                stage4_ready=True,
                hard_block=False,
                is_valid_setup=True,
                hard_block_reason=None,
                entry_pos=self._resolve_entry_pullback_fraction(
                    pullback_low=float(stage3.structure_low),
                    active_high=float(stage3.active_high),
                    entry_price=entry_plan,
                ),
                level_maturity_fraction=1.0,
                level_age_bars=max(idx - int(stage3.structure_high_idx), 0),
                structure_source="human_bos",
            )

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
            if ideal_like_impulse:
                refreshed_cluster = self._resolve_ideal_like_upper_tf_level_cluster(
                    one=one,
                    five=five,
                    idx=idx,
                    five_idx=five_idx,
                    stage3=stage3,
                    retired_clusters=retired_clusters,
                    params=params,
                )
                if refreshed_cluster is not None:
                    refreshed_indices, refreshed_prices = refreshed_cluster
                    refreshed_level = float(np.max(np.asarray(refreshed_prices, dtype=np.float64)))
                    if (
                        refreshed_level < (float(previous.level) - self._EPSILON)
                        and int(refreshed_indices[0]) > int(previous.cluster_first_idx)
                    ):
                        previous = None
            if previous is None:
                pass
            else:
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
                if not touch_indices and len(previous.cluster_indices) == 1:
                    synthetic_touch_idx = int(previous.cluster_indices[0])
                    if (
                        synthetic_touch_idx <= idx
                        and float(one.highs[synthetic_touch_idx]) < (stage3.active_high - self._EPSILON)
                        and abs(float(one.highs[synthetic_touch_idx]) - previous.level) <= tolerance
                    ):
                        touch_indices = (synthetic_touch_idx,)
                min_required_touches = max(1, len(previous.cluster_indices))
                if len(touch_indices) < min_required_touches:
                    return None
                latest_touch_idx = int(max(touch_indices))
                latest_high_max_age_bars = int(params.level_latest_high_max_age_bars)
                if ideal_like_impulse and int(params.ideal_like_level_latest_high_max_age_bars) > 0:
                    latest_high_max_age_bars = max(
                        latest_high_max_age_bars,
                        int(params.ideal_like_level_latest_high_max_age_bars),
                    )
                if (idx - latest_touch_idx) > latest_high_max_age_bars:
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
                elif break_depth > (float(params.level_low_minor_break_v1) * v1_now):
                    level_low_minor_break = True
                    penalty_level_low_break = 6

                level_maturity_fraction = self._resolve_level_maturity_fraction(
                    active_high_idx=stage3.active_high_idx,
                    cluster_first_idx=previous.cluster_first_idx,
                    current_idx=idx,
                )
                required_maturity_fraction = self._resolve_required_level_maturity_fraction(
                    one=one,
                    idx=idx,
                    stage3=stage3,
                    cluster_first_idx=previous.cluster_first_idx,
                    cluster_indices=previous.cluster_indices,
                    level=float(previous.level),
                    params=params,
                )
                if ideal_like_impulse:
                    required_maturity_fraction = min(
                        required_maturity_fraction,
                        float(params.ideal_like_relaxed_level_maturity_fraction),
                    )
                if level_maturity_fraction < required_maturity_fraction:
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

        cluster = None
        if ideal_like_impulse:
            cluster = self._resolve_ideal_like_upper_tf_level_cluster(
                one=one,
                five=five,
                idx=idx,
                five_idx=five_idx,
                stage3=stage3,
                retired_clusters=retired_clusters,
                params=params,
            )
        if cluster is None and not self._is_cat_d_profile(params):
            cluster = self._resolve_level_cluster(
                one=one,
                idx=idx,
                stage3=stage3,
                confirmed_highs=confirmed_highs,
                confirmed_lows=confirmed_lows,
                params=params,
                retired_clusters=retired_clusters,
                ideal_like_impulse=ideal_like_impulse,
            )
        if cluster is None:
            return None
        cluster_indices, cluster_prices = cluster
        level = float(np.max(np.asarray(cluster_prices, dtype=np.float64)))
        if level >= (stage3.active_high - self._EPSILON):
            return None

        touch_indices = tuple(
            high_idx
            for high_idx in confirmed_highs
            if high_idx >= cluster_indices[0]
            and float(one.highs[high_idx]) < (stage3.active_high - self._EPSILON)
            and abs(float(one.highs[high_idx]) - level) <= tolerance
        )
        if not touch_indices and len(cluster_indices) == 1:
            synthetic_touch_idx = int(cluster_indices[0])
            if (
                synthetic_touch_idx <= idx
                and float(one.highs[synthetic_touch_idx]) < (stage3.active_high - self._EPSILON)
                and abs(float(one.highs[synthetic_touch_idx]) - level) <= tolerance
            ):
                touch_indices = (synthetic_touch_idx,)
        min_required_touches = max(1, len(cluster_indices))
        if len(touch_indices) < min_required_touches:
            return None
        latest_touch_idx = int(max(touch_indices))
        latest_high_max_age_bars = int(params.level_latest_high_max_age_bars)
        if ideal_like_impulse and int(params.ideal_like_level_latest_high_max_age_bars) > 0:
            latest_high_max_age_bars = max(
                latest_high_max_age_bars,
                int(params.ideal_like_level_latest_high_max_age_bars),
            )
        if (idx - latest_touch_idx) > latest_high_max_age_bars:
            return None

        level_maturity_fraction = self._resolve_level_maturity_fraction(
            active_high_idx=stage3.active_high_idx,
            cluster_first_idx=int(cluster_indices[0]),
            current_idx=idx,
        )
        required_maturity_fraction = self._resolve_required_level_maturity_fraction(
            one=one,
            idx=idx,
            stage3=stage3,
            cluster_first_idx=int(cluster_indices[0]),
            cluster_indices=cluster_indices,
            level=level,
            params=params,
        )
        if ideal_like_impulse:
            required_maturity_fraction = min(
                required_maturity_fraction,
                float(params.ideal_like_relaxed_level_maturity_fraction),
            )
        if level_maturity_fraction < required_maturity_fraction:
            return None

        level_age_bars_1m = max(idx - int(cluster_indices[0]), 0)
        level_age_bars_upper_tf = max(
            int(level_age_bars_1m / (params.levels_timeframe.to_milliseconds() / 60000)), 1
        )
        if level_age_bars_upper_tf > params.level_max_age_bars_upper_tf:
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
        if stage4.structure_source == "human_bos":
            return stage4
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        v5_now = max(float(five.v5[five_idx]), self._EPSILON)
        ideal_like_impulse = self._is_ideal_like_impulse(stage1=stage1, params=params)
        depth_reference = max(stage1.reference_leg_size, stage1.pump_range_5m, self._EPSILON)
        slip_plan = max(float(stage4.level) * float(params.min_tick_fraction), float(params.slip_plan_v1_fraction) * v1_now)
        entry_plan = float(stage4.level) + slip_plan
        entry_pos = self._resolve_entry_pullback_fraction(
            pullback_low=float(stage3.pullback_low),
            active_high=float(stage3.active_high),
            entry_price=entry_plan,
        )
        low_last_red_plan = self._resolve_low_last_red_plan(
            one=one,
            stage1=stage1,
            stage3=stage3,
            stage4=stage4,
            idx=idx,
            confirmation_mode=str(getattr(params, "entry_confirmation_mode", "cross")),
        )
        # Keep planned risk anchored to the actionable reclaim extremum.
        sl_plan = low_last_red_plan
        tp1 = float(stage3.active_high)
        tp2 = self._resolve_tp2(
            active_high=tp1,
            entry_price=entry_plan,
            pullback_height=float(stage3.pullback_depth),
            v1=v1_now,
        )
        level_life_ema_spread_growth_share = self._resolve_level_life_ema_spread_growth_share(
            one=one,
            stage4=stage4,
            idx=idx,
        )
        if ideal_like_impulse:
            microstructure_stop = self._resolve_ideal_like_microstructure_stop(
                one=one,
                idx=idx,
                stage3=stage3,
                stage4=stage4,
            )
            if microstructure_stop is not None:
                low_last_red_plan = float(microstructure_stop)
            elif self._has_ideal_like_ema_spread_support(one=one, stage4=stage4, idx=idx):
                low_last_red_plan = float(stage3.pullback_low)
            else:
                low_last_red_plan = float(stage3.pullback_low)

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

        leg_v5 = self._safe_divide(depth_reference, v5_now)
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
        depth_frac = self._safe_divide(stage3.pullback_depth, depth_reference)
        if 0.30 <= depth_frac <= 0.75:
            score_b_depth = 10
        elif 0.20 <= depth_frac < 0.30 or 0.75 < depth_frac <= 1.0:
            score_b_depth = 7
        elif depth_frac <= float(params.pullback_valid_max_leg_fraction):
            score_b_depth = 3
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
        base_profile = self._resolve_pullback_base_profile(one=one, idx=idx, stage1=stage1, stage3=stage3)
        base_bonus = int(base_profile.get("bonus") or 0)
        if 0.40 <= entry_pos <= 0.55:
            level_pos_score = 7
        elif 0.35 <= entry_pos < 0.40 or 0.55 < entry_pos <= 0.62:
            level_pos_score = 4
        elif 0.30 <= entry_pos < 0.35 or 0.62 < entry_pos <= 0.70:
            level_pos_score = 1
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
        overhead_profile = self._resolve_overhead_resistance_profile(one=one, stage1=stage1, stage4=stage4)
        compact_geometry_adj = self._resolve_compact_geometry_adjustment(
            entry_plan=entry_plan,
            sl_plan=sl_plan,
            tp1=tp1,
            tp2=tp2,
        )
        overhead_quality_adj = self._resolve_overhead_quality_adjustment(
            overhead_score=float(overhead_profile.get("score") or 0.0),
            red_count=int(overhead_profile.get("red_count") or 0),
        )
        overhead_score = float(overhead_profile.get("score") or 0.0)
        overhead_red_count = int(overhead_profile.get("red_count") or 0)
        current_timestamp_ms = int(one.timestamps[idx])
        active_high_to_signal_minutes = max((current_timestamp_ms - int(stage3.active_high_timestamp)) / 60_000.0, 0.0)
        ideal_like_impulse = self._is_ideal_like_impulse(stage1=stage1, params=params)
        fresh_overhead_exception = (
            active_high_to_signal_minutes <= 12.0
            and stage4.touches <= 3
            and entry_pos <= 0.75
            and overhead_score <= 1.10
            and overhead_red_count <= 7
        )
        freshness_adj = self._resolve_setup_freshness_adjustment(
            current_timestamp_ms=current_timestamp_ms,
            pump_start_timestamp_ms=int(stage1.pump_start_timestamp),
            active_high_timestamp_ms=int(stage3.active_high_timestamp),
            level_valid_timestamp_ms=int(stage4.level_valid_timestamp),
        )
        impulse_quality_adj = self._resolve_impulse_quality_adjustment(stage1=stage1)
        pno_order_adj = self._resolve_pno_order_adj(stage4.pno_index)
        maturity_penalty = self._resolve_maturity_penalty(stage1=stage1, pno_index=stage4.pno_index)
        leg_start_wick_break_penalty = 8 if stage3.pullback_wick_broke_leg_start else 0
        final_score = float(
            score_a
            + score_b
            + score_c
            + score_d
            + score_e
            + score_tp2
            + compact_geometry_adj
            + overhead_quality_adj
            + freshness_adj
            + impulse_quality_adj
            + pno_order_adj
            + penalty_untested_highs
            + int(overhead_profile.get("penalty") or 0)
            - stage4.penalty_level_low_break
            - maturity_penalty
            - leg_start_wick_break_penalty
        )

        hard_block_reason = stage4.hard_block_reason
        hard_block = bool(stage4.hard_block)
        if stage4.level >= stage3.active_high:
            hard_block = True
            hard_block_reason = "level_not_below_active_high"
        elif stage4.touches > int(params.max_level_touches):
            hard_block = True
            hard_block_reason = "too_many_touches"
        elif self._has_deep_stale_break_below_pullback_after_level(
            one=one,
            idx=idx,
            stage3=stage3,
            stage4=stage4,
        ):
            hard_block = True
            hard_block_reason = "traded_too_far_below_pullback_after_level"
        elif float(five.closes[five_idx]) <= (
            float(five.ema20[five_idx])
            - max((0.15 * v5_now), (float(five.closes[five_idx]) * 0.0005), self._EPSILON)
        ):
            hard_block = True
            hard_block_reason = "close_below_ema20"
        elif stage3.pullback_close_broke_leg_start:
            hard_block = True
            hard_block_reason = "pullback_closed_below_leg_start"
        elif stage3.pullback_depth > (float(params.pullback_invalid_max_leg_fraction) * depth_reference):
            hard_block = True
            hard_block_reason = "pullback_too_deep_vs_leg"
        elif entry_pos > float(params.close_above_max_entry_pos):
            hard_block = True
            hard_block_reason = "entry_pos_too_high"
        elif (
            stage4.level_maturity_fraction >= 0.90
            and stage4.touches >= 5
            and entry_pos >= 0.78
            and level_life_ema_spread_growth_share < 0.20
        ):
            hard_block = True
            hard_block_reason = "level_too_stale"
        elif self._has_stale_reclaim_above_level(one=one, idx=idx, stage4=stage4, params=params):
            hard_block = True
            hard_block_reason = "level_already_reclaimed_too_far"
        elif self._has_prior_upper_tf_atr_reclaim_above_level(
            one=one,
            five=five,
            five_idx=five_idx,
            stage4=stage4,
        ):
            hard_block = True
            hard_block_reason = "level_already_reclaimed_too_far"
        elif (
            stage1.pre_pump_ema_crosses_1h >= 5
            and stage1.pre_pump_barcode_fraction_1h >= 0.50
            and (
                stage1.pump_counterflow_ratio_5m >= 0.08
                or stage1.pump_path_efficiency < 0.24
            )
        ):
            hard_block = True
            hard_block_reason = "precursor_too_choppy"
        elif active_high_to_signal_minutes > 90.0 and overhead_score >= 0.58:
            hard_block = True
            hard_block_reason = "stale_setup_into_overhead"
        elif stage4.level_maturity_fraction < (
            min(float(params.level_min_maturity_fraction), float(params.ideal_like_relaxed_level_maturity_fraction))
            if ideal_like_impulse
            else float(params.level_min_maturity_fraction)
        ):
            hard_block = True
            hard_block_reason = "level_not_mature_enough"
        elif overhead_score > 0.76 and not fresh_overhead_exception:
            hard_block = True
            hard_block_reason = "overhead_too_heavy"
        elif overhead_score >= 0.82 and overhead_red_count >= 5 and not fresh_overhead_exception:
            hard_block = True
            hard_block_reason = "overhead_too_heavy"
        elif dstop_plan <= self._EPSILON:
            hard_block = True
            hard_block_reason = "non_positive_stop_distance"
        elif net_tp1_move <= 0.0:
            hard_block = True
            hard_block_reason = "non_positive_tp1_after_fee"
        stage4_ready = not hard_block
        is_valid_setup = stage4_ready
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
            pullback_base_start_idx=(
                int(base_profile["start_idx"]) if base_profile.get("start_idx") is not None else None
            ),
            pullback_base_end_idx=(
                int(base_profile["end_idx"]) if base_profile.get("end_idx") is not None else None
            ),
            pullback_base_low=(
                float(base_profile["low"]) if base_profile.get("low") is not None else None
            ),
            pullback_base_high=(
                float(base_profile["high"]) if base_profile.get("high") is not None else None
            ),
            pullback_base_quality=float(base_profile.get("quality") or 0.0),
            pullback_base_left_vacuum=float(base_profile.get("left_vacuum") or 0.0),
            overhead_resistance_score=float(overhead_profile.get("score") or 0.0),
            overhead_resistance_penalty=int(overhead_profile.get("penalty") or 0),
            overhead_red_body_share=float(overhead_profile.get("red_body_share") or 0.0),
            overhead_red_count=int(overhead_profile.get("red_count") or 0),
            dominant_overhead_red_timestamp=(
                int(overhead_profile["dominant_timestamp"])
                if overhead_profile.get("dominant_timestamp") is not None
                else None
            ),
            dominant_overhead_red_high=(
                float(overhead_profile["dominant_high"])
                if overhead_profile.get("dominant_high") is not None
                else None
            ),
            dominant_overhead_red_body=float(overhead_profile.get("dominant_body") or 0.0),
            level_life_ema_spread_growth_share=level_life_ema_spread_growth_share,
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
        for position in range(len(confirmed_lows) - 1, -1, -1):
            low_idx = int(confirmed_lows[position])
            low_price = float(one.lows[low_idx])
            if (active_high - low_price) < max(required_rebound, self._EPSILON):
                continue
            if self._range_tree_query_value(one.low_range_tree, left=low_idx, right=end_idx) < (low_price - self._EPSILON):
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
        ideal_like_impulse: bool = False,
    ) -> tuple[tuple[int, ...], tuple[float, ...]] | None:
        if len(confirmed_highs) < 1:
            confirmed_highs = self._resolve_recent_shelf_highs(
                one=one,
                start_idx=stage3.pullback_start_idx + 1,
                end_idx=idx,
                active_high=stage3.active_high,
                v1_now=max(float(one.v1[idx]), self._EPSILON),
            )
        if len(confirmed_highs) < 1:
            confirmed_highs = self._resolve_fallback_level_highs(
                one=one,
                start_idx=stage3.pullback_start_idx + 1,
                end_idx=idx,
                active_high=stage3.active_high,
                v1_now=max(float(one.v1[idx]), self._EPSILON),
            )
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
                if cluster_size == 1:
                    # Primary check: confirmed_low after high
                    has_confirmed_low = self._is_single_touch_level_candidate(
                        one=one,
                        idx=idx,
                        high_idx=indices[0],
                        confirmed_lows=confirmed_lows,
                    )
                    # Alternative check: price consolidation near high (3+ bars within 0.3*v1)
                    if not has_confirmed_low:
                        consolidation_bars = 0
                        tolerance_consolidation = 0.35 * v1_now
                        high_price = float(one.highs[indices[0]])
                        for check_idx in range(indices[0], min(idx + 1, len(one.closes))):
                            high_touch = float(one.highs[check_idx]) >= (high_price - tolerance_consolidation)
                            close_near_high = abs(float(one.closes[check_idx]) - high_price) <= tolerance_consolidation
                            if high_touch or close_near_high:
                                consolidation_bars += 1
                        if consolidation_bars < 2:
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
                candidate_level = float(np.max(np.asarray(prices, dtype=np.float64)))
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
        if ideal_like_impulse:
            fallback = self._resolve_ideal_like_level_cluster(
                one=one,
                idx=idx,
                stage3=stage3,
                retired_clusters=retired_clusters,
                params=params,
            )
            if fallback is not None:
                return fallback
        return None

    def _resolve_ideal_like_level_cluster(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage3: Stage3Context,
        retired_clusters: list[RetiredCluster],
        params: PnoParams,
    ) -> tuple[tuple[int, ...], tuple[float, ...]] | None:
        if idx <= stage3.pullback_low_idx:
            return None
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        tolerance = max(float(params.level_touch_tolerance_v1) * v1_now, self._EPSILON)
        search_start = max(stage3.pullback_low_idx, idx - max(int(params.ideal_like_level_latest_high_max_age_bars), 6))
        candidate_idx: int | None = None
        candidate_price = -np.inf
        for probe_idx in range(search_start, idx + 1):
            high_price = float(one.highs[probe_idx])
            if high_price >= (stage3.active_high - self._EPSILON):
                continue
            close_price = float(one.closes[probe_idx])
            low_price = float(one.lows[probe_idx])
            body_low = min(float(one.opens[probe_idx]), close_price)
            if close_price <= (high_price - max(0.65 * (high_price - low_price), tolerance)):
                continue
            if body_low < (stage3.pullback_low - tolerance):
                continue
            if high_price > candidate_price:
                candidate_idx = int(probe_idx)
                candidate_price = high_price
        if candidate_idx is None:
            return None
        if not self._is_cluster_rearm_allowed(
            active_high_idx=stage3.active_high_idx,
            candidate_level=float(candidate_price),
            stage3=stage3,
            retired_clusters=retired_clusters,
            v1_now=v1_now,
            rearm_min_distance_v1=float(params.level_rearm_min_distance_v1),
        ):
            return None
        return (candidate_idx,), (float(candidate_price),)

    def _resolve_recent_shelf_highs(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
        active_high: float,
        v1_now: float,
    ) -> list[int]:
        if end_idx - start_idx < 1:
            return []
        window_start = max(start_idx, end_idx - 7)
        candidate_indices: list[int] = []
        for probe_idx in range(window_start, end_idx + 1):
            high_price = float(one.highs[probe_idx])
            if high_price >= (active_high - self._EPSILON):
                continue
            bar_range = max(float(one.highs[probe_idx]) - float(one.lows[probe_idx]), self._EPSILON)
            close_position = self._safe_divide(float(one.closes[probe_idx]) - float(one.lows[probe_idx]), bar_range)
            if close_position < 0.45:
                continue
            candidate_indices.append(int(probe_idx))
        if len(candidate_indices) < 2:
            return []
        ceiling = max(float(one.highs[probe_idx]) for probe_idx in candidate_indices)
        tolerance = max(0.40 * v1_now, self._EPSILON)
        shelf_indices = [
            int(probe_idx)
            for probe_idx in candidate_indices
            if float(one.highs[probe_idx]) >= (ceiling - tolerance)
        ]
        if len(shelf_indices) < 2:
            return []
        return shelf_indices[-3:]

    def _resolve_fallback_level_highs(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
        active_high: float,
        v1_now: float,
    ) -> list[int]:
        if end_idx - start_idx < 2:
            return []
        candidate_indices: list[int] = []
        tolerance = max(0.35 * v1_now, self._EPSILON)
        for probe_idx in range(max(start_idx, 1), min(end_idx, len(one.highs) - 1)):
            high_price = float(one.highs[probe_idx])
            if high_price >= (active_high - self._EPSILON):
                continue
            if high_price < float(one.highs[probe_idx - 1]) or high_price < float(one.highs[probe_idx + 1]):
                continue
            touch_count = 0
            for check_idx in range(probe_idx, min(end_idx + 1, len(one.highs))):
                if float(one.highs[check_idx]) >= (high_price - tolerance):
                    touch_count += 1
            if touch_count >= 2:
                candidate_indices.append(int(probe_idx))
        return candidate_indices

    def _is_single_touch_level_candidate(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        high_idx: int,
        confirmed_lows: np.ndarray | list[int],
    ) -> bool:
        if high_idx >= idx:
            return False
        confirmed_lows_arr = np.asarray(confirmed_lows, dtype=np.int64)
        return bool(np.any((confirmed_lows_arr > high_idx) & (confirmed_lows_arr <= idx)))

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
        stage4: Stage4Context,
        idx: int,
        confirmation_mode: str = "cross",
    ) -> float:
        del stage1
        search_start = max(int(stage4.cluster_first_idx), 0)
        first_cross_idx = self._resolve_level_first_cross_idx(
            one=one,
            stage4=stage4,
            idx=idx,
        )
        if first_cross_idx is None:
            search_end = idx
        else:
            search_end = first_cross_idx - 1
        if confirmation_mode != "close_above":
            search_end = min(search_end, idx - 1)
        if search_end < search_start:
            return float(stage3.pullback_low)
        search_slice = one.red[search_start : search_end + 1]
        red_indices = np.where(search_slice)[0]
        if red_indices.size == 0:
            return float(stage3.pullback_low)
        for red_offset in red_indices[::-1]:
            last_red_idx = search_start + int(red_offset)
            last_red_low = float(one.lows[last_red_idx])
            if last_red_low <= (float(stage4.level) - self._EPSILON):
                return last_red_low
        return float(stage3.pullback_low)

    def _has_prior_upper_tf_atr_reclaim_above_level(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        five_idx: int,
        stage4: Stage4Context,
    ) -> bool:
        if five_idx <= 0:
            return False
        level_valid_five_idx = int(
            np.searchsorted(five.timestamps, int(one.timestamps[max(int(stage4.cluster_first_idx), 0)]), side="right") - 1
        )
        if level_valid_five_idx < 0:
            return False
        search_start = max(level_valid_five_idx + 1, 0)
        search_end = min(int(five_idx) - 1, len(five.timestamps) - 1)
        if search_end < search_start:
            return False
        level = float(stage4.level)
        for probe_idx in range(search_start, search_end + 1):
            atr_like_threshold = max(float(five.v5[probe_idx]), self._EPSILON)
            if float(five.highs[probe_idx]) >= (level + atr_like_threshold - self._EPSILON):
                return True
        return False

    def _resolve_level_first_cross_idx(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        idx: int,
    ) -> int | None:
        search_start = max(min(int(stage4.cluster_first_idx) + 1, idx), 0)
        if search_start > idx:
            return None
        cross_offsets = np.where(one.highs[search_start : idx + 1] >= (float(stage4.level) - self._EPSILON))[0]
        if cross_offsets.size == 0:
            return None
        return search_start + int(cross_offsets[0])

    def _resolve_level_first_cross_bars(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        idx: int,
    ) -> int | None:
        first_cross_idx = self._resolve_level_first_cross_idx(one=one, stage4=stage4, idx=idx)
        if first_cross_idx is None:
            return None
        return max(int(first_cross_idx) - int(stage4.cluster_first_idx), 0)

    def _resolve_level_life_profile(
        self,
        *,
        one: OneMinuteFrame,
        stage4: Stage4Context,
        signal_idx: int,
    ) -> dict[str, int | float | bool]:
        start_idx = max(int(stage4.cluster_first_idx) + 1, 0)
        end_idx = min(signal_idx - 1, len(one.timestamps) - 1)
        if end_idx < start_idx:
            return {
                "bar_count": 0,
                "close_above_count": 0,
                "close_below_pullback_count": 0,
                "low_below_pullback_count": 0,
                "false_break_wick_count": 0,
                "prior_full_above_bar": False,
                "prior_tp1_hit": False,
                "path_efficiency": 1.0,
                "quote_volume_median": 0.0,
            }
        highs = one.highs[start_idx : end_idx + 1]
        lows = one.lows[start_idx : end_idx + 1]
        closes = one.closes[start_idx : end_idx + 1]
        quote_volume = one.quote_volume[start_idx : end_idx + 1]
        level = float(stage4.level)
        pullback_low = float(stage4.pullback_low)
        close_above_mask = closes > (level + self._EPSILON)
        full_above_mask = close_above_mask & (lows > (level + self._EPSILON))
        false_break_mask = (highs > (level + self._EPSILON)) & ~close_above_mask
        close_below_pullback_mask = closes < (pullback_low - self._EPSILON)
        low_below_pullback_mask = lows < (pullback_low - self._EPSILON)
        path_efficiency = 1.0
        if closes.size >= 2:
            path_efficiency = self._safe_divide(
                abs(float(closes[-1]) - float(closes[0])),
                float(np.sum(np.abs(np.diff(closes)))),
            )
        return {
            "bar_count": int(closes.size),
            "close_above_count": int(np.sum(close_above_mask)),
            "close_below_pullback_count": int(np.sum(close_below_pullback_mask)),
            "low_below_pullback_count": int(np.sum(low_below_pullback_mask)),
            "false_break_wick_count": int(np.sum(false_break_mask)),
            "prior_full_above_bar": bool(np.any(full_above_mask)),
            "prior_tp1_hit": bool(np.any(highs >= (float(stage4.tp1) - self._EPSILON))),
            "path_efficiency": float(path_efficiency),
            "quote_volume_median": float(np.nanmedian(quote_volume)) if quote_volume.size > 0 else 0.0,
        }

    def _resolve_close_above_pre_signal_decay_reason(
        self,
        *,
        one: OneMinuteFrame,
        signal_idx: int,
        params: PnoParams,
        stage4: Stage4Context,
    ) -> str | None:
        level_profile = self._resolve_level_life_profile(
            one=one,
            stage4=stage4,
            signal_idx=signal_idx,
        )
        first_cross_bars = self._resolve_level_first_cross_bars(one=one, stage4=stage4, idx=signal_idx)
        if first_cross_bars is not None and first_cross_bars > int(params.close_above_max_level_cross_bars):
            return "level_crossed_too_late"
        if bool(level_profile["prior_tp1_hit"]):
            return "tp1_already_tagged_before_signal"
        if bool(level_profile["prior_full_above_bar"]):
            return "level_already_accepted_before_signal"
        if (
            int(level_profile["bar_count"]) >= 40
            and int(level_profile["close_above_count"]) >= 4
            and int(level_profile["false_break_wick_count"]) >= 4
            and float(level_profile["path_efficiency"]) <= 0.08
        ):
            return "level_drifted_too_long_before_signal"
        if (
            int(level_profile["bar_count"]) >= 120
            and int(level_profile["close_above_count"]) <= 1
            and int(level_profile["close_below_pullback_count"]) >= 20
            and int(level_profile["low_below_pullback_count"]) >= 40
            and float(level_profile["path_efficiency"]) <= 0.03
        ):
            return "level_spent_too_long_below_pullback"
        if (
            float(level_profile["quote_volume_median"]) < float(params.close_above_min_level_life_quote_volume_median)
            and float(level_profile["path_efficiency"]) <= 0.22
        ):
            return "level_life_too_illiquid"
        return None

    def _is_armed_entry_invalidated_before_trigger(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        armed: ArmedContext,
        params: PnoParams | None = None,
    ) -> bool:
        risk_distance = max(float(armed.stage4.entry_plan) - float(armed.stage4.sl_plan), self._EPSILON)
        close_tolerance = max(0.05 * risk_distance, self._EPSILON)
        wick_tolerance = max(0.15 * risk_distance, close_tolerance)
        current_close = float(one.closes[idx])
        current_low = float(one.lows[idx])
        stop_level = float(armed.stage4.low_last_red_plan)
        pullback_low = float(armed.stage3.pullback_low)
        stop_broken = current_close <= (stop_level - close_tolerance) or current_low <= (stop_level - wick_tolerance)
        pullback_broken = current_close <= (pullback_low - close_tolerance) or current_low <= (pullback_low - wick_tolerance)
        if armed.stage4.structure_source == "human_bos":
            return stop_broken or pullback_broken
        close_above_decay_broken = (
            self._resolve_close_above_pre_signal_decay_reason(
                one=one,
                signal_idx=idx,
                params=params or PnoParams(symbol=""),
                stage4=armed.stage4,
            )
            is not None
        )
        if (
            params is not None
            and bool(getattr(params, "ideal_like_ignore_decay_invalidation", False))
            and self._is_ideal_like_impulse(stage1=armed.stage1, params=params)
        ):
            close_above_decay_broken = False
        return stop_broken or pullback_broken or close_above_decay_broken

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

    def _resolve_pullback_base_profile(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        stage1: Stage1Context,
        stage3: Stage3Context,
    ) -> dict[str, int | float | None]:
        v1_now = max(float(one.v1[idx]), self._EPSILON)
        search_start = max(stage1.active_high_idx + 1, stage3.pullback_low_idx - 3)
        search_end = min(idx, stage3.pullback_low_idx + 8)
        best_profile: dict[str, int | float | None] = {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        }
        if search_end <= search_start:
            return best_profile
        for block_size in range(2, 7):
            for block_start in range(search_start, search_end - block_size + 2):
                block_end = block_start + block_size - 1
                if not (block_start <= stage3.pullback_low_idx <= block_end):
                    continue
                block_high = float(np.max(one.highs[block_start : block_end + 1]))
                block_low = float(np.min(one.lows[block_start : block_end + 1]))
                block_range = block_high - block_low
                if block_range <= 0.0 or block_range > (1.20 * v1_now):
                    continue
                left_start = max(stage1.active_high_idx + 1, block_start - 12)
                left_slice_highs = one.highs[left_start:block_start]
                right_slice_highs = one.highs[block_end + 1 : idx + 1]
                if left_slice_highs.size == 0 or right_slice_highs.size == 0:
                    continue
                left_drop = float(np.max(left_slice_highs)) - block_high
                right_lift = float(np.max(right_slice_highs)) - block_high
                if left_drop < (1.5 * v1_now) or right_lift < (0.5 * v1_now):
                    continue
                left_overlap = np.sum((left_slice_highs >= (block_low - self._EPSILON)) & (left_slice_highs <= (block_high + self._EPSILON)))
                left_vacuum = 1.0 - min(float(left_overlap) / max(len(left_slice_highs), 1), 1.0)
                quality = (
                    0.40 * min(right_lift / max(v1_now, self._EPSILON), 2.5)
                    + 0.35 * max(1.0 - (block_range / max(1.20 * v1_now, self._EPSILON)), 0.0)
                    + 0.25 * left_vacuum
                )
                bonus = 5 if quality >= 1.20 else 3 if quality >= 0.80 else 1 if quality >= 0.50 else 0
                if quality > float(best_profile["quality"] or 0.0):
                    best_profile = {
                        "bonus": bonus,
                        "start_idx": int(block_start),
                        "end_idx": int(block_end),
                        "low": block_low,
                        "high": block_high,
                        "quality": float(quality),
                        "left_vacuum": float(left_vacuum),
                    }
        return best_profile

    def _resolve_overhead_resistance_profile(
        self,
        *,
        one: OneMinuteFrame,
        stage1: Stage1Context,
        stage4: Stage4Context,
    ) -> dict[str, int | float | None]:
        zone_high = float(stage1.active_high)
        zone_low = float(stage4.level)
        if zone_high <= zone_low:
            return {
                "score": 0.0,
                "penalty": 0,
                "red_body_share": 0.0,
                "red_count": 0,
                "dominant_timestamp": None,
                "dominant_high": None,
                "dominant_body": 0.0,
            }
        start_idx = min(stage1.active_high_idx + 1, stage4.level_valid_idx)
        end_idx = max(stage1.active_high_idx + 1, stage4.level_valid_idx)
        weighted_red_body = 0.0
        dominant_weighted_body = 0.0
        dominant_timestamp: int | None = None
        dominant_high: float | None = None
        dominant_body = 0.0
        red_count = 0
        bars_considered = 0
        zone_span = max(zone_high - zone_low, self._EPSILON)
        for probe_idx in range(start_idx, end_idx + 1):
            bar_high = float(one.highs[probe_idx])
            bar_low = float(one.lows[probe_idx])
            overlap = max(min(bar_high, zone_high) - max(bar_low, zone_low), 0.0)
            if overlap <= 0.0:
                continue
            bars_considered += 1
            bar_open = float(one.opens[probe_idx])
            bar_close = float(one.closes[probe_idx])
            if bar_close >= bar_open:
                continue
            red_count += 1
            bar_body = bar_open - bar_close
            bar_range = max(bar_high - bar_low, self._EPSILON)
            weighted_body = bar_body * (overlap / bar_range)
            weighted_red_body += weighted_body
            if weighted_body > dominant_weighted_body:
                dominant_weighted_body = weighted_body
                dominant_body = bar_body
                dominant_timestamp = int(one.timestamps[probe_idx])
                dominant_high = bar_high
        red_body_share = weighted_red_body / zone_span
        red_density = (red_count / bars_considered) if bars_considered > 0 else 0.0
        score = (0.70 * red_body_share) + (0.30 * red_density)
        if score >= 1.0 or dominant_weighted_body >= (0.35 * zone_span):
            penalty = -8
        elif score >= 0.65 or dominant_weighted_body >= (0.22 * zone_span):
            penalty = -5
        elif score >= 0.35:
            penalty = -3
        else:
            penalty = 0
        return {
            "score": float(score),
            "penalty": int(penalty),
            "red_body_share": float(red_body_share),
            "red_count": int(red_count),
            "dominant_timestamp": dominant_timestamp,
            "dominant_high": dominant_high,
            "dominant_body": float(dominant_body),
        }

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
        age_ms = max(stage1.current_levels_timestamp - stage1.pump_start_timestamp, stage1.levels_timeframe_ms)
        if age_ms <= 30 * 60_000:
            time_penalty = 0
        elif age_ms <= 60 * 60_000:
            time_penalty = 4
        elif age_ms <= 90 * 60_000:
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

    def _resolve_reference_high(
        self,
        *,
        five: FiveMinuteFrame,
        pump_start_idx: int,
        active_high_idx: int,
        params: PnoParams,
    ) -> tuple[float, float]:
        del params
        if active_high_idx < pump_start_idx:
            return float(five.highs[active_high_idx]), 1.0
        cache_key = (int(pump_start_idx), int(active_high_idx))
        cached = self._runtime_reference_high_cache.get(cache_key)
        if cached is not None:
            return cached

        pump_pre_atr = float(five.atr_pre_14[pump_start_idx])
        baseline_quote = float(five.pre_quote_median_24[pump_start_idx])
        reference_high = float(five.highs[pump_start_idx])
        last_weight = 1.0

        for probe_idx in range(pump_start_idx + 1, active_high_idx + 1):
            bar_high = float(five.highs[probe_idx])
            extension = bar_high - reference_high
            if extension <= self._EPSILON:
                continue

            bar_open = float(five.opens[probe_idx])
            bar_close = float(five.closes[probe_idx])
            bar_tr = float(five.tr[probe_idx])
            bar_quote = float(five.quote_volume[probe_idx])
            bar_low = float(five.lows[probe_idx])
            body = abs(bar_close - bar_open)

            tr_strength = self._safe_divide(bar_tr, pump_pre_atr)
            volume_strength = self._safe_divide(bar_quote, baseline_quote)
            body_strength = self._safe_divide(body, max(bar_tr, self._EPSILON))
            extension_strength = self._safe_divide(extension, pump_pre_atr)
            close_position = self._safe_divide(bar_close - bar_low, max(bar_tr, self._EPSILON))
            green_strength = 1.0 if bar_close >= bar_open else 0.0

            tr_score = min(max((tr_strength - 1.1) / 1.2, 0.0), 1.0)
            volume_score = min(max((volume_strength - 1.25) / 1.5, 0.0), 1.0)
            body_score = min(max((body_strength - 0.45) / 0.35, 0.0), 1.0)
            extension_score = min(max((extension_strength - 0.12) / 0.38, 0.0), 1.0)
            close_score = min(max((close_position - 0.55) / 0.3, 0.0), 1.0)
            quality_score = (
                (0.32 * tr_score)
                + (0.18 * volume_score)
                + (0.16 * body_score)
                + (0.24 * extension_score)
                + (0.06 * close_score)
                + (0.04 * green_strength)
            )
            weight = quality_score * quality_score

            reference_high += weight * extension
            last_weight = weight

        reference_high = min(reference_high, float(five.highs[active_high_idx]))
        resolved = (float(reference_high), float(last_weight))
        self._runtime_reference_high_cache[cache_key] = resolved
        return resolved

    def _resolve_stage1_hold_status(self, *, one: OneMinuteFrame, idx: int, stage1: Stage1Context | None) -> str:
        if stage1 is None or idx < 0 or idx >= len(one.timestamps):
            return "unknown"
        hold_floor = min(float(stage1.hold_floor), float(stage1.stage1_hold_price))
        # Allow brief intrabar sweeps around the hold floor before stage2 is formed.
        close_tolerance = self._resolve_stage1_hold_close_tolerance(
            active_high=float(stage1.active_high),
            stage1_hold_price=float(stage1.stage1_hold_price),
            leg_size=float(stage1.leg_size),
            reference_leg_size=float(stage1.reference_leg_size),
        )
        wick_tolerance = max(float(stage1.leg_size) * 0.30, close_tolerance)
        if float(one.closes[idx]) <= (hold_floor - close_tolerance):
            return "closed_below_hold"
        if float(one.lows[idx]) <= (hold_floor - wick_tolerance):
            return "wick_below_hold"
        return "held_above_hold"

    def _resolve_stage1_hold_close_tolerance(
        self,
        *,
        active_high: float,
        stage1_hold_price: float,
        leg_size: float,
        reference_leg_size: float,
    ) -> float:
        resolved_leg_size = max(float(leg_size), self._EPSILON)
        resolved_reference_leg_size = max(float(reference_leg_size), resolved_leg_size, self._EPSILON)
        tolerance = max(
            resolved_leg_size * 0.05,
            resolved_reference_leg_size * 0.08,
            self._EPSILON,
        )
        if float(stage1_hold_price) > (float(active_high) + self._EPSILON):
            tolerance = max(tolerance, resolved_reference_leg_size * 0.18)
        return tolerance

    def _allows_pullback_search_after_inplay_end(
        self,
        *,
        one: OneMinuteFrame,
        five: FiveMinuteFrame,
        idx: int,
        five_idx: int,
        stage1: Stage1Context | None,
    ) -> bool:
        if stage1 is None or idx < 0 or idx >= len(one.timestamps) or five_idx < 0 or five_idx >= len(five.timestamps):
            return False
        hold_status = self._resolve_stage1_hold_status(one=one, idx=idx, stage1=stage1)
        if hold_status != "held_above_hold":
            return False
        bars_since_active_high = idx - int(stage1.active_high_idx)
        if bars_since_active_high > 6:
            return False
        current_close_5m = float(five.closes[five_idx])
        current_ema20_5m = float(five.ema20[five_idx])
        ema_tolerance = max(float(five.v5[five_idx]) * 0.20, current_close_5m * 0.0005, self._EPSILON)
        return current_close_5m >= (current_ema20_5m - ema_tolerance)

    def _resolve_leg_start_status(
        self,
        *,
        five: FiveMinuteFrame,
        five_idx: int,
        stage1: Stage1Context | None,
    ) -> str:
        if stage1 is None or five_idx < 0 or five_idx >= len(five.timestamps):
            return "unknown"
        leg_start = float(stage1.leg_start)
        leg_break_tolerance = max(float(stage1.leg_size) * 0.08, self._EPSILON)
        if float(five.closes[five_idx]) <= (leg_start - leg_break_tolerance):
            return "closed_below_leg_start"
        if float(five.lows[five_idx]) <= (leg_start - leg_break_tolerance):
            return "wick_below_leg_start"
        return "held_above_leg_start"

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

    def _resolve_tp2(
        self,
        *,
        active_high: float,
        entry_price: float,
        pullback_height: float,
        v1: float,
    ) -> float:
        base_high = float(active_high)
        entry = min(float(entry_price), base_high)
        projected_tp2 = base_high + max(base_high - entry, self._EPSILON)
        target_step = max(float(v1), 0.0005 * base_high, self._EPSILON)
        round_step = self._round_to_preferred_step(target_step)
        tp2 = np.floor(projected_tp2 / max(round_step, self._EPSILON)) * round_step
        if tp2 <= (base_high + self._EPSILON):
            tp2 = projected_tp2
        return float(tp2)

    def _resolve_post_tp1_trailing_stop(
        self,
        *,
        one: OneMinuteFrame,
        tp1_hit_idx: int,
        current_idx: int,
        previous_high_watermark: float,
        current_high: float,
        current_stop: float,
        be_protect_price: float,
    ) -> tuple[float, float]:
        new_high_watermark = max(float(previous_high_watermark), float(current_high))
        if float(current_high) <= (float(previous_high_watermark) + self._EPSILON):
            return float(current_stop), new_high_watermark
        search_start = max(int(tp1_hit_idx) + 1, 0)
        search_end = max(int(current_idx), search_start)
        search_slice = one.red[search_start : search_end + 1]
        candidate_stop = max(float(current_stop), float(be_protect_price))
        red_indices = np.where(search_slice)[0]
        if red_indices.size > 0:
            last_red_idx = search_start + int(red_indices[-1])
            candidate_stop = max(candidate_stop, float(one.lows[last_red_idx]))
        return candidate_stop, new_high_watermark

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

    @staticmethod
    def _resolve_compact_geometry_adjustment(
        *,
        entry_plan: float,
        sl_plan: float,
        tp1: float,
        tp2: float,
    ) -> int:
        risk = entry_plan - sl_plan
        if risk <= 0.0:
            return 0
        tp1_r = (tp1 - entry_plan) / risk
        tp2_r = (tp2 - entry_plan) / risk
        adjustment = 0
        if 0.65 <= tp1_r <= 1.15:
            adjustment += 6
        elif 0.55 <= tp1_r < 0.65 or 1.15 < tp1_r <= 1.45:
            adjustment += 3
        elif tp1_r > 2.0:
            adjustment -= 6
        elif tp1_r > 1.6:
            adjustment -= 3
        elif tp1_r < 0.45:
            adjustment -= 2

        if tp2_r <= 2.0:
            adjustment += 2
        elif tp2_r > 3.0:
            adjustment -= 3
        return adjustment

    @staticmethod
    def _resolve_setup_freshness_adjustment(
        *,
        current_timestamp_ms: int,
        pump_start_timestamp_ms: int,
        active_high_timestamp_ms: int,
        level_valid_timestamp_ms: int,
    ) -> int:
        pump_to_signal_minutes = max((current_timestamp_ms - pump_start_timestamp_ms) / 60_000.0, 0.0)
        active_high_to_signal_minutes = max((current_timestamp_ms - active_high_timestamp_ms) / 60_000.0, 0.0)
        level_life_minutes = max((current_timestamp_ms - level_valid_timestamp_ms) / 60_000.0, 0.0)

        adjustment = 0
        if active_high_to_signal_minutes <= 15.0:
            adjustment += 7
        elif active_high_to_signal_minutes <= 30.0:
            adjustment += 4
        elif active_high_to_signal_minutes <= 45.0:
            adjustment += 1
        elif active_high_to_signal_minutes <= 75.0:
            adjustment -= 2
        elif active_high_to_signal_minutes <= 120.0:
            adjustment -= 5
        elif active_high_to_signal_minutes <= 180.0:
            adjustment -= 8
        else:
            adjustment -= 11

        if pump_to_signal_minutes <= 35.0:
            adjustment += 3
        elif pump_to_signal_minutes <= 60.0:
            adjustment += 1
        elif pump_to_signal_minutes > 150.0:
            adjustment -= 4
        elif pump_to_signal_minutes > 90.0:
            adjustment -= 2

        if level_life_minutes <= 8.0:
            adjustment += 2
        elif level_life_minutes <= 16.0:
            adjustment += 1
        elif level_life_minutes > 35.0:
            adjustment -= 4
        elif level_life_minutes > 24.0:
            adjustment -= 2
        elif level_life_minutes > 16.0:
            adjustment -= 2
        return adjustment

    @staticmethod
    def _resolve_impulse_quality_adjustment(*, stage1: Stage1Context) -> int:
        adjustment = 0

        if stage1.pump_volume_ratio_start >= 40.0:
            adjustment += 6
        elif stage1.pump_volume_ratio_start >= 20.0:
            adjustment += 4
        elif stage1.pump_volume_ratio_start >= 8.0:
            adjustment += 2
        elif stage1.pump_volume_ratio_start < 4.0:
            adjustment -= 2

        if stage1.pump_volume_ratio_continue >= 20.0:
            adjustment += 2
        elif stage1.pump_volume_ratio_continue < 2.0:
            adjustment -= 1

        if stage1.pump_path_efficiency >= 0.55:
            adjustment += 3
        elif stage1.pump_path_efficiency >= 0.40:
            adjustment += 1
        elif stage1.pump_path_efficiency < 0.22:
            adjustment -= 4
        elif stage1.pump_path_efficiency < 0.30:
            adjustment -= 2

        if stage1.pump_counterflow_ratio_5m <= 0.01:
            adjustment += 4
        elif stage1.pump_counterflow_ratio_5m <= 0.03:
            adjustment += 2
        elif stage1.pump_counterflow_ratio_5m <= 0.06:
            adjustment += 0
        elif stage1.pump_counterflow_ratio_5m <= 0.10:
            adjustment -= 2
        elif stage1.pump_counterflow_ratio_5m <= 0.18:
            adjustment -= 4
        else:
            adjustment -= 7

        if stage1.pre_pump_ema_crosses_1h <= 1:
            adjustment += 2
        elif stage1.pre_pump_ema_crosses_1h == 2:
            adjustment += 0
        elif stage1.pre_pump_ema_crosses_1h == 3:
            adjustment -= 2
        elif stage1.pre_pump_ema_crosses_1h == 4:
            adjustment -= 4
        else:
            adjustment -= 6

        if stage1.pre_pump_barcode_fraction_1h <= 0.20:
            adjustment += 1
        elif stage1.pre_pump_barcode_fraction_1h >= 0.55:
            adjustment -= 3
        elif stage1.pre_pump_barcode_fraction_1h >= 0.45:
            adjustment -= 1

        return adjustment

    @staticmethod
    def _resolve_overhead_quality_adjustment(
        *,
        overhead_score: float,
        red_count: int,
    ) -> int:
        if overhead_score <= 0.42 and red_count <= 2:
            return 2
        if overhead_score <= 0.54 and red_count <= 3:
            return 1
        if overhead_score >= 0.85 or red_count >= 7:
            return -5
        if overhead_score >= 0.72 or red_count >= 5:
            return -4
        if overhead_score >= 0.62 or red_count >= 4:
            return -2
        return 0

    def _resolve_signal_bar_context_online(
        self,
        *,
        one: OneMinuteFrame,
        idx: int,
        level: float,
    ) -> dict[str, float]:
        open_price = float(one.opens[idx])
        high_price = float(one.highs[idx])
        low_price = float(one.lows[idx])
        close_price = float(one.closes[idx])
        volume = float(one.volumes[idx])
        bar_range = max(high_price - low_price, self._EPSILON)
        body = abs(close_price - open_price)
        recent_start = max(0, idx - 15)
        recent_volumes = one.volumes[recent_start:idx]
        recent_volume_median = float(np.nanmedian(recent_volumes)) if recent_volumes.size > 0 else np.nan
        volume_vs_recent = (
            self._safe_divide(volume, recent_volume_median)
            if np.isfinite(recent_volume_median) and recent_volume_median > 0.0
            else np.nan
        )
        ema9_series = one.frame["ema9"] if "ema9" in one.frame.columns else None
        ema20_series = one.frame["ema20"] if "ema20" in one.frame.columns else None
        if ema9_series is None or ema20_series is None:
            closes_series = pd.Series(one.closes[: idx + 1], dtype="float64")
            if ema9_series is None:
                ema9_series = closes_series.ewm(span=9, adjust=False).mean()
            if ema20_series is None:
                ema20_series = closes_series.ewm(span=20, adjust=False).mean()
        ema9 = float(ema9_series.iloc[idx]) if idx < len(ema9_series) else np.nan
        ema20 = float(ema20_series.iloc[idx]) if idx < len(ema20_series) else np.nan
        ema20_prev_idx = max(0, idx - 3)
        ema9_prev_idx = max(0, idx - 3)
        ema20_prev = float(ema20_series.iloc[ema20_prev_idx]) if ema20_prev_idx < len(ema20_series) else np.nan
        ema9_prev = float(ema9_series.iloc[ema9_prev_idx]) if ema9_prev_idx < len(ema9_series) else np.nan
        ema9_slope_3 = (
            ((ema9 - ema9_prev) / ema9_prev) * 100.0
            if np.isfinite(ema9) and np.isfinite(ema9_prev) and ema9_prev > 0.0
            else np.nan
        )
        ema_spread_pct = (
            ((ema9 - ema20) / ema20) * 100.0
            if np.isfinite(ema9) and np.isfinite(ema20) and ema20 > 0.0
            else np.nan
        )
        ema20_slope_3 = (
            ((ema20 - ema20_prev) / ema20_prev) * 100.0
            if np.isfinite(ema20) and np.isfinite(ema20_prev) and ema20_prev > 0.0
            else np.nan
        )
        close_clearance_pct = (
            ((close_price - level) / level) * 100.0
            if np.isfinite(level) and level > 0.0
            else np.nan
        )
        return {
            "signal_bar_body_share": body / bar_range,
            "signal_bar_close_position": (close_price - low_price) / bar_range,
            "signal_bar_volume_vs_recent": volume_vs_recent,
            "signal_close_clearance_pct": close_clearance_pct,
            "signal_bar_ema9_slope_3": ema9_slope_3,
            "signal_bar_ema_spread_pct": ema_spread_pct,
            "signal_bar_ema20_slope_3": ema20_slope_3,
        }

    def _resolve_close_trigger_score_adjustment(
        self,
        *,
        stage4: Stage4Context,
        signal_context: dict[str, float],
    ) -> tuple[int, int, int, int]:
        body_share = float(signal_context.get("signal_bar_body_share") or 0.0)
        close_position = float(signal_context.get("signal_bar_close_position") or 0.0)
        volume_vs_recent = float(signal_context.get("signal_bar_volume_vs_recent") or np.nan)
        close_clearance_pct = float(signal_context.get("signal_close_clearance_pct") or np.nan)
        overhead_score = float(stage4.overhead_resistance_score)
        overhead_red_count = int(stage4.overhead_red_count)

        if close_position >= 0.90 and close_clearance_pct >= 0.25:
            body_close_adj = 2
        elif close_position >= 0.82 and close_clearance_pct >= 0.12 and body_share >= 0.35:
            body_close_adj = 1
        elif close_position < 0.72 or close_clearance_pct < 0.08:
            body_close_adj = -2
        elif close_position < 0.80 or close_clearance_pct < 0.12:
            body_close_adj = -1
        else:
            body_close_adj = 0

        if overhead_score <= 0.50 and overhead_red_count <= 2:
            overhead_adj = 4
        elif overhead_score <= 0.58 and overhead_red_count <= 3:
            overhead_adj = 2
        elif overhead_score >= 0.70 or overhead_red_count >= 5:
            overhead_adj = -5
        elif overhead_score >= 0.62 or overhead_red_count >= 4:
            overhead_adj = -3
        elif overhead_score >= 0.58:
            overhead_adj = -1
        else:
            overhead_adj = 0

        if np.isfinite(volume_vs_recent) and volume_vs_recent >= 3.0:
            volume_adj = 1
        elif np.isfinite(volume_vs_recent) and volume_vs_recent < 0.70:
            volume_adj = -1
        else:
            volume_adj = 0

        total_adj = int(body_close_adj + overhead_adj + volume_adj)
        return body_close_adj, overhead_adj, volume_adj, total_adj

    def _resolve_close_trigger_filter_reason(
        self,
        *,
        params: PnoParams,
        stage1: Stage1Context,
        stage3: Stage3Context,
        stage4: Stage4Context,
        signal_context: dict[str, float],
    ) -> str | None:
        entry_pos = float(stage4.entry_pos)
        clean_structure_override = bool(
            entry_pos <= 0.55
            and float(stage4.overhead_resistance_score) <= 0.58
            and int(stage4.touches) <= 3
            and float(stage3.post_high_body_overlap_rate) <= 0.82
            and float(stage3.post_high_wick_share) <= min(float(params.close_above_max_post_high_wick_share), 0.72)
        )
        if (
            stage4.level_maturity_fraction >= 0.90
            and int(stage4.touches) >= 5
            and entry_pos >= 0.78
            and float(stage4.level_life_ema_spread_growth_share) < 0.20
        ):
            return "close_above_level_too_stale"
        if entry_pos < float(params.close_above_min_entry_pos):
            return "close_above_entry_pos_too_low"
        if entry_pos > float(params.close_above_max_entry_pos):
            return "close_above_entry_pos_too_high"
        pullback_fraction = self._safe_divide(float(stage3.pullback_depth), max(float(stage4.active_high) - float(stage3.pullback_low), self._EPSILON))
        if pullback_fraction > float(params.close_above_max_pullback_fraction_of_leg):
            return "close_above_pullback_too_deep"
        if float(stage1.active_high_bar_upper_wick_share) > float(params.close_above_max_active_high_upper_wick_share):
            return "close_above_active_high_upper_wick_too_heavy"
        if float(stage1.active_high_bar_close_position) < float(params.close_above_min_active_high_close_position):
            return "close_above_active_high_closed_too_low"
        if float(stage3.post_high_chop_alternation_rate) < float(params.close_above_min_post_high_alternation_rate):
            return "close_above_post_high_alternation_too_low"
        signal_volume_vs_recent = float(signal_context.get("signal_bar_volume_vs_recent") or np.nan)
        if (
            float(stage1.pump_volume_ratio_start) < 2.5
            and float(stage1.pump_path_efficiency) < 0.50
        ):
            return "close_above_pump_too_weak_into_active_high"
        if (
            float(stage1.pump_volume_ratio_start) < 4.0
            and float(stage1.pump_path_efficiency) < 0.40
        ):
            return "close_above_pump_tape_too_thin"
        if (
            float(stage1.active_high_bar_upper_wick_share) >= 0.60
            and float(stage1.active_high_bar_close_position) <= 0.40
        ):
            return "close_above_active_high_too_exhausted"
        if float(stage3.post_high_wick_share) > float(params.close_above_max_post_high_wick_share):
            return "close_above_post_high_wick_too_high"
        if float(stage4.overhead_resistance_score) >= 0.76 and int(stage4.overhead_red_count) >= 5:
            return "close_above_overhead_too_heavy"
        signal_close_position = float(signal_context.get("signal_bar_close_position") or np.nan)
        if (
            np.isfinite(signal_close_position)
            and signal_close_position < float(params.close_above_min_signal_close_position_in_chop)
            and float(stage3.post_high_body_overlap_rate) >= float(params.close_above_choppy_overlap_threshold)
        ):
            return "close_above_signal_closed_too_low_in_chop"
        if (
            np.isfinite(signal_close_position)
            and float(stage3.post_high_body_overlap_rate) >= 0.95
            and float(stage3.post_high_wick_share) >= 0.55
            and signal_close_position < 0.72
        ):
            return "close_above_chop_reclaim_too_noisy"
        signal_ema9_slope_3 = float(signal_context.get("signal_bar_ema9_slope_3") or np.nan)
        min_signal_ema9_slope_3 = float(params.close_above_min_signal_ema9_slope_3)
        if min_signal_ema9_slope_3 > 0.0:
            if not np.isfinite(signal_ema9_slope_3):
                if not clean_structure_override:
                    return "close_above_signal_ema9_slope_missing"
            elif (
                signal_ema9_slope_3 < min_signal_ema9_slope_3
                and (
                    not clean_structure_override
                    or signal_ema9_slope_3 < (0.65 * min_signal_ema9_slope_3)
                )
            ):
                return "close_above_signal_ema9_slope_too_low"
        signal_ema20_slope_3 = float(signal_context.get("signal_bar_ema20_slope_3") or np.nan)
        min_signal_ema20_slope_3 = float(params.close_above_min_signal_ema20_slope_3)
        if min_signal_ema20_slope_3 > 0.0:
            if not np.isfinite(signal_ema20_slope_3):
                if not clean_structure_override:
                    return "close_above_signal_ema20_slope_missing"
            elif (
                signal_ema20_slope_3 < min_signal_ema20_slope_3
                and (
                    not clean_structure_override
                    or signal_ema20_slope_3 < (0.65 * min_signal_ema20_slope_3)
                )
            ):
                return "close_above_signal_ema20_slope_too_low"
        signal_ema_spread_pct = float(signal_context.get("signal_bar_ema_spread_pct") or np.nan)
        min_signal_ema_spread_pct = float(params.close_above_min_signal_ema_spread_pct)
        if min_signal_ema_spread_pct > 0.0:
            if not np.isfinite(signal_ema_spread_pct):
                if not clean_structure_override:
                    return "close_above_signal_ema_spread_missing"
            elif (
                signal_ema_spread_pct < min_signal_ema_spread_pct
                and (
                    not clean_structure_override
                    or signal_ema_spread_pct < (0.70 * min_signal_ema_spread_pct)
                )
            ):
                return "close_above_signal_ema_spread_too_tight"
        if np.isfinite(signal_volume_vs_recent) and signal_volume_vs_recent < float(params.close_above_min_signal_volume_vs_recent):
            return "close_above_signal_volume_too_low"
        if not np.isfinite(signal_volume_vs_recent) and float(params.close_above_min_signal_volume_vs_recent) > 0.0:
            return "close_above_signal_volume_missing"
        return None
        
        # Если главный хай уже есть и достаточный рост, объем не является ключевым фактором
        active_high = float(stage4.active_high)
        pullback_depth = float(stage3.pullback_depth)
        has_sufficient_growth = pullback_depth >= (float(params.pullback_min_v1) * 0.5)  # Используем половину минимального значения как порог
        
        if active_high > 0 and has_sufficient_growth:
            return None
        
        signal_volume_vs_recent = float(signal_context.get("signal_bar_volume_vs_recent") or np.nan)
        if np.isfinite(signal_volume_vs_recent) and signal_volume_vs_recent < float(params.close_above_min_signal_volume_vs_recent):
            return "close_above_signal_volume_too_low"
        if not np.isfinite(signal_volume_vs_recent) and float(params.close_above_min_signal_volume_vs_recent) > 0.0:
            return "close_above_signal_volume_missing"
        return None

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
            if high_price < armed.stage4.level:
                return None, entry_idx
            entry_price = max(open_price, float(armed.stage4.entry_plan))
            stop_loss = float(armed.stage4.low_last_red_plan)
        elif confirmation_mode == "close_above":
            if high_price < armed.stage4.level or close_price <= armed.stage4.level:
                return None, entry_idx
            ideal_like_impulse = self._is_ideal_like_impulse(stage1=armed.stage1, params=params)
            close_above_decay_reason = self._resolve_close_above_pre_signal_decay_reason(
                one=one,
                signal_idx=entry_idx,
                params=params,
                stage4=armed.stage4,
            )
            if (
                ideal_like_impulse
                and bool(getattr(params, "ideal_like_ignore_decay_invalidation", False))
                and close_above_decay_reason == "level_already_accepted_before_signal"
            ):
                close_above_decay_reason = None
            if armed.stage4.structure_source == "human_bos":
                close_above_decay_reason = None
            if close_above_decay_reason is not None:
                return None, entry_idx
            if armed.stage4.structure_source == "human_bos":
                entry_price = max(open_price, float(armed.stage4.entry_plan))
            else:
                actual_entry_idx = entry_idx + 1
                if actual_entry_idx >= len(one.timestamps):
                    return None, entry_idx
                entry_price = float(one.opens[actual_entry_idx])
            stop_loss = float(armed.stage4.low_last_red_plan)
            signal_kind = "close_above"
        else:
            return None, entry_idx

        ideal_like_impulse = self._is_ideal_like_impulse(stage1=armed.stage1, params=params)
        entry_pos_reference_high = float(armed.stage4.active_high)
        if ideal_like_impulse and float(armed.stage4.tp1) > (entry_pos_reference_high + self._EPSILON):
            entry_pos_reference_high = float(armed.stage4.tp1)
        actual_entry_pos = self._resolve_entry_pullback_fraction(
            pullback_low=float(armed.stage3.pullback_low),
            active_high=entry_pos_reference_high,
            entry_price=float(entry_price),
        )
        max_actual_entry_pos = (
            float(params.close_above_max_entry_pos)
            if confirmation_mode == "close_above"
            else float(params.max_entry_pullback_fraction)
        )
        if actual_entry_pos > max_actual_entry_pos:
            return None, entry_idx

        trigger_body_close_adj = 0
        trigger_overhead_adj = 0
        trigger_volume_adj = 0
        trigger_score_adjustment = 0
        trigger_adjusted_final_score = float(armed.stage4.final_score)
        signal_context: dict[str, float] = {}
        if confirmation_mode == "close_above":
            signal_context = self._resolve_signal_bar_context_online(
                one=one,
                idx=entry_idx,
                level=float(armed.stage4.level),
            )
            if armed.stage4.structure_source != "human_bos":
                (
                    trigger_body_close_adj,
                    trigger_overhead_adj,
                    trigger_volume_adj,
                    trigger_score_adjustment,
                ) = self._resolve_close_trigger_score_adjustment(
                    stage4=armed.stage4,
                    signal_context=signal_context,
                )
                close_trigger_filter_reason = self._resolve_close_trigger_filter_reason(
                    params=params,
                    stage1=armed.stage1,
                    stage3=armed.stage3,
                    stage4=armed.stage4,
                    signal_context=signal_context,
                )
            else:
                close_trigger_filter_reason = None
            if (
                ideal_like_impulse
                and close_trigger_filter_reason in {
                    "close_above_signal_closed_too_low_in_chop",
                    "close_above_chop_reclaim_too_noisy",
                }
            ):
                close_trigger_filter_reason = None
            if close_trigger_filter_reason is not None:
                return None, entry_idx
            trigger_adjusted_final_score = float(armed.stage4.final_score + trigger_score_adjustment)

        # If the actual executable entry is already above the original payoff geometry,
        # the setup has decayed and should wait for a new valid level/high cycle instead
        # of forcing a nonsensical late entry.
        active_high_price = float(armed.stage4.active_high)
        tp1_price = float(armed.stage4.tp1)
        tp2_price = float(armed.stage4.tp2)
        allow_entry_above_active_high = ideal_like_impulse and tp1_price > (active_high_price + self._EPSILON)
        if (
            stop_loss >= (entry_price - self._EPSILON)
            or ((not allow_entry_above_active_high) and entry_price >= (active_high_price - self._EPSILON))
            or entry_price >= (tp1_price - self._EPSILON)
            or tp2_price <= (entry_price + self._EPSILON)
        ):
            return None, entry_idx

        # Check RR using actual entry price and stop loss (stage 5 validation)
        actual_risk = entry_price - stop_loss
        net_tp1_move = tp1_price - entry_price - (float(params.fee_rate) * entry_price) - (0.5 * float(params.fee_rate) * tp1_price)
        if actual_risk <= self._EPSILON or self._safe_divide(net_tp1_move, actual_risk) <= (
            float(params.min_entry_rr) + self._EPSILON
        ):
            return None, entry_idx

        position_size = self._resolve_position_size(params=params, entry_price=entry_price, stop_loss=stop_loss)
        if position_size <= 0.0:
            return None, entry_idx

        pump_to_peak_bars = max(armed.stage1.active_high_idx - armed.stage1.start_idx, 1)
        levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
        htf_bars_since_active_high = int(
            max(
                (int(one.timestamps[actual_entry_idx]) - int(armed.stage4.active_high_timestamp))
                // max(levels_timeframe_ms, 1),
                0,
            )
        )
        metadata: dict[str, int | float | str | bool | None] = {
            "strategy_id": "pno",
            "symbol": params.symbol,
            "stage_path": PNO_STAGE_PATH,
            "fee_rate": round(float(params.fee_rate), 6),
            "sleep_start_timestamp_ms": int(armed.stage1.sleep_start_timestamp),
            "sleep_end_timestamp_ms": int(armed.stage1.sleep_end_timestamp),
            "pump_start_timestamp_ms": int(armed.stage1.pump_start_timestamp),
            "stage1_confirm_timestamp_ms": int(armed.stage1.stage1_confirm_timestamp),
            "active_high_timestamp_ms": int(armed.stage1.active_high_timestamp),
            "active_high": round(float(armed.stage1.active_high), 8),
            "leg_start": round(float(armed.stage1.leg_start), 8),
            "leg_size": round(float(armed.stage1.leg_size), 8),
            "stage1_hold_price": round(float(armed.stage1.stage1_hold_price), 8),
            "hold_floor": round(float(armed.stage1.hold_floor), 8),
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
            "pump_path_efficiency": round(float(armed.stage1.pump_path_efficiency), 4),
            "pump_wick_share": round(float(armed.stage1.pump_wick_share), 4),
            "pump_body_share_mean": round(float(armed.stage1.pump_body_share_mean), 4),
            "pump_flat_body_share": round(float(armed.stage1.pump_flat_body_share), 4),
            "pump_body_wick_edge": round(float(armed.stage1.pump_body_wick_edge), 4),
            "pump_micro_flat_bar_share": round(float(armed.stage1.pump_micro_flat_bar_share), 4),
            "active_high_bar_body_share": round(float(armed.stage1.active_high_bar_body_share), 4),
            "active_high_bar_upper_wick_share": round(float(armed.stage1.active_high_bar_upper_wick_share), 4),
            "active_high_bar_close_position": round(float(armed.stage1.active_high_bar_close_position), 4),
            "pump_max_red_body_share_5m": round(float(armed.stage1.pump_max_red_body_share_5m), 4),
            "pump_counterflow_ratio_5m": round(float(armed.stage1.pump_counterflow_ratio_5m), 4),
            "pump_max_red_body_share_1m": round(float(armed.stage1.pump_max_red_body_share_1m), 4),
            "pump_counterflow_ratio_1m": round(float(armed.stage1.pump_counterflow_ratio_1m), 4),
            "hold_status_at_validation": str(armed.stage1.hold_status_at_validation),
            "leg_start_status_at_validation": str(armed.stage1.leg_start_status_at_validation),
            "pullback_low": round(float(armed.stage3.pullback_low), 8),
            "pullback_depth": round(float(armed.stage3.pullback_depth), 8),
            "post_high_ema20_pierce_count": int(armed.stage3.post_high_ema20_pierce_count),
            "post_high_close_below_ema20_count": int(armed.stage3.post_high_close_below_ema20_count),
            "post_high_wick_share": round(float(armed.stage3.post_high_wick_share), 4),
            "post_high_body_overlap_rate": round(float(armed.stage3.post_high_body_overlap_rate), 4),
            "post_high_max_red_body_share": round(float(armed.stage3.post_high_max_red_body_share), 4),
            "level": round(float(armed.stage4.level), 8),
            "level_first_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_first_idx]),
            "level_last_local_high_timestamp_ms": int(one.timestamps[armed.stage4.cluster_last_idx]),
            "level_valid_timestamp_ms": int(armed.stage4.level_valid_timestamp),
            "level_maturity_fraction": round(float(armed.stage4.level_maturity_fraction), 4),
            "level_age_bars": int(armed.stage4.level_age_bars),
            "entry_pos": round(float(armed.stage4.entry_pos), 4),
            "pullback_fraction_of_leg": round(
                self._safe_divide(
                    float(armed.stage3.pullback_depth),
                    max(float(armed.stage4.active_high) - float(armed.stage3.pullback_low), self._EPSILON),
                ),
                4,
            ),
            "touches": int(armed.stage4.touches),
            "pno_index": int(armed.stage4.pno_index),
            "pullback_base_low": round(float(armed.stage4.pullback_base_low), 8) if armed.stage4.pullback_base_low is not None else None,
            "pullback_base_high": round(float(armed.stage4.pullback_base_high), 8) if armed.stage4.pullback_base_high is not None else None,
            "pullback_base_start_timestamp_ms": int(one.timestamps[armed.stage4.pullback_base_start_idx]) if armed.stage4.pullback_base_start_idx is not None else None,
            "pullback_base_end_timestamp_ms": int(one.timestamps[armed.stage4.pullback_base_end_idx]) if armed.stage4.pullback_base_end_idx is not None else None,
            "pullback_base_quality": round(float(armed.stage4.pullback_base_quality), 4),
            "pullback_base_left_vacuum": round(float(armed.stage4.pullback_base_left_vacuum), 4),
            "overhead_resistance_score": round(float(armed.stage4.overhead_resistance_score), 4),
            "overhead_resistance_penalty": int(armed.stage4.overhead_resistance_penalty),
            "overhead_red_body_share": round(float(armed.stage4.overhead_red_body_share), 4),
            "overhead_red_count": int(armed.stage4.overhead_red_count),
            "dominant_overhead_red_timestamp_ms": int(armed.stage4.dominant_overhead_red_timestamp) if armed.stage4.dominant_overhead_red_timestamp is not None else None,
            "dominant_overhead_red_high": round(float(armed.stage4.dominant_overhead_red_high), 8) if armed.stage4.dominant_overhead_red_high is not None else None,
            "dominant_overhead_red_body": round(float(armed.stage4.dominant_overhead_red_body), 8),
            "entry_confirmation_mode": confirmation_mode,
            "entry_signal_kind": signal_kind,
            "be_arm_to_active_high_fraction": round(float(params.be_arm_to_active_high_fraction), 4),
            "be_buffer_r_fraction": round(float(params.be_buffer_r_fraction), 4),
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
            "base_final_score": round(float(armed.stage4.final_score), 4),
            "final_score": round(float(trigger_adjusted_final_score), 4),
            "trigger_score_body_close": int(trigger_body_close_adj),
            "trigger_score_overhead": int(trigger_overhead_adj),
            "trigger_score_volume": int(trigger_volume_adj),
            "trigger_score_adjustment": int(trigger_score_adjustment),
            "low_last_red_plan": round(float(armed.stage4.low_last_red_plan), 8),
            "entry_price_planned_slip": round(float(armed.stage4.entry_plan - armed.stage4.level), 8),
            "pump_to_peak_bars": int(pump_to_peak_bars),
            "pump_to_peak_minutes": float(pump_to_peak_bars),
            "entry_price_actual": round(float(entry_price), 8),
            "sl_actual": round(float(stop_loss), 8),
            "htf_bars_since_active_high": int(htf_bars_since_active_high),
        }
        if signal_context:
            signal_volume_vs_recent = float(signal_context["signal_bar_volume_vs_recent"])
            signal_close_clearance_pct = float(signal_context["signal_close_clearance_pct"])
            metadata.update(
                {
                    "signal_bar_body_share": round(float(signal_context["signal_bar_body_share"]), 4),
                    "signal_bar_close_position": round(float(signal_context["signal_bar_close_position"]), 4),
                    "signal_bar_volume_vs_recent": (
                        round(signal_volume_vs_recent, 4) if np.isfinite(signal_volume_vs_recent) else np.nan
                    ),
                    "signal_close_clearance_pct": (
                        round(signal_close_clearance_pct, 4) if np.isfinite(signal_close_clearance_pct) else np.nan
                    ),
                    "signal_bar_ema9_slope_3": (
                        round(float(signal_context["signal_bar_ema9_slope_3"]), 4)
                        if np.isfinite(float(signal_context["signal_bar_ema9_slope_3"]))
                        else np.nan
                    ),
                    "signal_bar_ema_spread_pct": (
                        round(float(signal_context["signal_bar_ema_spread_pct"]), 4)
                        if np.isfinite(float(signal_context["signal_bar_ema_spread_pct"]))
                        else np.nan
                    ),
                    "signal_bar_ema20_slope_3": (
                        round(float(signal_context["signal_bar_ema20_slope_3"]), 4)
                        if np.isfinite(float(signal_context["signal_bar_ema20_slope_3"]))
                        else np.nan
                    ),
                }
            )

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

    def _resolve_be_arm_fraction(
        self,
        *,
        params: PnoParams,
        confirmation_mode: str,
        bars_since_entry_after: int,
    ) -> float:
        if confirmation_mode != "close_above":
            return float(params.be_arm_to_active_high_fraction)
        steps = max(int(bars_since_entry_after), 0) // max(int(params.close_above_be_step_bars), 1)
        fraction = float(params.close_above_be_start_fraction) - (float(params.close_above_be_step_fraction) * steps)
        return max(float(params.close_above_be_min_fraction), fraction)

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
        initial_stop_loss = float(stop_loss)
        initial_risk = max(entry_price - initial_stop_loss, self._EPSILON)
        tp1_share = float(params.tp1_share)
        remainder_share = max(1.0 - tp1_share, 0.0)
        confirmation_mode = str(metadata.get("entry_confirmation_mode", getattr(params, "entry_confirmation_mode", "cross")))
        initial_be_arm_fraction = self._resolve_be_arm_fraction(
            params=params,
            confirmation_mode=confirmation_mode,
            bars_since_entry_after=0,
        )
        be_arm_price = entry_price + (initial_be_arm_fraction * max(tp1 - entry_price, 0.0))
        be_buffer = max(entry_price * float(params.min_tick_fraction), float(params.be_buffer_r_fraction) * initial_risk)
        be_protect_price = max(be_fee, entry_price + be_buffer)
        tp1_be_protect_price = float(be_protect_price)
        be_arm_r = self._safe_divide(be_arm_price - entry_price, initial_risk)
        be_armed = False
        be_arm_idx: int | None = None
        be_arm_fraction_at_trigger: float | None = None
        be_arm_price_at_trigger: float | None = None
        tp1_hit = False
        tp1_hit_idx: int | None = None
        tp2_hit_idx: int | None = None
        runner_stop_after_tp1 = float(tp1_be_protect_price)
        runner_high_watermark = float(tp1)
        partial_exit_price: float | None = None
        partial_exit_idx: int | None = None
        partial_realized_pnl = 0.0
        runner_exit_price: float | None = None
        runner_exit_idx: int | None = None
        runner_realized_pnl = 0.0
        runner_exit_reason: str | None = None
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
            bars_since_entry_after = max(idx - entry_idx - 1, 0)
            current_be_arm_fraction = self._resolve_be_arm_fraction(
                params=params,
                confirmation_mode=confirmation_mode,
                bars_since_entry_after=bars_since_entry_after,
            )
            current_be_arm_price = entry_price + (current_be_arm_fraction * max(tp1 - entry_price, 0.0))
            max_favorable = max(max_favorable, high - entry_price)
            max_adverse = max(max_adverse, entry_price - low)
            active_stop = be_protect_price if be_armed else initial_stop_loss

            if not tp1_hit:
                if low <= active_stop:
                    exit_price = active_stop
                    exit_idx = idx
                    result_type = TradeResultType.BE if be_armed else TradeResultType.SL
                    runner_exit_price = active_stop
                    runner_exit_idx = idx
                    runner_exit_reason = "be_pre_tp1" if be_armed else "sl_non_entry"
                    runner_realized_pnl = self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=active_stop,
                        quantity=position_size,
                        fee_rate=fee_rate,
                    )
                    realized_pnl = runner_realized_pnl
                    category = str(runner_exit_reason)
                    break
                if high >= tp1:
                    tp1_hit = True
                    tp1_hit_idx = idx
                    partial_exit_price = tp1
                    partial_exit_idx = idx
                    if not be_armed:
                        be_armed = True
                        be_arm_idx = idx
                        be_arm_fraction_at_trigger = current_be_arm_fraction
                        be_arm_price_at_trigger = current_be_arm_price
                    partial_realized_pnl = self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=tp1,
                        quantity=position_size * tp1_share,
                        fee_rate=fee_rate,
                    )
                    realized_pnl += partial_realized_pnl
                    if high >= tp2:
                        exit_price = tp2
                        exit_idx = idx
                        tp2_hit_idx = idx
                        result_type = TradeResultType.TP2
                        runner_exit_price = tp2
                        runner_exit_idx = idx
                        runner_exit_reason = "tp2"
                        runner_realized_pnl = self._net_leg_pnl(
                            entry_price=entry_price,
                            exit_price=tp2,
                            quantity=position_size * remainder_share,
                            fee_rate=fee_rate,
                        )
                        realized_pnl += runner_realized_pnl
                        category = "tp2"
                        break
                    continue
                if (not be_armed) and high >= current_be_arm_price:
                    be_armed = True
                    be_arm_idx = idx
                    be_arm_fraction_at_trigger = current_be_arm_fraction
                    be_arm_price_at_trigger = current_be_arm_price
            elif tp1_hit_idx is not None and idx > tp1_hit_idx:
                runner_stop_after_tp1, runner_high_watermark = self._resolve_post_tp1_trailing_stop(
                    one=one,
                    tp1_hit_idx=tp1_hit_idx,
                    current_idx=idx,
                    previous_high_watermark=runner_high_watermark,
                    current_high=high,
                    current_stop=runner_stop_after_tp1,
                    be_protect_price=be_protect_price,
                )
                if low <= runner_stop_after_tp1:
                    exit_price = runner_stop_after_tp1
                    exit_idx = idx
                    result_type = TradeResultType.TP1_BE
                    runner_exit_price = runner_stop_after_tp1
                    runner_exit_idx = idx
                    runner_exit_reason = "tp1_be"
                    runner_realized_pnl = self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=runner_stop_after_tp1,
                        quantity=position_size * remainder_share,
                        fee_rate=fee_rate,
                    )
                    realized_pnl += runner_realized_pnl
                    category = "tp1_be"
                    break
                if high >= tp2:
                    exit_price = tp2
                    exit_idx = idx
                    tp2_hit_idx = idx
                    result_type = TradeResultType.TP2
                    runner_exit_price = tp2
                    runner_exit_idx = idx
                    runner_exit_reason = "tp2"
                    runner_realized_pnl = self._net_leg_pnl(
                        entry_price=entry_price,
                        exit_price=tp2,
                        quantity=position_size * remainder_share,
                        fee_rate=fee_rate,
                    )
                    realized_pnl += runner_realized_pnl
                    category = "tp2"
                    break

            if idx == (len(one.timestamps) - 1):
                exit_idx = idx
                exit_price = close

        if category == "incomplete":
            return None, entry_idx
        if result_type not in {TradeResultType.SL, TradeResultType.BE, TradeResultType.TP1_BE, TradeResultType.TP2}:
            return None, entry_idx

        trade_metadata = dict(metadata)
        trade_metadata.update(
            {
                "category": category,
                "be_fee": round(float(be_fee), 8),
                "initial_stop_loss": round(float(initial_stop_loss), 8),
                "initial_risk": round(float(initial_risk), 8),
                "tp1_share": round(float(tp1_share), 4),
                "runner_share": round(float(remainder_share), 4),
                "be_arm_price": round(float(be_arm_price), 8),
                "be_arm_start_fraction": round(float(initial_be_arm_fraction), 4),
                "be_arm_step_fraction": round(float(params.close_above_be_step_fraction), 4),
                "be_arm_step_bars": int(params.close_above_be_step_bars),
                "be_arm_min_fraction": round(float(params.close_above_be_min_fraction), 4),
                "be_arm_price_at_trigger": round(float(be_arm_price_at_trigger), 8) if be_arm_price_at_trigger is not None else None,
                "be_arm_fraction_at_trigger": round(float(be_arm_fraction_at_trigger), 4) if be_arm_fraction_at_trigger is not None else None,
                "be_arm_r": round(float(be_arm_r), 6) if np.isfinite(be_arm_r) else np.nan,
                "be_buffer": round(float(be_buffer), 8),
                "be_protect_price": round(float(be_protect_price), 8),
                "be_protect_r": round(self._safe_divide(be_protect_price - entry_price, initial_risk), 6),
            "tp1_be_protect_price": round(float(tp1_be_protect_price), 8),
            "runner_stop_after_tp1": round(float(runner_stop_after_tp1), 8),
            "tp1_be_protect_r": round(self._safe_divide(tp1_be_protect_price - entry_price, initial_risk), 6),
                "be_armed": bool(be_armed),
                "be_arm_timestamp_ms": int(one.timestamps[be_arm_idx]) if be_arm_idx is not None else None,
                "tp1_hit_timestamp_ms": int(one.timestamps[tp1_hit_idx]) if tp1_hit_idx is not None else None,
                "tp2_hit_timestamp_ms": int(one.timestamps[tp2_hit_idx]) if tp2_hit_idx is not None else None,
                "tp1_r": round(self._safe_divide(tp1 - entry_price, initial_risk), 6),
                "tp2_r": round(self._safe_divide(tp2 - entry_price, initial_risk), 6),
                "active_high_r": round(self._safe_divide(tp1 - entry_price, initial_risk), 6),
                "partial_exit_price": round(float(partial_exit_price), 8) if partial_exit_price is not None else None,
                "partial_exit_timestamp_ms": int(one.timestamps[partial_exit_idx]) if partial_exit_idx is not None else None,
                "partial_exit_share": round(float(tp1_share), 4) if tp1_hit else 0.0,
                "partial_realized_pnl": round(float(partial_realized_pnl), 8),
                "runner_exit_price": round(float(runner_exit_price), 8) if runner_exit_price is not None else None,
                "runner_exit_timestamp_ms": int(one.timestamps[runner_exit_idx]) if runner_exit_idx is not None else None,
                "runner_exit_share": round(float(remainder_share if tp1_hit else 1.0), 4),
                "runner_realized_pnl": round(float(runner_realized_pnl), 8),
                "runner_exit_reason": runner_exit_reason,
                "be_armed_pre_tp1": bool(be_arm_idx is not None and (tp1_hit_idx is None or be_arm_idx < tp1_hit_idx)),
                "holding_bars": int(exit_idx - entry_idx),
                "mfe_r": round(self._safe_divide(max_favorable, initial_risk), 6),
                "mae_r": round(self._safe_divide(max_adverse, initial_risk), 6),
                "mfe_pct": round(self._to_percent(max_favorable, entry_price), 6),
                "mae_pct": round(self._to_percent(max_adverse, entry_price), 6),
                "tp1_hit": tp1_hit,
                "tp1_hit_share": round(float(tp1_share), 4) if tp1_hit else 0.0,
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
        configured_trade_risk = params.pno_r_trade if params.pno_r_trade is not None else (params.pno_deposit * params.pno_risk_pct)
        trade_risk = min(float(configured_trade_risk), float(params.pno_deposit) * float(PNO_DEFAULT_RISK_PCT))
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
    def _range_tree_query_value(tree: RangeSearchTree, *, left: int, right: int) -> float:
        if left > right:
            return np.inf if tree.is_min_tree else -np.inf
        left_idx = left + tree.size
        right_idx = right + tree.size
        result = np.inf if tree.is_min_tree else -np.inf
        while left_idx <= right_idx:
            if left_idx & 1:
                node_value = float(tree.tree[left_idx])
                result = min(result, node_value) if tree.is_min_tree else max(result, node_value)
                left_idx += 1
            if not (right_idx & 1):
                node_value = float(tree.tree[right_idx])
                result = min(result, node_value) if tree.is_min_tree else max(result, node_value)
                right_idx -= 1
            left_idx //= 2
            right_idx //= 2
        return float(result)

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
    ) -> np.ndarray:
        if end_idx - start_idx < 2 or one.confirmed_high_indices.size == 0:
            return np.empty(0, dtype=np.int64)
        upper_bound = min(end_idx - 1, len(one.timestamps) - 2)
        left = int(np.searchsorted(one.confirmed_high_indices, max(start_idx, 1), side="left"))
        right = int(np.searchsorted(one.confirmed_high_indices, upper_bound, side="right"))
        if left >= right:
            return np.empty(0, dtype=np.int64)
        indices = one.confirmed_high_indices[left:right]
        confirmed_at = one.confirmed_high_confirmed_at[left:right]
        mask = confirmed_at <= end_idx
        if not np.any(mask):
            return np.empty(0, dtype=np.int64)
        return indices[mask]

    def _resolve_confirmed_lows(
        self,
        *,
        one: OneMinuteFrame,
        start_idx: int,
        end_idx: int,
    ) -> np.ndarray:
        if end_idx - start_idx < 2 or one.confirmed_low_indices.size == 0:
            return np.empty(0, dtype=np.int64)
        upper_bound = min(end_idx - 1, len(one.timestamps) - 2)
        left = int(np.searchsorted(one.confirmed_low_indices, max(start_idx, 1), side="left"))
        right = int(np.searchsorted(one.confirmed_low_indices, upper_bound, side="right"))
        if left >= right:
            return np.empty(0, dtype=np.int64)
        indices = one.confirmed_low_indices[left:right]
        confirmed_at = one.confirmed_low_confirmed_at[left:right]
        mask = confirmed_at <= end_idx
        if not np.any(mask):
            return np.empty(0, dtype=np.int64)
        return indices[mask]

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
    def _bars_for_duration(timeframe_ms: int, duration_ms: int) -> int:
        if timeframe_ms <= 0:
            return 1
        return max(1, (duration_ms + timeframe_ms - 1) // timeframe_ms)

    @staticmethod
    def _scale_required_bars(*, base_bars: int, base_timeframe_ms: int, timeframe_ms: int) -> int:
        if base_bars <= 0 or base_timeframe_ms <= 0 or timeframe_ms <= 0:
            return 1
        duration_ms = base_bars * base_timeframe_ms
        return max(1, (duration_ms + timeframe_ms - 1) // timeframe_ms)

    @staticmethod
    def _threshold_count(window: int, *, numerator: int, denominator: int) -> int:
        if window <= 0 or numerator <= 0 or denominator <= 0:
            return 1
        return max(1, (window * numerator + denominator - 1) // denominator)

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
    def _aggregate_frame(frame: pd.DataFrame, *, target_timeframe_ms: int) -> pd.DataFrame:
        work = frame.copy()
        source_timeframe_ms = PnoEngine._infer_timeframe_ms(work)
        if source_timeframe_ms is None or source_timeframe_ms <= 0 or target_timeframe_ms <= 0:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        if target_timeframe_ms < source_timeframe_ms or target_timeframe_ms % source_timeframe_ms != 0:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        work["bucket"] = (work["timestamp"] // target_timeframe_ms) * target_timeframe_ms
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
    def _aggregate_to_5m(frame: pd.DataFrame) -> pd.DataFrame:
        return PnoEngine._aggregate_frame(frame, target_timeframe_ms=Timeframe.M5.to_milliseconds())

    def _materialize_sparse_entry_frame(
        self,
        *,
        levels_frame: pd.DataFrame,
        source_entry_frame: pd.DataFrame,
        params: PnoParams,
        stage1_state: Stage1StateArrays | None,
        seconds_frame_provider: object,
    ) -> pd.DataFrame:
        del source_entry_frame
        loader = getattr(seconds_frame_provider, "load_aggregated_window", None)
        if not callable(loader):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        windows = self._resolve_stage1_fetch_windows(
            levels_frame=levels_frame,
            params=params,
            stage1_state=stage1_state,
        )
        if not windows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        frames: list[pd.DataFrame] = []
        for start_timestamp_ms, end_timestamp_ms in windows:
            frame = loader(
                symbol=params.symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
                target_timeframe=params.entry_timeframe,
            )
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frames.append(self.prepare_data(frame))
        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        materialized = pd.concat(frames, ignore_index=True)
        return self.prepare_data(materialized)

    def _resolve_stage1_fetch_windows(
        self,
        *,
        levels_frame: pd.DataFrame,
        params: PnoParams,
        stage1_state: Stage1StateArrays | None,
    ) -> list[tuple[int, int]]:
        timestamps = pd.to_numeric(levels_frame["timestamp"], errors="coerce").to_numpy(dtype=np.int64)
        highs = pd.to_numeric(levels_frame["high"], errors="coerce").to_numpy(dtype=np.float64)
        lows = pd.to_numeric(levels_frame["low"], errors="coerce").to_numpy(dtype=np.float64)
        if timestamps.size == 0:
            return []

        candidate_indices = (
            np.nonzero(stage1_state.inplay)[0].astype(np.int64, copy=False)
            if stage1_state is not None and stage1_state.has_inplay
            else np.arange(len(timestamps), dtype=np.int64)
        )
        raw_windows: list[tuple[int, int]] = []
        levels_timeframe_ms = int(params.levels_timeframe.to_milliseconds())
        entry_preroll_ms = max(30 * 60_000, 6 * int(params.entry_timeframe.to_milliseconds()))
        for five_idx in candidate_indices.tolist():
            candidate = self._resolve_htf_stage1_candidate_from_arrays(
                timestamps=timestamps,
                highs=highs,
                lows=lows,
                five_idx=int(five_idx),
                params=params,
            )
            if candidate is None:
                continue
            end_idx = min(
                int(candidate["active_high_5m_idx"])
                + int(params.stage1_pullback_max_bars)
                + int(params.stage1_fetch_post_bars),
                len(timestamps) - 1,
            )
            raw_windows.append(
                (
                    max(int(timestamps[0]), int(candidate["pump_start_timestamp"]) - entry_preroll_ms),
                    int(timestamps[end_idx]) + levels_timeframe_ms - 1,
                )
            )
        return self._merge_timestamp_windows(raw_windows)

    @staticmethod
    def _merge_timestamp_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if not windows:
            return []
        merged: list[tuple[int, int]] = []
        for start_timestamp_ms, end_timestamp_ms in sorted(windows):
            if not merged or start_timestamp_ms > (merged[-1][1] + 1):
                merged.append((int(start_timestamp_ms), int(end_timestamp_ms)))
                continue
            merged[-1] = (merged[-1][0], max(merged[-1][1], int(end_timestamp_ms)))
        return merged

    def _resolve_htf_stage1_candidate_from_arrays(
        self,
        *,
        timestamps: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        five_idx: int,
        params: PnoParams,
    ) -> dict[str, int | float] | None:
        if five_idx <= 0 or five_idx >= len(timestamps):
            return None

        best_candidate: dict[str, int | float] | None = None
        for active_high_5m_idx in range(max(0, five_idx - params.stage1_pullback_max_bars), five_idx):
            pump_start_min_idx = max(0, active_high_5m_idx - params.stage1_pump_max_bars + 1)
            pump_start_max_idx = active_high_5m_idx - params.stage1_pump_min_bars + 1
            if pump_start_max_idx < pump_start_min_idx:
                continue
            for pump_start_5m_idx in range(pump_start_max_idx, pump_start_min_idx - 1, -1):
                pump_bars = active_high_5m_idx - pump_start_5m_idx + 1
                pullback_bars = five_idx - active_high_5m_idx
                if pullback_bars < params.stage1_pullback_min_bars or pullback_bars > params.stage1_pullback_max_bars:
                    continue
                segment_highs = highs[pump_start_5m_idx : five_idx + 1]
                if segment_highs.size == 0:
                    continue
                relative_peak_idx = int(np.argmax(segment_highs))
                if (pump_start_5m_idx + relative_peak_idx) != active_high_5m_idx:
                    continue
                pump_low = float(np.nanmin(lows[pump_start_5m_idx : active_high_5m_idx + 1]))
                active_high = float(highs[active_high_5m_idx])
                pump_height = active_high - pump_low
                if pump_height <= self._EPSILON:
                    continue
                pullback_low = float(np.nanmin(lows[active_high_5m_idx + 1 : five_idx + 1]))
                min_allowed_low = pump_low + (float(params.stage1_pullback_low_min_pump_fraction) * pump_height)
                if pullback_low < (min_allowed_low - self._EPSILON):
                    continue
                pullback_height = active_high - pullback_low
                if pullback_height <= self._EPSILON:
                    continue
                level_ceiling = pullback_low + (float(params.stage1_level_max_pullback_reclaim_fraction) * pullback_height)
                level = float(highs[five_idx])
                if level > (level_ceiling + self._EPSILON):
                    continue
                pullback_low_5m_idx = int(active_high_5m_idx + 1 + np.argmin(lows[active_high_5m_idx + 1 : five_idx + 1]))
                candidate = {
                    "pump_start_5m_idx": int(pump_start_5m_idx),
                    "pump_start_timestamp": int(timestamps[pump_start_5m_idx]),
                    "active_high_5m_idx": int(active_high_5m_idx),
                    "active_high_timestamp": int(timestamps[active_high_5m_idx]),
                    "active_high": active_high,
                    "pullback_start_5m_idx": int(active_high_5m_idx + 1),
                    "pullback_end_5m_idx": int(five_idx),
                    "pullback_low_5m_idx": int(pullback_low_5m_idx),
                    "pullback_low": pullback_low,
                    "level": level,
                    "level_timestamp": int(timestamps[five_idx]),
                    "pump_low": pump_low,
                    "pump_height": pump_height,
                    "min_allowed_low": min_allowed_low,
                    "pump_bars": int(pump_bars),
                    "pullback_bars": int(pullback_bars),
                }
                if best_candidate is None:
                    best_candidate = candidate
                    continue
                best_active_high = float(best_candidate["active_high"])
                best_pullback_low = float(best_candidate["pullback_low"])
                if active_high > (best_active_high + self._EPSILON) or (
                    abs(active_high - best_active_high) <= self._EPSILON and pullback_low > best_pullback_low
                ):
                    best_candidate = candidate
        return best_candidate

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator <= 0.0:
            return 0.0
        return numerator / denominator
