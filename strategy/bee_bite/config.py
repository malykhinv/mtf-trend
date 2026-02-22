"""Конфиг и параметры стратегии bee_bite."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Literal

from domain.enums.entry_trigger import EntryTrigger
from domain.enums.timeframe import Timeframe

BeeBiteProfileId = Literal["A", "B", "C"]
BeeBiteGridMode = Literal["baseline", "expanded", "research"]
BeeBiteReclaimMode = Literal["strict", "balanced", "aggressive"]
BeeBiteRetestMode = Literal["confirmation", "immediate"]

BEE_BITE_PROFILE_IDS: tuple[BeeBiteProfileId, ...] = ("A", "B", "C")
BEE_BITE_GRID_MODES: tuple[BeeBiteGridMode, ...] = ("baseline", "expanded", "research")
BEE_BITE_RECLAIM_MODES: tuple[BeeBiteReclaimMode, ...] = ("strict", "balanced", "aggressive")
BEE_BITE_RETEST_MODES: tuple[BeeBiteRetestMode, ...] = ("confirmation", "immediate")


@dataclass(frozen=True, slots=True)
class ScoreThreshold:
    min_score: float


@dataclass(frozen=True, slots=True)
class BeeBiteReclaimSettings:
    micro_offset_multiplier: float
    reclaim_limit_delta: int
    retest_limit_bars: int
    emergency_reset_ratio: float


BEE_BITE_PROFILE_SCORE_THRESHOLDS: dict[BeeBiteProfileId, ScoreThreshold] = {
    "A": ScoreThreshold(min_score=4.0),
    "B": ScoreThreshold(min_score=3.0),
    "C": ScoreThreshold(min_score=2.0),
}

BEE_BITE_PROFILE_TOP_N: dict[BeeBiteProfileId, int] = {
    "A": 5,
    "B": 10,
    "C": 20,
}


BEE_BITE_RECLAIM_SETTINGS: dict[BeeBiteReclaimMode, BeeBiteReclaimSettings] = {
    "strict": BeeBiteReclaimSettings(
        micro_offset_multiplier=1.0,
        reclaim_limit_delta=0,
        retest_limit_bars=6,
        emergency_reset_ratio=0.70,
    ),
    "balanced": BeeBiteReclaimSettings(
        micro_offset_multiplier=0.75,
        reclaim_limit_delta=1,
        retest_limit_bars=0,
        emergency_reset_ratio=0.65,
    ),
    "aggressive": BeeBiteReclaimSettings(
        micro_offset_multiplier=0.50,
        reclaim_limit_delta=2,
        retest_limit_bars=0,
        emergency_reset_ratio=0.60,
    ),
}


@dataclass(frozen=True, slots=True)
class BeeBiteParams:
    # section-11 профильные параметры A/B/C (без breakout-ретест полей)
    bite_lookback: int
    bite_volume_mult: float
    bite_retest_window_hours: int
    bite_min_rr: float
    bite_tp2_mult: float
    bite_min_move_atr: float
    bite_max_retest_depth: float
    bite_confirmation_bars: int
    bite_entry_trigger: EntryTrigger
    bite_min_depth_threshold: float
    bite_micro_offset: float
    bite_reclaim_limit_bars: int
    bite_max_age_range_hours: int
    bite_cooldown_hours: int
    bite_reclaim_mode: BeeBiteReclaimMode
    bite_retest_mode: BeeBiteRetestMode
    symbol: str
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
    bite_r_trade: float = 1.0
    bite_portfolio_risk_limit: float = 3.0
    bite_min_stop_atr_ratio: float = 0.3
    bite_t_max_in_trade: int | None = None
    bite_tp1_share: float = 0.5
    bite_tp1_stop_buffer_pct: float = 0.001
    bite_profile_id: BeeBiteProfileId = "A"
    bite_grid_mode: BeeBiteGridMode = "baseline"


@dataclass(frozen=True, slots=True)
class BeeBiteProfileRuntime:
    reclaim_mode: BeeBiteReclaimMode
    retest_mode: BeeBiteRetestMode
    top_n_min: int
    top_n_max: int
    cooldown_hours: int
    min_depth_threshold: float
    micro_offset: float
    reclaim_limit_bars: int
    max_age_range_hours: int


BEE_BITE_PROFILE_RUNTIME: dict[BeeBiteProfileId, BeeBiteProfileRuntime] = {
    "A": BeeBiteProfileRuntime(
        reclaim_mode="strict",
        retest_mode="confirmation",
        top_n_min=5,
        top_n_max=50,
        cooldown_hours=8,
        min_depth_threshold=0.15,
        micro_offset=0.15,
        reclaim_limit_bars=5,
        max_age_range_hours=12,
    ),
    "B": BeeBiteProfileRuntime(
        reclaim_mode="balanced",
        retest_mode="confirmation",
        top_n_min=10,
        top_n_max=80,
        cooldown_hours=6,
        min_depth_threshold=0.12,
        micro_offset=0.10,
        reclaim_limit_bars=6,
        max_age_range_hours=8,
    ),
    "C": BeeBiteProfileRuntime(
        reclaim_mode="aggressive",
        retest_mode="immediate",
        top_n_min=20,
        top_n_max=120,
        cooldown_hours=4,
        min_depth_threshold=0.10,
        micro_offset=0.05,
        reclaim_limit_bars=8,
        max_age_range_hours=6,
    ),
}


BEE_BITE_PROFILE_BASELINES: dict[BeeBiteProfileId, BeeBiteParams] = {
    "A": BeeBiteParams(
        bite_lookback=13,
        bite_volume_mult=1.5,
        bite_retest_window_hours=24,
        bite_min_rr=3.0,
        bite_tp2_mult=2.0,
        bite_min_move_atr=0.5,
        bite_max_retest_depth=0.20,
        bite_confirmation_bars=2,
        bite_entry_trigger=EntryTrigger.PRICE_CONFIRMATION,
        bite_min_depth_threshold=BEE_BITE_PROFILE_RUNTIME["A"].min_depth_threshold,
        bite_micro_offset=BEE_BITE_PROFILE_RUNTIME["A"].micro_offset,
        bite_reclaim_limit_bars=BEE_BITE_PROFILE_RUNTIME["A"].reclaim_limit_bars,
        bite_max_age_range_hours=BEE_BITE_PROFILE_RUNTIME["A"].max_age_range_hours,
        bite_cooldown_hours=BEE_BITE_PROFILE_RUNTIME["A"].cooldown_hours,
        bite_reclaim_mode=BEE_BITE_PROFILE_RUNTIME["A"].reclaim_mode,
        bite_retest_mode=BEE_BITE_PROFILE_RUNTIME["A"].retest_mode,
        symbol="",
        bite_profile_id="A",
        bite_grid_mode="baseline",
    ),
    "B": BeeBiteParams(
        bite_lookback=21,
        bite_volume_mult=2.0,
        bite_retest_window_hours=36,
        bite_min_rr=3.5,
        bite_tp2_mult=2.5,
        bite_min_move_atr=0.5,
        bite_max_retest_depth=0.25,
        bite_confirmation_bars=2,
        bite_entry_trigger=EntryTrigger.PRICE_CONFIRMATION,
        bite_min_depth_threshold=BEE_BITE_PROFILE_RUNTIME["B"].min_depth_threshold,
        bite_micro_offset=BEE_BITE_PROFILE_RUNTIME["B"].micro_offset,
        bite_reclaim_limit_bars=BEE_BITE_PROFILE_RUNTIME["B"].reclaim_limit_bars,
        bite_max_age_range_hours=BEE_BITE_PROFILE_RUNTIME["B"].max_age_range_hours,
        bite_cooldown_hours=BEE_BITE_PROFILE_RUNTIME["B"].cooldown_hours,
        bite_reclaim_mode=BEE_BITE_PROFILE_RUNTIME["B"].reclaim_mode,
        bite_retest_mode=BEE_BITE_PROFILE_RUNTIME["B"].retest_mode,
        symbol="",
        bite_profile_id="B",
        bite_grid_mode="baseline",
    ),
    "C": BeeBiteParams(
        bite_lookback=8,
        bite_volume_mult=1.2,
        bite_retest_window_hours=12,
        bite_min_rr=2.5,
        bite_tp2_mult=1.5,
        bite_min_move_atr=0.5,
        bite_max_retest_depth=0.30,
        bite_confirmation_bars=1,
        bite_entry_trigger=EntryTrigger.IMMEDIATE,
        bite_min_depth_threshold=BEE_BITE_PROFILE_RUNTIME["C"].min_depth_threshold,
        bite_micro_offset=BEE_BITE_PROFILE_RUNTIME["C"].micro_offset,
        bite_reclaim_limit_bars=BEE_BITE_PROFILE_RUNTIME["C"].reclaim_limit_bars,
        bite_max_age_range_hours=BEE_BITE_PROFILE_RUNTIME["C"].max_age_range_hours,
        bite_cooldown_hours=BEE_BITE_PROFILE_RUNTIME["C"].cooldown_hours,
        bite_reclaim_mode=BEE_BITE_PROFILE_RUNTIME["C"].reclaim_mode,
        bite_retest_mode=BEE_BITE_PROFILE_RUNTIME["C"].retest_mode,
        symbol="",
        bite_profile_id="C",
        bite_grid_mode="baseline",
    ),
}

BEE_BITE_EXTENDED_GRID_OFFSETS: dict[str, tuple[int | float | EntryTrigger, ...]] = {
    "bite_lookback": (-5, 0, 5),
    "bite_volume_mult": (-0.3, 0.0, 0.3),
    "bite_retest_window_hours": (-12, 0, 12),
    "bite_min_rr": (-0.5, 0.0, 0.5),
    "bite_tp2_mult": (-0.5, 0.0, 0.5),
    "bite_confirmation_bars": (-1, 0, 1),
    "bite_entry_trigger": (EntryTrigger.IMMEDIATE, EntryTrigger.PRICE_CONFIRMATION),
}

BEE_BITE_RESEARCH_GRID_OFFSETS: dict[str, tuple[int | float | EntryTrigger, ...]] = {
    "bite_lookback": (-8, -4, 0, 4, 8),
    "bite_volume_mult": (-0.5, -0.25, 0.0, 0.25, 0.5),
    "bite_retest_window_hours": (-18, -9, 0, 9, 18),
    "bite_min_rr": (-0.75, -0.35, 0.0, 0.35, 0.75),
    "bite_tp2_mult": (-0.75, -0.35, 0.0, 0.35, 0.75),
    "bite_min_move_atr": (-0.2, -0.1, 0.0, 0.1, 0.2),
    "bite_max_retest_depth": (-0.10, -0.05, 0.0, 0.05, 0.10),
    "bite_confirmation_bars": (-1, 0, 1),
    "bite_entry_trigger": (EntryTrigger.IMMEDIATE, EntryTrigger.PRICE_CONFIRMATION),
}


def parse_bee_bite_profile_id(raw_value: str | None, *, default: BeeBiteProfileId = "A") -> BeeBiteProfileId:
    profile = (raw_value or default).strip().upper()
    if profile not in BEE_BITE_PROFILE_IDS:
        supported = ", ".join(BEE_BITE_PROFILE_IDS)
        raise ValueError(f"Invalid BEE_BITE_PROFILE: {profile}. Supported values: {supported}")
    return profile  # type: ignore[return-value]


def parse_bee_bite_grid_mode(raw_value: str | None, *, default: BeeBiteGridMode = "baseline") -> BeeBiteGridMode:
    grid_mode = (raw_value or default).strip().lower()
    if grid_mode not in BEE_BITE_GRID_MODES:
        supported = ", ".join(BEE_BITE_GRID_MODES)
        raise ValueError(f"Invalid BEE_BITE_GRID_MODE: {grid_mode}. Supported values: {supported}")
    return grid_mode  # type: ignore[return-value]


def parse_bee_bite_reclaim_mode(
    raw_value: str | None,
    *,
    default: BeeBiteReclaimMode,
) -> BeeBiteReclaimMode:
    reclaim_mode = (raw_value or default).strip().lower()
    if reclaim_mode not in BEE_BITE_RECLAIM_MODES:
        supported = ", ".join(BEE_BITE_RECLAIM_MODES)
        raise ValueError(f"Invalid BEE_BITE_RECLAIM_MODE: {reclaim_mode}. Supported values: {supported}")
    return reclaim_mode  # type: ignore[return-value]


def parse_bee_bite_retest_mode(
    raw_value: str | None,
    *,
    default: BeeBiteRetestMode,
) -> BeeBiteRetestMode:
    retest_mode = (raw_value or default).strip().lower()
    if retest_mode not in BEE_BITE_RETEST_MODES:
        supported = ", ".join(BEE_BITE_RETEST_MODES)
        raise ValueError(f"Invalid BEE_BITE_RETEST_MODE: {retest_mode}. Supported values: {supported}")
    return retest_mode  # type: ignore[return-value]


def build_bee_bite_grid(
    *,
    profile_id: BeeBiteProfileId,
    grid_mode: BeeBiteGridMode,
    reclaim_mode: BeeBiteReclaimMode,
    retest_mode: BeeBiteRetestMode,
    cooldown_hours: int,
    max_age_range_hours: int,
) -> list[BeeBiteParams]:
    baseline = _apply_runtime_modes(
        params=BEE_BITE_PROFILE_BASELINES[profile_id],
        reclaim_mode=reclaim_mode,
        retest_mode=retest_mode,
        cooldown_hours=cooldown_hours,
        max_age_range_hours=max_age_range_hours,
    )
    if grid_mode == "baseline":
        return [baseline]

    offsets_map = BEE_BITE_EXTENDED_GRID_OFFSETS if grid_mode == "expanded" else BEE_BITE_RESEARCH_GRID_OFFSETS

    lookbacks = _numeric_candidates(baseline.bite_lookback, offsets_map["bite_lookback"])
    volume_mult = _numeric_candidates(baseline.bite_volume_mult, offsets_map["bite_volume_mult"])
    retest_windows = _numeric_candidates(
        baseline.bite_retest_window_hours,
        offsets_map["bite_retest_window_hours"],
    )
    min_rr = _numeric_candidates(baseline.bite_min_rr, offsets_map["bite_min_rr"])
    tp2_mult = _numeric_candidates(baseline.bite_tp2_mult, offsets_map["bite_tp2_mult"])
    min_move_atr = _numeric_candidates(baseline.bite_min_move_atr, offsets_map["bite_min_move_atr"])
    max_retest_depth = _numeric_candidates(baseline.bite_max_retest_depth, offsets_map["bite_max_retest_depth"])
    confirmation_bars = _numeric_candidates(
        baseline.bite_confirmation_bars,
        offsets_map["bite_confirmation_bars"],
    )
    entry_triggers = tuple(offsets_map["bite_entry_trigger"])

    combinations: list[BeeBiteParams] = []
    for lb, vm, rw, rr, tp2, mma, mrd, confirm_bars, trigger in product(
        lookbacks,
        volume_mult,
        retest_windows,
        min_rr,
        tp2_mult,
        min_move_atr,
        max_retest_depth,
        confirmation_bars,
        entry_triggers,
    ):
        params = replace(
            baseline,
            bite_lookback=int(lb),
            bite_volume_mult=float(vm),
            bite_retest_window_hours=int(rw),
            bite_min_rr=float(rr),
            bite_tp2_mult=float(tp2),
            bite_min_move_atr=float(mma),
            bite_max_retest_depth=float(mrd),
            bite_confirmation_bars=int(confirm_bars),
            bite_entry_trigger=trigger,
            bite_grid_mode=grid_mode,
        )
        try:
            validate_bee_bite_params(params)
        except ValueError:
            continue
        combinations.append(params)

    return combinations


def validate_bee_bite_runtime(
    *,
    profile_id: BeeBiteProfileId,
    grid_mode: BeeBiteGridMode,
    top_n: int | None,
    reclaim_mode: BeeBiteReclaimMode,
    retest_mode: BeeBiteRetestMode,
    cooldown_hours: int,
    max_age_range_hours: int,
) -> None:
    if profile_id not in BEE_BITE_PROFILE_IDS:
        raise ValueError(f"Неподдерживаемый профиль bee_bite: {profile_id}")
    if grid_mode not in BEE_BITE_GRID_MODES:
        raise ValueError(f"Неподдерживаемый режим сетки bee_bite: {grid_mode}")

    runtime = BEE_BITE_PROFILE_RUNTIME[profile_id]

    if reclaim_mode not in BEE_BITE_RECLAIM_MODES:
        raise ValueError(f"Неподдерживаемый reclaim-режим bee_bite: {reclaim_mode}")
    if retest_mode not in BEE_BITE_RETEST_MODES:
        raise ValueError(f"Неподдерживаемый retest-режим bee_bite: {retest_mode}")
    allowed_reclaim_by_profile: dict[BeeBiteProfileId, tuple[BeeBiteReclaimMode, ...]] = {
        "A": ("strict",),
        "B": ("strict", "balanced"),
        "C": ("balanced", "aggressive"),
    }
    allowed_retest_by_profile: dict[BeeBiteProfileId, tuple[BeeBiteRetestMode, ...]] = {
        "A": ("confirmation",),
        "B": ("confirmation",),
        "C": ("confirmation", "immediate"),
    }

    allowed_reclaim = allowed_reclaim_by_profile[profile_id]
    if reclaim_mode not in allowed_reclaim:
        supported = ", ".join(allowed_reclaim)
        raise ValueError(f"профиль {profile_id} поддерживает reclaim_mode только из [{supported}]")

    allowed_retest = allowed_retest_by_profile[profile_id]
    if retest_mode not in allowed_retest:
        supported = ", ".join(allowed_retest)
        raise ValueError(f"профиль {profile_id} поддерживает retest_mode только из [{supported}]")

    if cooldown_hours < 1 or cooldown_hours > 100:
        raise ValueError("параметр cooldown_hours должен быть в диапазоне [1, 100]")
    if max_age_range_hours < 1 or max_age_range_hours > 100:
        raise ValueError("параметр max_age_range_hours должен быть в диапазоне [1, 100]")

    if top_n is None:
        return

    if top_n < runtime.top_n_min or top_n > runtime.top_n_max:
        raise ValueError(
            f"для bee_bite профиля {profile_id} параметр --top-n должен быть в диапазоне "
            f"[{runtime.top_n_min}, {runtime.top_n_max}]"
        )
    if grid_mode == "expanded" and top_n < max(20, runtime.top_n_min):
        raise ValueError("для bee_bite в режиме expanded параметр --top-n должен быть >= 20")
    if grid_mode == "research" and top_n < max(30, runtime.top_n_min):
        raise ValueError("для bee_bite в режиме research параметр --top-n должен быть >= 30")


def get_bee_bite_score_threshold(profile_id: BeeBiteProfileId) -> ScoreThreshold:
    return BEE_BITE_PROFILE_SCORE_THRESHOLDS[profile_id]


def get_bee_bite_top_n(profile_id: BeeBiteProfileId) -> int:
    return BEE_BITE_PROFILE_TOP_N[profile_id]


def get_bee_bite_runtime(profile_id: BeeBiteProfileId) -> BeeBiteProfileRuntime:
    return BEE_BITE_PROFILE_RUNTIME[profile_id]


def get_bee_bite_reclaim_settings(reclaim_mode: BeeBiteReclaimMode) -> BeeBiteReclaimSettings:
    return BEE_BITE_RECLAIM_SETTINGS[reclaim_mode]


def validate_bee_bite_params(params: BeeBiteParams) -> None:
    if params.bite_lookback < 5 or params.bite_lookback > 120:
        raise ValueError("параметр bite_lookback должен быть в диапазоне [5, 120]")
    if params.bite_volume_mult <= 0.0 or params.bite_volume_mult > 5.0:
        raise ValueError("параметр bite_volume_mult должен быть в диапазоне (0, 5]")
    if params.bite_retest_window_hours < 4 or params.bite_retest_window_hours > 96:
        raise ValueError("параметр bite_retest_window_hours должен быть в диапазоне [4, 96]")
    if params.bite_min_rr <= 1.0 or params.bite_min_rr > 8.0:
        raise ValueError("параметр bite_min_rr должен быть в диапазоне (1.0, 8.0]")
    if params.bite_tp2_mult <= 1.0 or params.bite_tp2_mult > 4.0:
        raise ValueError("параметр bite_tp2_mult должен быть в диапазоне (1.0, 4.0]")
    if not 0.0 < params.bite_max_retest_depth <= 2.0:
        raise ValueError("параметр bite_max_retest_depth должен быть в диапазоне (0, 2]")
    if params.bite_confirmation_bars < 1 or params.bite_confirmation_bars > 6:
        raise ValueError("параметр bite_confirmation_bars должен быть в диапазоне [1, 6]")
    if params.bite_min_depth_threshold <= 0.0 or params.bite_min_depth_threshold > 1.0:
        raise ValueError("параметр bite_min_depth_threshold должен быть в диапазоне (0, 1]")
    if params.bite_micro_offset < 0.0 or params.bite_micro_offset > 1.0:
        raise ValueError("параметр bite_micro_offset должен быть в диапазоне [0, 1]")
    if params.bite_reclaim_limit_bars < 1 or params.bite_reclaim_limit_bars > 20:
        raise ValueError("параметр bite_reclaim_limit_bars должен быть в диапазоне [1, 20]")
    if params.bite_max_age_range_hours < 1 or params.bite_max_age_range_hours > 100:
        raise ValueError("параметр bite_max_age_range_hours должен быть в диапазоне [1, 100]")
    if params.bite_cooldown_hours < 1 or params.bite_cooldown_hours > 100:
        raise ValueError("параметр bite_cooldown_hours должен быть в диапазоне [1, 100]")
    if params.bite_min_stop_atr_ratio <= 0.0 or params.bite_min_stop_atr_ratio > 1.0:
        raise ValueError("параметр bite_min_stop_atr_ratio должен быть в диапазоне (0, 1]")
    if params.bite_t_max_in_trade is not None and params.bite_t_max_in_trade < 1:
        raise ValueError("параметр bite_t_max_in_trade должен быть >= 1 или None")

    if params.bite_entry_trigger == EntryTrigger.PRICE_CONFIRMATION and params.bite_confirmation_bars < 2:
        raise ValueError("режим reclaim (PRICE_CONFIRMATION) требует bite_confirmation_bars >= 2")
    if params.bite_entry_trigger == EntryTrigger.IMMEDIATE and params.bite_confirmation_bars != 1:
        raise ValueError("режим retest (IMMEDIATE) требует bite_confirmation_bars == 1")


def _numeric_candidates(
    baseline_value: int | float | None,
    offsets: tuple[int | float | EntryTrigger, ...],
) -> tuple[int | float | None, ...]:
    if baseline_value is None:
        return (None,)

    values: set[int | float] = set()
    for offset in offsets:
        if isinstance(offset, EntryTrigger):
            continue
        candidate = baseline_value + offset
        if isinstance(baseline_value, int):
            values.add(int(candidate))
        else:
            values.add(round(float(candidate), 4))
    return tuple(sorted(values))


def _apply_runtime_modes(
    *,
    params: BeeBiteParams,
    reclaim_mode: BeeBiteReclaimMode,
    retest_mode: BeeBiteRetestMode,
    cooldown_hours: int,
    max_age_range_hours: int,
) -> BeeBiteParams:
    runtime = get_bee_bite_runtime(params.bite_profile_id)
    reclaim_settings = get_bee_bite_reclaim_settings(reclaim_mode)

    entry_trigger = EntryTrigger.IMMEDIATE if retest_mode == "immediate" else EntryTrigger.PRICE_CONFIRMATION
    confirmation_bars = 1 if retest_mode == "immediate" else max(2, params.bite_confirmation_bars)
    min_depth = runtime.min_depth_threshold * reclaim_settings.micro_offset_multiplier

    return replace(
        params,
        bite_entry_trigger=entry_trigger,
        bite_confirmation_bars=confirmation_bars,
        bite_reclaim_mode=reclaim_mode,
        bite_retest_mode=retest_mode,
        bite_cooldown_hours=cooldown_hours,
        bite_max_age_range_hours=max_age_range_hours,
        bite_min_depth_threshold=min_depth,
        bite_micro_offset=runtime.micro_offset * reclaim_settings.micro_offset_multiplier,
        bite_reclaim_limit_bars=runtime.reclaim_limit_bars + reclaim_settings.reclaim_limit_delta,
    )
