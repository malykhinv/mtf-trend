import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.coin_filter import select_top_coins, MAX_COINS


def test_select_top_coins_limits_and_sorts():
    # create 200 coins with increasing volume
    coins = [
        {"symbol": f"COIN{i}", "volume": i}
        for i in range(200)
    ]
    random.shuffle(coins)  # ensure order is unsorted

    result = select_top_coins(coins)

    # should only keep MAX_COINS entries
    assert len(result) == MAX_COINS

    # volumes should be sorted descending
    volumes = [c["volume"] for c in result]
    assert volumes == sorted(volumes, reverse=True)

    # top volume should be the highest available
    assert result[0]["volume"] == 199
    # last volume should match the cutoff point
    assert result[-1]["volume"] == 199 - MAX_COINS + 1
