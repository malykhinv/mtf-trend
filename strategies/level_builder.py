from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from domain.models import Band, Level, LevelPattern, SwingsOutput, SwingHigh

_EPSILON = 1e-9


@dataclass(frozen=True)
class LevelBuildResult:
    """Result of building a level from swings."""

    level: Level
    used_swings: tuple[SwingHigh, ...]
    swings_above_level: tuple[SwingHigh, ...]
    consolidation_band: Band | None


def build_level(swings_output: SwingsOutput, atr_h: float) -> LevelBuildResult | None:
    """Build a level from the provided swings output.

    The function implements templates (A) and (B) defined by the strategy
    specification. The resulting level must satisfy the following constraints:

    * Width strictly less than ``atr_h``.
    * For template (A) at least two swings are required.
    * For template (B) a single swing accompanied by a consolidation band is
      required.
    * No more than one swing high is allowed above the computed ``level_top``.
    """

    if atr_h <= 0:
        raise ValueError("atr_h должен быть положительным")

    swings = tuple(swings_output.swings)
    if not swings:
        return None

    if len(swings) >= 2:
        result = _build_template_a(swings, atr_h)
        if result is not None:
            return result

    if len(swings) == 1 and swings_output.has_consolidation:
        result = _build_template_b(swings[0], swings_output, atr_h)
        if result is not None:
            return result

    return None


def _build_template_a(swings: Sequence[SwingHigh], atr_h: float) -> LevelBuildResult | None:
    swings_by_price = sorted(swings, key=lambda swing: swing.price)
    total = len(swings_by_price)

    # We may exclude the highest swing to tolerate a single fake breakout
    # above the level (constraint §8.6).
    for excluded in range(0, min(2, total)):
        subset = swings_by_price[: total - excluded]
        if len(subset) < 2:
            break

        level_low = subset[0].price
        level_top = subset[-1].price
        width = level_top - level_low
        if width >= atr_h - _EPSILON:
            continue

        level = Level(
            level_low=level_low,
            level_top=level_top,
            width=width,
            pattern=LevelPattern.MULTIPLE_SWINGS,
        )

        used_swings = _preserve_original_order(swings, subset)
        swings_above = tuple(swing for swing in swings if swing.price > level_top + _EPSILON)
        if len(swings_above) > 1:
            continue

        return LevelBuildResult(
            level=level,
            used_swings=used_swings,
            swings_above_level=swings_above,
            consolidation_band=None,
        )

    return None


def _build_template_b(
    swing: SwingHigh,
    swings_output: SwingsOutput,
    atr_h: float,
) -> LevelBuildResult | None:
    band = swings_output.consolidation_band
    if band is None:
        return None

    level_low = band.low
    level_top = swing.price
    width = level_top - level_low
    if width >= atr_h - _EPSILON:
        return None
    if band.high < level_top - _EPSILON:
        return None

    level = Level(
        level_low=level_low,
        level_top=level_top,
        width=width,
        pattern=LevelPattern.SINGLE_WITH_CONSOLIDATION,
    )

    swings_above = tuple(sw for sw in swings_output.swings if sw.price > level_top + _EPSILON)
    if len(swings_above) > 1:
        return None

    return LevelBuildResult(
        level=level,
        used_swings=(swing,),
        swings_above_level=swings_above,
        consolidation_band=band,
    )


def _preserve_original_order(
    original: Sequence[SwingHigh],
    subset: Iterable[SwingHigh],
) -> tuple[SwingHigh, ...]:
    subset_set = set(subset)
    return tuple(swing for swing in original if swing in subset_set)


__all__ = ["LevelBuildResult", "build_level"]
