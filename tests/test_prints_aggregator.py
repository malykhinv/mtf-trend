from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.utils.prints_aggregator import PrintsAggregator, TradePrint


def test_prints_aggregator_respects_time_window() -> None:
    aggregator = PrintsAggregator()
    base_time = datetime.now(tz=config.UTC)
    aggregator.add_print(TradePrint(timestamp=base_time, is_buy=True, quantity=2.0))

    later = base_time + timedelta(seconds=config.AGGR_WINDOW_SEC + 1)
    imbalance = aggregator.imbalance(now=later)

    assert imbalance == 0.0


def test_prints_aggregator_clear_resets_state() -> None:
    aggregator = PrintsAggregator()
    moment = datetime.now(tz=config.UTC)
    aggregator.add_print(TradePrint(timestamp=moment, is_buy=True, quantity=1.5))
    aggregator.add_print(TradePrint(timestamp=moment, is_buy=False, quantity=0.5))
    assert aggregator.imbalance(now=moment) > 0.0

    aggregator.clear()

    assert aggregator.imbalance(now=moment) == 0.0
