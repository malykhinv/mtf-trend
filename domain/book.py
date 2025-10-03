"""In-memory order book representation with recent band tracking."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, List, Optional, Tuple

from .models.enums import Side
from .models.order_book import OrderBookLevel, OrderBookSnapshot, OrderBookUpdate
from .models._timezone import ensure_current_timezone


@dataclass(slots=True)
class _StoredLevel:
    """Mutable storage for a single order book level."""

    price: float
    quantity: float
    notional: float
    first_seen_at: datetime
    last_update_at: datetime
    min_quantity_seen: float
    max_quantity_seen: float

    def update(self, *, quantity: float, timestamp: datetime) -> None:
        """Update statistics for the level with the latest values."""

        ensure_current_timezone(timestamp)
        self.quantity = quantity
        self.notional = self.price * quantity
        self.last_update_at = timestamp
        if quantity < self.min_quantity_seen:
            self.min_quantity_seen = quantity
        if quantity > self.max_quantity_seen:
            self.max_quantity_seen = quantity

    def to_dataclass(self) -> OrderBookLevel:
        """Convert mutable storage back to an immutable domain model."""

        return OrderBookLevel(
            price=self.price,
            quantity=self.quantity,
            notional=self.notional,
            first_seen_at=self.first_seen_at,
            last_update_at=self.last_update_at,
            min_quantity_seen=self.min_quantity_seen,
            max_quantity_seen=self.max_quantity_seen,
        )


@dataclass(slots=True)
class _RecentBandEntry:
    """Single entry in the recent band ring buffer."""

    price: float
    level_index: int
    timestamp: datetime


class RecentBand:
    """Ring buffer tracking recently touched price levels."""

    __slots__ = (
        "_capacity",
        "_entries",
        "_next",
        "_count",
        "_index_prices",
        "_index_slots",
    )

    def __init__(self, capacity: int) -> None:
        self._capacity = max(0, capacity)
        self._entries: List[Optional[_RecentBandEntry]] = [None] * self._capacity
        self._next = 0
        self._count = 0
        self._index_prices: List[float] = []
        self._index_slots: List[int] = []

    def __len__(self) -> int:
        return self._count

    def clear(self) -> None:
        """Remove all entries from the ring buffer."""

        self._entries = [None] * self._capacity
        self._next = 0
        self._count = 0
        self._index_prices.clear()
        self._index_slots.clear()

    def _locate_price(self, price: float) -> Tuple[int, Optional[int]]:
        """Return index buffer location and stored slot for the price."""

        index_pos = bisect_left(self._index_prices, price)
        if (
            index_pos < len(self._index_prices)
            and self._index_prices[index_pos] == price
        ):
            return index_pos, self._index_slots[index_pos]
        return index_pos, None

    def _remove_from_index(self, price: float) -> Optional[int]:
        """Remove price from index buffers and return previous slot if any."""

        index_pos, slot = self._locate_price(price)
        if slot is None:
            return None
        del self._index_prices[index_pos]
        del self._index_slots[index_pos]
        return slot

    def _store_in_index(self, price: float, slot: int) -> None:
        """Insert or update slot reference for the provided price."""

        index_pos, existing_slot = self._locate_price(price)
        if existing_slot is None:
            self._index_prices.insert(index_pos, price)
            self._index_slots.insert(index_pos, slot)
        else:
            self._index_slots[index_pos] = slot

    def record(self, price: float, level_index: int, timestamp: datetime) -> None:
        """Store the latest state for the provided price level."""

        if self._capacity == 0:
            return
        ensure_current_timezone(timestamp)

        previous_slot = self._remove_from_index(price)
        if previous_slot is not None:
            if self._entries[previous_slot] is not None:
                self._entries[previous_slot] = None
                self._count -= 1

        slot = self._next
        self._next = (self._next + 1) % self._capacity

        evicted = self._entries[slot]
        if evicted is not None:
            self._remove_from_index(evicted.price)
            self._count -= 1

        self._entries[slot] = _RecentBandEntry(
            price=price, level_index=level_index, timestamp=timestamp
        )
        self._store_in_index(price, slot)
        self._count += 1

    def discard(self, price: float) -> None:
        """Remove a price level from the recent band if it exists."""

        slot = self._remove_from_index(price)
        if slot is None:
            return
        entry = self._entries[slot]
        if entry is not None and entry.price == price:
            self._entries[slot] = None
            self._count -= 1

    def contains(self, price: float) -> bool:
        """Check if the price is currently tracked in the recent band."""

        _, slot = self._locate_price(price)
        return slot is not None

    def iter_newest_first(self) -> Iterator[_RecentBandEntry]:
        """Iterate over entries starting from the newest one."""

        if self._capacity == 0 or self._count == 0:
            return
        remaining = self._count
        index = (self._next - 1) % self._capacity
        visited = 0
        while remaining > 0 and visited < self._capacity:
            entry = self._entries[index]
            if entry is not None:
                yield entry
                remaining -= 1
            index = (index - 1) % self._capacity
            visited += 1

    def iter_oldest_first(self) -> Iterator[_RecentBandEntry]:
        """Iterate over entries starting from the oldest one."""

        if self._capacity == 0 or self._count == 0:
            return
        remaining = self._count
        index = self._next
        visited = 0
        while remaining > 0 and visited < self._capacity:
            entry = self._entries[index]
            if entry is not None:
                yield entry
                remaining -= 1
            index = (index + 1) % self._capacity
            visited += 1

    def latest_level_index(self, price: float) -> Optional[int]:
        """Return the last recorded position for the provided price."""

        _, slot = self._locate_price(price)
        if slot is None:
            return None
        entry = self._entries[slot]
        return None if entry is None else entry.level_index


class _BookSide:
    """Order book side with sorted levels and recent band membership."""

    __slots__ = (
        "_side",
        "_levels",
        "_sort_keys",
        "_index_prices",
        "_index_positions",
        "_recent_band",
    )

    def __init__(self, side: Side, *, recent_band_capacity: int) -> None:
        self._side = side
        self._levels: List[_StoredLevel] = []
        self._sort_keys: List[float] = []
        self._index_prices: List[float] = []
        self._index_positions: List[int] = []
        self._recent_band = RecentBand(recent_band_capacity)

    @property
    def recent_band(self) -> RecentBand:
        return self._recent_band

    def _sort_key(self, price: float) -> float:
        return -price if self._side is Side.BID else price

    def _find_insertion_index(self, price: float) -> int:
        key = self._sort_key(price)
        return bisect_left(self._sort_keys, key)

    def _locate_price(self, price: float) -> Tuple[int, Optional[int]]:
        index_pos = bisect_left(self._index_prices, price)
        if (
            index_pos < len(self._index_prices)
            and self._index_prices[index_pos] == price
        ):
            return index_pos, self._index_positions[index_pos]
        return index_pos, None

    def _assign_index(self, price: float, level_index: int) -> None:
        index_pos, existing_index = self._locate_price(price)
        if existing_index is None:
            self._index_prices.insert(index_pos, price)
            self._index_positions.insert(index_pos, level_index)
        else:
            self._index_positions[index_pos] = level_index

    def _remove_index(self, price: float) -> None:
        index_pos, existing_index = self._locate_price(price)
        if existing_index is None:
            return
        del self._index_prices[index_pos]
        del self._index_positions[index_pos]

    def _reindex(self, start: int) -> None:
        for index in range(start, len(self._levels)):
            level = self._levels[index]
            self._assign_index(level.price, index)

    def _create_level(self, level: OrderBookLevel) -> _StoredLevel:
        ensure_current_timezone(
            level.first_seen_at,
            level.last_update_at,
        )
        return _StoredLevel(
            price=level.price,
            quantity=level.quantity,
            notional=level.notional,
            first_seen_at=level.first_seen_at,
            last_update_at=level.last_update_at,
            min_quantity_seen=level.min_quantity_seen,
            max_quantity_seen=level.max_quantity_seen,
        )

    def update_level(self, level: OrderBookLevel) -> None:
        """Insert or update a single level based on an incremental event."""

        price = level.price
        if level.quantity <= 0:
            self.remove_level(price)
            return

        _, existing_index = self._locate_price(price)
        timestamp = level.last_update_at
        ensure_current_timezone(timestamp)

        if existing_index is not None:
            index = existing_index
            stored = self._levels[index]
            stored.update(quantity=level.quantity, timestamp=timestamp)
            stored.notional = level.notional
            if level.first_seen_at < stored.first_seen_at:
                stored.first_seen_at = level.first_seen_at
            if level.min_quantity_seen < stored.min_quantity_seen:
                stored.min_quantity_seen = level.min_quantity_seen
            if level.max_quantity_seen > stored.max_quantity_seen:
                stored.max_quantity_seen = level.max_quantity_seen
        else:
            stored = self._create_level(level)
            insert_at = self._find_insertion_index(price)
            self._levels.insert(insert_at, stored)
            self._sort_keys.insert(insert_at, self._sort_key(price))
            self._assign_index(price, insert_at)
            self._reindex(insert_at + 1)
            index = insert_at

        self._recent_band.record(price, index, timestamp)

    def remove_level(self, price: float) -> None:
        """Remove a level from the side if present."""

        _, index = self._locate_price(price)
        if index is None:
            return
        del self._levels[index]
        del self._sort_keys[index]
        self._remove_index(price)
        self._reindex(index)
        self._recent_band.discard(price)

    def replace_levels(self, levels: Iterable[OrderBookLevel]) -> None:
        """Replace current side state with provided levels (snapshot)."""

        self._levels = []
        self._sort_keys = []
        self._index_prices = []
        self._index_positions = []
        self._recent_band.clear()

        for position, level in enumerate(levels):
            stored = self._create_level(level)
            self._levels.append(stored)
            self._sort_keys.append(self._sort_key(level.price))
            self._assign_index(level.price, position)
            self._recent_band.record(level.price, position, level.last_update_at)

    def iter_levels(self) -> Iterator[_StoredLevel]:
        return iter(self._levels)

    def iter_recent_levels(self) -> Iterator[_StoredLevel]:
        """Yield levels that are currently inside the recent band."""

        for level in self._levels:
            if self._recent_band.contains(level.price):
                yield level

    def find_nearest_wall(self, *, min_notional: float) -> Optional[_StoredLevel]:
        """Return first level inside the recent band exceeding the threshold."""

        for level in self._levels:
            if not self._recent_band.contains(level.price):
                continue
            if level.notional >= min_notional:
                return level
        return None

    def to_dataclasses(self) -> Tuple[OrderBookLevel, ...]:
        return tuple(level.to_dataclass() for level in self._levels)


class OrderBook:
    """Aggregated bid/ask sides with helpers for recent band operations."""

    __slots__ = ("_bids", "_asks")

    def __init__(self, *, recent_band_capacity: int = 128) -> None:
        self._bids = _BookSide(Side.BID, recent_band_capacity=recent_band_capacity)
        self._asks = _BookSide(Side.ASK, recent_band_capacity=recent_band_capacity)

    @property
    def bids(self) -> _BookSide:
        return self._bids

    @property
    def asks(self) -> _BookSide:
        return self._asks

    def apply_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        """Reset the order book using a full snapshot."""

        self._bids.replace_levels(snapshot.bids)
        self._asks.replace_levels(snapshot.asks)

    def apply_update(self, update: OrderBookUpdate) -> None:
        """Apply incremental changes to both sides of the order book."""

        for level in update.bids:
            self._bids.update_level(level)
        for level in update.asks:
            self._asks.update_level(level)

    def iter_recent_band(self, side: Side) -> Iterator[OrderBookLevel]:
        """Iterate over levels inside the recent band for the requested side."""

        book_side = self._bids if side is Side.BID else self._asks
        for level in book_side.iter_recent_levels():
            yield level.to_dataclass()

    def find_nearest_wall(self, *, side: Side, min_notional: float) -> Optional[OrderBookLevel]:
        """Return nearest wall candidate on provided side within recent band."""

        book_side = self._bids if side is Side.BID else self._asks
        level = book_side.find_nearest_wall(min_notional=min_notional)
        return None if level is None else level.to_dataclass()

    def best_bid(self) -> Optional[OrderBookLevel]:
        try:
            level = next(self._bids.iter_levels())
        except StopIteration:
            return None
        return level.to_dataclass()

    def best_ask(self) -> Optional[OrderBookLevel]:
        try:
            level = next(self._asks.iter_levels())
        except StopIteration:
            return None
        return level.to_dataclass()


__all__ = [
    "OrderBook",
    "RecentBand",
]
