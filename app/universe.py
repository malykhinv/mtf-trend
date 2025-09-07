from __future__ import annotations

from domain.ports.rest_client import RestClient
import constants


class UniverseBuilder:
    def __init__(self, rest: RestClient) -> None:
        self._rest = rest

    def build(self) -> list[str]:
        symbols: list[str] = []

        tickers = self._rest.fetch_all_tickers()
        for info in tickers:
            sym = info["symbol"]
            if not sym.endswith("USDT"):
                continue

            quote_vol, _ = self._rest.get_24h_stats(sym)
            if quote_vol < constants.UNIVERSE_MIN_24H_USDT:
                continue

            bid = float(info["bidPrice"])
            ask = float(info["askPrice"])
            if bid <= 0:
                continue
            spread_bps = (ask - bid) / bid * 10_000.0
            if spread_bps > constants.UNIVERSE_MAX_SPREAD_BPS:
                continue

            bids = self._rest.get_depth(sym)
            top10_bid_usdt = sum(p * q for p, q in bids)
            if top10_bid_usdt < constants.UNIVERSE_MIN_TOP10_BID_USDT:
                continue

            symbols.append(sym)

        return symbols
