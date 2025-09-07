from __future__ import annotations

from domain.services.rest_client import RestClient
import constants


class UniverseBuilder:
    def __init__(self, rest: RestClient) -> None:
        self._rest = rest

    def build(self) -> list[str]:
        symbols: list[str] = []

        r = self._rest._client.get("/fapi/v1/ticker/24hr")
        r.raise_for_status()
        for info in r.json():
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

            depth = self._rest._client.get(
                "/fapi/v1/depth", params={"symbol": sym, "limit": 10}
            )
            depth.raise_for_status()
            bids = depth.json().get("bids", [])
            top10_bid_usdt = sum(float(p) * float(q) for p, q in bids)
            if top10_bid_usdt < constants.UNIVERSE_MIN_TOP10_BID_USDT:
                continue

            symbols.append(sym)

        return symbols
