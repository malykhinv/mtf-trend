"""Strategy wrapper for PNO."""

from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import io
import os
import tempfile
from typing import Any, ClassVar
import zipfile

import pandas as pd
import requests

from constants import DEFAULT_CACHE_DIR
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.pno.config import (
    PnoCategoryProfile,
    PnoParams,
    build_pno_grid,
    describe_pno_category_profile_set,
    resolve_pno_category_profiles,
    validate_pno_params,
    with_pno_risk,
)
from strategy.pno.engine import PnoEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames
from pathlib import Path
from vectorbt_runner.data_preparer import DataPreparer


@dataclass
class _PnoSecondsFrameProvider:
    cache_dir: Path
    _shared_window_cache: ClassVar[dict[tuple[str, int, int], pd.DataFrame]] = {}
    _shared_day_cache: ClassVar[dict[tuple[str, str], pd.DataFrame]] = {}

    def __post_init__(self) -> None:
        self._runtime_cache_dir = Path(self.cache_dir)
        self._persistent_cache_dir = self._resolve_persistent_cache_dir(self._runtime_cache_dir)
        self._runtime_preparer = DataPreparer(self._runtime_cache_dir)
        self._persistent_preparer = DataPreparer(self._persistent_cache_dir)
        self._storage = ParquetStorage(self._persistent_cache_dir)
        self._client: CcxtFuturesClient | None = None
        self._window_cache: dict[tuple[str, int, int], pd.DataFrame] = {}
        self._day_cache: dict[tuple[str, str], pd.DataFrame] = {}

    @staticmethod
    def _resolve_persistent_cache_dir(cache_dir: Path) -> Path:
        resolved_cache_dir = cache_dir.expanduser().resolve()
        temp_root = Path(tempfile.gettempdir()).expanduser().resolve()
        if resolved_cache_dir == temp_root or temp_root in resolved_cache_dir.parents:
            return Path(os.getenv("CACHE_DIR", DEFAULT_CACHE_DIR)).expanduser().resolve()
        return resolved_cache_dir

    def load_aggregated_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        target_timeframe: Timeframe,
    ) -> pd.DataFrame:
        seconds_frame = self._ensure_seconds_window(
            symbol=symbol,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )
        if seconds_frame.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return PnoEngine._aggregate_frame(
            seconds_frame,
            target_timeframe_ms=target_timeframe.to_milliseconds(),
        )

    def _ensure_seconds_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        cache_key = (symbol, int(start_timestamp_ms), int(end_timestamp_ms))
        cached = self._window_cache.get(cache_key)
        if cached is not None:
            return cached
        shared_cached = self._shared_window_cache.get(cache_key)
        if shared_cached is not None:
            local_copy = shared_cached.copy()
            self._window_cache[cache_key] = local_copy
            return local_copy

        seconds_frame = self._runtime_preparer.load_symbol_data_range(
            symbol,
            Timeframe.S1,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )
        if self._persistent_cache_dir != self._runtime_cache_dir:
            persistent_seconds_frame = self._persistent_preparer.load_symbol_data_range(
                symbol,
                Timeframe.S1,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
            if not persistent_seconds_frame.empty:
                seconds_frame = (
                    pd.concat([seconds_frame, persistent_seconds_frame], ignore_index=True)
                    .drop_duplicates(subset=["timestamp"], keep="last")
                    .sort_values("timestamp")
                    .reset_index(drop=True)
                )
        if self._frame_covers_window(
            seconds_frame,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        ):
            self._window_cache[cache_key] = seconds_frame.copy()
            self._shared_window_cache[cache_key] = seconds_frame.copy()
            return seconds_frame
        fetched_parts: list[pd.DataFrame] = []
        newly_fetched_day_parts: list[pd.DataFrame] = []
        for utc_day in self._iter_utc_days(
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        ):
            day_key = (symbol, utc_day.isoformat())
            day_frame = self._day_cache.get(day_key)
            if day_frame is None:
                day_frame = self._shared_day_cache.get(day_key)
            if day_frame is None:
                day_frame = self._fetch_seconds_for_day(symbol=symbol, utc_day=utc_day)
                self._day_cache[day_key] = day_frame
                self._shared_day_cache[day_key] = day_frame.copy()
                if not day_frame.empty:
                    newly_fetched_day_parts.append(day_frame.copy())
            else:
                self._day_cache[day_key] = day_frame.copy()
            if day_frame.empty:
                continue
            day_slice = day_frame.loc[
                (day_frame["timestamp"] >= int(start_timestamp_ms))
                & (day_frame["timestamp"] <= int(end_timestamp_ms))
            ].copy()
            if not day_slice.empty:
                fetched_parts.append(day_slice)

        if fetched_parts:
            fetched = (
                pd.concat(fetched_parts, ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            seconds_frame = (
                pd.concat([seconds_frame, fetched], ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        if newly_fetched_day_parts:
            newly_fetched = (
                pd.concat(newly_fetched_day_parts, ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            self._storage.save_incremental(symbol, Timeframe.S1, newly_fetched)
        self._window_cache[cache_key] = seconds_frame.copy()
        self._shared_window_cache[cache_key] = seconds_frame.copy()
        return seconds_frame

    @staticmethod
    def _frame_covers_window(
        frame: pd.DataFrame,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> bool:
        if frame.empty:
            return False
        clipped = frame.loc[
            (frame["timestamp"] >= int(start_timestamp_ms))
            & (frame["timestamp"] <= int(end_timestamp_ms)),
            ["timestamp"],
        ].drop_duplicates()
        expected_points = ((int(end_timestamp_ms) - int(start_timestamp_ms)) // 1_000) + 1
        return len(clipped) >= expected_points

    @staticmethod
    def _iter_utc_days(
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> tuple[date, ...]:
        start_day = datetime.fromtimestamp(int(start_timestamp_ms) / 1000, tz=timezone.utc).date()
        end_day = datetime.fromtimestamp(int(end_timestamp_ms) / 1000, tz=timezone.utc).date()
        days: list[date] = []
        cursor = start_day
        while cursor <= end_day:
            days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)

    def _fetch_seconds_for_day(
        self,
        *,
        symbol: str,
        utc_day: date,
    ) -> pd.DataFrame:
        archive_frame = self._fetch_seconds_from_archive(symbol=symbol, utc_day=utc_day)
        if not archive_frame.empty:
            return archive_frame
        day_start_ms = int(datetime.combine(utc_day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        day_end_ms = day_start_ms + (24 * 60 * 60 * 1000) - 1
        return self._fetch_seconds_from_live_trades(
            symbol=symbol,
            start_timestamp_ms=day_start_ms,
            end_timestamp_ms=day_end_ms,
        )

    def _fetch_seconds_from_archive(
        self,
        *,
        symbol: str,
        utc_day: date,
    ) -> pd.DataFrame:
        market_id = self._resolve_market_id(symbol)
        url = (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/"
            f"{market_id}/{market_id}-aggTrades-{utc_day.isoformat()}.zip"
        )
        response = requests.get(url, timeout=60)
        if response.status_code == 404:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            if not names:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            raw = archive.read(names[0])
        trades = pd.read_csv(io.BytesIO(raw))
        if trades.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return self._aggregate_agg_trades_to_seconds(trades)

    def _fetch_seconds_from_live_trades(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if self._client is None:
            self._client = CcxtFuturesClient(exchange=Exchange.BINANCE)
        self._client._ensure_markets_loaded()
        market_id = self._client._client.market_id(symbol)
        all_rows: list[dict[str, object]] = []
        since = int(start_timestamp_ms)
        while since <= int(end_timestamp_ms):
            batch = self._client._retry_exchange_call(
                operation="binance_fetch_agg_trades",
                symbol=symbol,
                endpoint="fapiPublicGetAggTrades",
                call=self._client._client.fapiPublicGetAggTrades,
                params={
                    "symbol": market_id,
                    "startTime": since,
                    "endTime": int(end_timestamp_ms),
                    "limit": 1000,
                },
            )
            if not isinstance(batch, list) or not batch:
                break
            all_rows.extend(batch)
            last_ts = int(batch[-1].get("T") or 0)
            if last_ts >= int(end_timestamp_ms):
                break
            since = max(last_ts + 1, since + 1)
        if not all_rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        trades = pd.DataFrame(all_rows)
        return self._aggregate_agg_trades_to_seconds(trades)

    def _resolve_market_id(self, symbol: str) -> str:
        if self._client is None:
            self._client = CcxtFuturesClient(exchange=Exchange.BINANCE)
        self._client._ensure_markets_loaded()
        return str(self._client._client.market_id(symbol))

    @staticmethod
    def _aggregate_agg_trades_to_seconds(trades: pd.DataFrame) -> pd.DataFrame:
        if trades.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        work = trades.copy()
        timestamp_column = "transact_time" if "transact_time" in work.columns else "T"
        quantity_column = "quantity" if "quantity" in work.columns else "q"
        work["price"] = pd.to_numeric(work["price"] if "price" in work.columns else work["p"], errors="coerce")
        work["quantity"] = pd.to_numeric(work[quantity_column], errors="coerce")
        work["timestamp"] = ((pd.to_numeric(work[timestamp_column], errors="coerce") // 1000) * 1000).astype("Int64")
        work = work.loc[work["timestamp"].notna() & work["price"].notna() & work["quantity"].notna()].copy()
        if work.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        work["timestamp"] = work["timestamp"].astype("int64")
        aggregated = (
            work.groupby("timestamp", sort=True)
            .agg(
                open=("price", "first"),
                high=("price", "max"),
                low=("price", "min"),
                close=("price", "last"),
                volume=("quantity", "sum"),
            )
            .reset_index()
        )
        return aggregated.astype(
            {
                "timestamp": "int64",
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "float64",
            }
        )


class PnoStrategy(BaseStrategy[PnoParams]):
    def __init__(
        self,
        *,
        deposit: float,
        risk_pct: float,
        entry_confirmation_mode_filter: str | None = None,
        category_mode_filter: str = "all",
        cache_dir: str | Path | None = None,
    ) -> None:
        self._deposit = deposit
        self._risk_pct = risk_pct
        self._entry_confirmation_mode_filter = entry_confirmation_mode_filter
        self._category_mode_filter = category_mode_filter
        self._cache_dir = Path(cache_dir) if cache_dir is not None else Path("./.output/cache")
        self._engine = PnoEngine(cache_dir=self._cache_dir)
        self._seconds_provider = _PnoSecondsFrameProvider(self._cache_dir)
        self._last_generation_diagnostics: dict[str, object] = {}

    def validate_config(self, params: PnoParams) -> None:
        validate_pno_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return self._engine.prepare_data(data)

    def generate_events(self, data: pd.DataFrame, params: PnoParams) -> list[TradeResult]:
        prepared = self._engine.prepare_data(data)
        return self._generate_events_for_profiles(
            profiles=resolve_pno_category_profiles(params, category_mode=self._category_mode_filter),
            runner=lambda profile_params: self._engine.generate_events_single_frame(frame=prepared, params=profile_params),
        )

    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: PnoParams,
        **context: object,
    ) -> list[TradeResult]:
        engine_context = {"seconds_frame_provider": self._seconds_provider}
        engine_context.update(context)
        return self._generate_events_for_profiles(
            profiles=resolve_pno_category_profiles(params, category_mode=self._category_mode_filter),
            runner=lambda profile_params: self._engine.generate_events_multi_tf(
                levels_frame=mtf_frames.levels_frame,
                entry_frame=mtf_frames.entry_frame,
                params=profile_params,
                **engine_context,
            ),
        )

    def prepare_symbol_context(
        self,
        *,
        symbol: str,
        mtf_frames: SymbolMtfFrames,
        params: PnoParams,
    ) -> dict[str, Any] | None:
        del symbol, mtf_frames, params
        return {"seconds_frame_provider": self._seconds_provider}

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
            "pno_category_mode": self._category_mode_filter,
            "pno_category_profile_set": describe_pno_category_profile_set(
                params,
                category_mode=self._category_mode_filter,
            ),
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
            "pno_pullback_valid_min_leg_fraction": params.pullback_valid_min_leg_fraction,
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
            "pno_stage3_min_post_high_5m_volume_support_fraction": params.stage3_min_post_high_5m_volume_support_fraction,
            "pno_stage3_fast_reclaim_min_post_high_5m_volume_support_fraction": (
                params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction
            ),
            "pno_stage3_fast_reclaim_max_pullback_age_bars": params.stage3_fast_reclaim_max_pullback_age_bars,
            "pno_ideal_like_impulse_enabled": params.ideal_like_impulse_enabled,
            "pno_ideal_like_min_impulse_atr_pre": params.ideal_like_min_impulse_atr_pre,
            "pno_ideal_like_min_peak_bar_tr_atr_pre": params.ideal_like_min_peak_bar_tr_atr_pre,
            "pno_ideal_like_min_volume_ratio_start": params.ideal_like_min_volume_ratio_start,
            "pno_ideal_like_min_path_efficiency": params.ideal_like_min_path_efficiency,
            "pno_ideal_like_max_wick_share": params.ideal_like_max_wick_share,
            "pno_ideal_like_min_body_share_mean": params.ideal_like_min_body_share_mean,
            "pno_ideal_like_min_body_wick_edge": params.ideal_like_min_body_wick_edge,
            "pno_ideal_like_max_micro_flat_bar_share": params.ideal_like_max_micro_flat_bar_share,
            "pno_ideal_like_max_active_high_upper_wick_share": params.ideal_like_max_active_high_upper_wick_share,
            "pno_ideal_like_max_counterflow_ratio_5m": params.ideal_like_max_counterflow_ratio_5m,
            "pno_ideal_like_relaxed_level_maturity_fraction": params.ideal_like_relaxed_level_maturity_fraction,
            "pno_ideal_like_level_latest_high_max_age_bars": params.ideal_like_level_latest_high_max_age_bars,
            "pno_ideal_like_ignore_decay_invalidation": params.ideal_like_ignore_decay_invalidation,
            "pno_level_cluster_spread_v1": params.level_cluster_spread_v1,
            "pno_level_cluster_relaxed_spread_v1": params.level_cluster_relaxed_spread_v1,
            "pno_level_latest_high_max_age_bars": params.level_latest_high_max_age_bars,
            "pno_level_max_age_bars_upper_tf": params.level_max_age_bars_upper_tf,
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
            "pno_close_above_min_entry_pos": params.close_above_min_entry_pos,
            "pno_close_above_max_pullback_fraction_of_leg": params.close_above_max_pullback_fraction_of_leg,
            "pno_close_above_max_post_high_wick_share": params.close_above_max_post_high_wick_share,
            "pno_close_above_max_active_high_upper_wick_share": params.close_above_max_active_high_upper_wick_share,
            "pno_close_above_min_active_high_close_position": params.close_above_min_active_high_close_position,
            "pno_close_above_min_post_high_alternation_rate": params.close_above_min_post_high_alternation_rate,
            "pno_close_above_min_signal_volume_vs_recent": params.close_above_min_signal_volume_vs_recent,
            "pno_close_above_min_signal_ema9_slope_3": params.close_above_min_signal_ema9_slope_3,
            "pno_close_above_min_signal_ema20_slope_3": params.close_above_min_signal_ema20_slope_3,
            "pno_close_above_min_signal_ema_spread_pct": params.close_above_min_signal_ema_spread_pct,
            "pno_close_above_min_signal_close_position_in_chop": params.close_above_min_signal_close_position_in_chop,
            "pno_close_above_choppy_overlap_threshold": params.close_above_choppy_overlap_threshold,
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
        self._last_generation_diagnostics = {}
        return diagnostics

    def _generate_events_for_profiles(
        self,
        *,
        profiles: tuple[PnoCategoryProfile, ...],
        runner: Any,
    ) -> list[TradeResult]:
        combined_diagnostics = self._empty_generation_diagnostics()
        seen_trade_keys: set[tuple[object, ...]] = set()
        trades: list[TradeResult] = []
        profile_contexts: dict[str, object] = {}
        self._engine.begin_runtime_batch()
        try:
            for profile in profiles:
                profile_trades = runner(profile.params)
                profile_diagnostics = self._engine.consume_last_generation_diagnostics()
                tagged_diagnostics = self._tag_profile_diagnostics(profile_diagnostics, profile=profile)
                self._merge_generation_diagnostics(combined_diagnostics, tagged_diagnostics)
                profile_contexts[profile.category_id] = dict(tagged_diagnostics.get("context", {}))

                for trade in profile_trades:
                    tagged_trade = self._tag_trade_result(trade, profile=profile)
                    trade_key = self._trade_dedup_key(tagged_trade, symbol=profile.params.symbol)
                    if trade_key in seen_trade_keys:
                        continue
                    seen_trade_keys.add(trade_key)
                    trades.append(tagged_trade)
        finally:
            self._engine.end_runtime_batch()

        trades.sort(key=lambda trade: (trade.entry_timestamp_ms, trade.exit_timestamp_ms))
        combined_diagnostics["trades_generated"] = len(trades)
        context = combined_diagnostics.setdefault("context", {})
        if isinstance(context, dict):
            context["category_mode"] = "multi_profile" if len(profiles) > 1 else "single_profile"
            context["category_profiles_run"] = [profile.category_id for profile in profiles]
            context["category_profile_labels"] = {
                profile.category_id: profile.label for profile in profiles
            }
            context["category_profile_priorities"] = {
                profile.category_id: profile.priority for profile in profiles
            }
            context["category_profile_contexts"] = profile_contexts
        self._last_generation_diagnostics = combined_diagnostics
        return trades

    @staticmethod
    def _empty_generation_diagnostics() -> dict[str, object]:
        return {
            "trades_generated": 0,
            "blocked_cycles": 0,
            "skipped_insufficient_data": 0,
            "trade_count_proxy_used": True,
            "stage_hits": {},
            "stage_events": [],
            "stage_rejections": [],
            "context": {},
        }

    @staticmethod
    def _trade_dedup_key(trade: TradeResult, *, symbol: str) -> tuple[object, ...]:
        metadata = trade.metadata or {}
        actual_entry = metadata.get("entry_price_actual")
        entry_level = actual_entry if isinstance(actual_entry, (int, float)) else trade.entry_price.value
        trade_symbol = str(metadata.get("symbol") or symbol)
        return (
            trade_symbol,
            int(trade.entry_timestamp_ms),
            round(float(entry_level), 10),
            metadata.get("level"),
        )

    @staticmethod
    def _tag_trade_result(trade: TradeResult, *, profile: PnoCategoryProfile) -> TradeResult:
        metadata = dict(trade.metadata or {})
        metadata["pno_category_id"] = profile.category_id
        metadata["pno_category_label"] = profile.label
        metadata["pno_category_priority"] = profile.priority
        metadata["pno_profile_variant_id"] = profile.params.pno_variant_id
        return replace(trade, metadata=metadata)

    @staticmethod
    def _tag_profile_diagnostics(
        diagnostics: dict[str, object],
        *,
        profile: PnoCategoryProfile,
    ) -> dict[str, object]:
        tagged = dict(diagnostics)
        stage_events = tagged.get("stage_events")
        if isinstance(stage_events, list):
            tagged["stage_events"] = [
                {
                    **dict(event),
                    "pno_category_id": profile.category_id,
                    "pno_category_label": profile.label,
                    "pno_category_priority": profile.priority,
                }
                for event in stage_events
            ]
        stage_rejections = tagged.get("stage_rejections")
        if isinstance(stage_rejections, list):
            tagged["stage_rejections"] = [
                {
                    **dict(event),
                    "pno_category_id": profile.category_id,
                    "pno_category_label": profile.label,
                    "pno_category_priority": profile.priority,
                }
                for event in stage_rejections
            ]
        context = tagged.get("context")
        if isinstance(context, dict):
            tagged["context"] = {
                **context,
                "pno_category_id": profile.category_id,
                "pno_category_label": profile.label,
                "pno_category_priority": profile.priority,
                "pno_profile_variant_id": profile.params.pno_variant_id,
            }
        return tagged

    @staticmethod
    def _merge_generation_diagnostics(
        combined: dict[str, object],
        incoming: dict[str, object],
    ) -> None:
        for key in ("blocked_cycles", "skipped_insufficient_data"):
            combined[key] = int(combined.get(key, 0)) + int(incoming.get(key, 0))

        combined["trade_count_proxy_used"] = bool(combined.get("trade_count_proxy_used", True)) and bool(
            incoming.get("trade_count_proxy_used", True)
        )

        combined_stage_hits = combined.setdefault("stage_hits", {})
        incoming_stage_hits = incoming.get("stage_hits")
        if isinstance(combined_stage_hits, dict) and isinstance(incoming_stage_hits, dict):
            for stage_id, hit_count in incoming_stage_hits.items():
                combined_stage_hits[str(stage_id)] = int(combined_stage_hits.get(str(stage_id), 0)) + int(hit_count)

        combined_stage_events = combined.setdefault("stage_events", [])
        incoming_stage_events = incoming.get("stage_events")
        if isinstance(combined_stage_events, list) and isinstance(incoming_stage_events, list):
            combined_stage_events.extend(dict(event) for event in incoming_stage_events)

        combined_stage_rejections = combined.setdefault("stage_rejections", [])
        incoming_stage_rejections = incoming.get("stage_rejections")
        if isinstance(combined_stage_rejections, list) and isinstance(incoming_stage_rejections, list):
            combined_stage_rejections.extend(dict(event) for event in incoming_stage_rejections)

        combined_context = combined.setdefault("context", {})
        incoming_context = incoming.get("context")
        if isinstance(combined_context, dict) and isinstance(incoming_context, dict):
            stage_order = incoming_context.get("stage_order")
            if stage_order is not None:
                combined_context.setdefault("stage_order", list(stage_order))
            for passthrough_key in (
                "trade_count_proxy",
                "quote_volume_proxy",
                "symbol",
            ):
                if passthrough_key in incoming_context:
                    combined_context.setdefault(passthrough_key, incoming_context[passthrough_key])
