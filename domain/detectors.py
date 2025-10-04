from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Iterable, Optional, Sequence, Tuple

from config.config import CONFIG
from config.models.trading_profile import TradingProfile
from config.models.wall_absolute_thresholds import WallAbsoluteThresholds

from book import OrderBook
from models.enums import Exchange, Side
from models.order_book import OrderBookLevel
from models.pressure import Pressure
from models.wall import Wall
from utils import compute_odr_weight, get_current_time, median_filter_of_three


def compute_odr(book: OrderBook, last_price: float, tick_size: float) -> Pressure:
    bid_levels: Tuple[OrderBookLevel, ...] = _collect_levels(
        book.iter_recent_band(Side.BID),
        book.best_bid(),
    )
    ask_levels: Tuple[OrderBookLevel, ...] = _collect_levels(
        book.iter_recent_band(Side.ASK),
        book.best_ask(),
    )
    buy_pressure: float = _sum_side_pressure(bid_levels, last_price, tick_size)
    sell_pressure: float = _sum_side_pressure(ask_levels, last_price, tick_size)
    raw_ratio: float = _compute_ratio(sell_pressure, buy_pressure)
    smoothed_ratio: float = book.update_odr_history(raw_ratio)
    return Pressure(
        computed_at=get_current_time(),
        buy_pressure=buy_pressure,
        sell_pressure=sell_pressure,
        imbalance_ratio=smoothed_ratio,
    )


def check_if_has_pressure(pressure: Pressure, want_long: bool) -> bool:
    if want_long:
        return pressure.imbalance_ratio <= CONFIG.odr.odr_in_long
    return pressure.imbalance_ratio >= CONFIG.odr.odr_in_short


def check_if_price_recent(level_price: float, recent_low: float, recent_high: float) -> bool:
    return recent_low <= level_price <= recent_high


def check_if_is_large_wall(
    level: OrderBookLevel,
    abs_usd_min: float,
    rel_mult_min: float,
    median_level_qty: float,
    persist_s: float,
    now: datetime,
) -> bool:
    lifetime: timedelta = now - level.first_seen_at
    if lifetime < timedelta(seconds=persist_s):
        return False
    if level.notional < abs_usd_min:
        return False
    if median_level_qty > 0.0 and level.quantity < median_level_qty * rel_mult_min:
        return False
    return True


def check_if_has_near_wall(
    book: OrderBook,
    side_stop: Side,
    profile: TradingProfile,
) -> Optional[Wall]:
    abs_threshold: float = _resolve_absolute_threshold(profile)
    level: Optional[OrderBookLevel] = book.find_nearest_wall(
        side=side_stop,
        min_notional=abs_threshold,
    )
    if level is None:
        return None
    now: datetime = get_current_time()
    persist_s: float = _resolve_persist(profile)
    median_qty: float = _compute_median_quantity(book.iter_recent_band(side_stop))
    if not check_if_is_large_wall(
        level,
        abs_threshold,
        CONFIG.walls.relative_multiplier,
        median_qty,
        persist_s,
        now,
    ):
        return None
    return Wall(
        exchange=_resolve_exchange(),
        symbol="",
        side=side_stop,
        price=level.price,
        quantity=level.quantity,
        notional=level.notional,
        first_seen_at=level.first_seen_at,
        last_seen_at=level.last_update_at,
    )


def check_if_has_opposite_wall(
    book: OrderBook,
    side_move: Side,
    our_wall: Wall,
    profile: TradingProfile,
) -> bool:
    opposite_side: Side = Side.ASK if side_move is Side.BID else Side.BID
    abs_threshold: float = _resolve_absolute_threshold(profile)
    level: Optional[OrderBookLevel] = book.find_nearest_wall(
        side=opposite_side,
        min_notional=abs_threshold,
    )
    if level is None:
        return False
    now: datetime = get_current_time()
    persist_s: float = _resolve_persist(profile)
    median_qty: float = _compute_median_quantity(book.iter_recent_band(opposite_side))
    if not check_if_is_large_wall(
        level,
        abs_threshold,
        CONFIG.walls.relative_multiplier,
        median_qty,
        persist_s,
        now,
    ):
        return False
    return level.notional >= our_wall.notional


def _collect_levels(
    levels: Iterable[OrderBookLevel],
    fallback: Optional[OrderBookLevel],
) -> Tuple[OrderBookLevel, ...]:
    collected: Tuple[OrderBookLevel, ...] = tuple(levels)
    if collected:
        return collected
    return () if fallback is None else (fallback,)


def _sum_side_pressure(
    levels: Sequence[OrderBookLevel],
    last_price: float,
    tick_size: float,
) -> float:
    contributions: list[float] = [
        compute_odr_weight(level.price, last_price, tick_size) * level.notional
        for level in levels
    ]
    smoothed: Tuple[float, ...] = median_filter_of_three(contributions)
    return float(sum(smoothed))


def _compute_ratio(sell_pressure: float, buy_pressure: float) -> float:
    if buy_pressure <= 0.0:
        return float("inf") if sell_pressure > 0.0 else 1.0
    return sell_pressure / buy_pressure


def _compute_median_quantity(levels: Iterable[OrderBookLevel]) -> float:
    quantities: Tuple[float, ...] = tuple(level.quantity for level in levels)
    if not quantities:
        return 0.0
    return float(median(quantities))


def _resolve_absolute_threshold(profile: TradingProfile) -> float:
    thresholds: WallAbsoluteThresholds = CONFIG.walls.absolute
    if profile is TradingProfile.TOP:
        return float(thresholds.top_usd)
    if profile is TradingProfile.ALT:
        return float(thresholds.alt_usd)
    if profile is TradingProfile.LISTING:
        return float(thresholds.listing_usd)
    return float(thresholds.top_usd)


def _resolve_persist(profile: TradingProfile) -> float:
    if profile is TradingProfile.TOP:
        return CONFIG.walls.persist_s_top
    if profile is TradingProfile.ALT:
        return CONFIG.walls.persist_s_alt
    if profile is TradingProfile.LISTING:
        return CONFIG.walls.persist_s_listing
    return CONFIG.walls.persist_s_top


def _resolve_exchange() -> Exchange:
    return Exchange(CONFIG.general.exchange.value)


__all__ = [
    "compute_odr",
    "check_if_has_pressure",
    "check_if_price_recent",
    "check_if_is_large_wall",
    "check_if_has_near_wall",
    "check_if_has_opposite_wall",
]
