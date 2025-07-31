import ccxt

from utils.futures_trader import FuturesTrader


class DummyExchange:
    """Minimal ccxt-like exchange for testing FuturesTrader."""

    def __init__(self):
        self.set_margin_mode_calls = []
        self.set_leverage_calls = []
        self.create_order_calls = []
        self.fetch_order_calls = []
        self.cancel_order_calls = []
        self.create_order_should_fail_once = False
        self._failed_once = False
        self.fetch_order_responses = {}

    def set_margin_mode(self, mode, symbol):
        self.set_margin_mode_calls.append((mode, symbol))

    def set_leverage(self, leverage, symbol):
        self.set_leverage_calls.append((leverage, symbol))

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if self.create_order_should_fail_once and not self._failed_once:
            self._failed_once = True
            raise ccxt.NetworkError("timeout")
        order_id = str(len(self.create_order_calls) + 1)
        self.create_order_calls.append((symbol, type_, side, amount, price, params))
        # default order life-cycle: open then close
        if params and params.get("stopPrice"):
            # stop-loss remains open until cancelled
            self.fetch_order_responses[order_id] = [
                {"id": order_id, "status": "open"},
                {"id": order_id, "status": "open"},
            ]
        elif params and params.get("reduceOnly"):
            # take-profit fills immediately
            self.fetch_order_responses[order_id] = [
                {"id": order_id, "status": "closed"}
            ]
        else:
            self.fetch_order_responses[order_id] = [
                {"id": order_id, "status": "open"},
                {"id": order_id, "status": "closed"},
            ]
        return {"id": order_id, "status": "open"}

    def fetch_order(self, order_id, symbol):
        self.fetch_order_calls.append((order_id, symbol))
        responses = self.fetch_order_responses.get(order_id, [{"id": order_id, "status": "closed"}])
        result = responses.pop(0)
        self.fetch_order_responses[order_id] = responses
        return result

    def cancel_order(self, order_id, symbol):
        self.cancel_order_calls.append((order_id, symbol))


def test_market_order_sets_leverage_and_margin():
    exchange = DummyExchange()
    trader = FuturesTrader("key", "secret", exchange=exchange)

    trader.place_market_order("BTC/USDT", "buy", 1)

    assert exchange.set_margin_mode_calls == [("ISOLATED", "BTC/USDT")]
    assert exchange.set_leverage_calls == [(3, "BTC/USDT")]
    assert exchange.create_order_calls[0][1] == "market"


def test_limit_maker_uses_gtx():
    exchange = DummyExchange()
    trader = FuturesTrader("key", "secret", exchange=exchange)

    trader.place_limit_maker_order("BTC/USDT", "sell", 1, 20000)

    params = exchange.create_order_calls[0][5]
    assert params == {"timeInForce": "GTX"}


def test_exit_orders_after_fill():
    exchange = DummyExchange()
    trader = FuturesTrader("key", "secret", exchange=exchange)

    trader.place_market_order("BTC/USDT", "buy", 1, tp=21000, sl=19000)

    # three create_order calls: entry, tp, sl
    assert len(exchange.create_order_calls) == 3
    # take profit should be reduce-only limit
    entry, tp_order, sl_order = exchange.create_order_calls
    assert tp_order[2] == "sell"
    assert tp_order[5] == {"reduceOnly": True}
    assert sl_order[5]["stopPrice"] == 19000
    # stop-loss should be cancelled once TP fills
    assert exchange.cancel_order_calls == [("3", "BTC/USDT")]


def test_retry_on_network_error():
    exchange = DummyExchange()
    exchange.create_order_should_fail_once = True
    trader = FuturesTrader("key", "secret", exchange=exchange)

    trader.place_market_order("BTC/USDT", "buy", 1)

    # first attempt fails then succeeds
    assert len(exchange.create_order_calls) == 1
    # ensure fetch_order eventually called
    assert exchange.fetch_order_calls

