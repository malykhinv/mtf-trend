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
        self.ticker_prices = [0.0]
        self.fetch_ticker_calls = []

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
            if amount < 1:
                # trailing stop closes eventually
                self.fetch_order_responses[order_id] = [
                    {"id": order_id, "status": "open"},
                    {"id": order_id, "status": "closed"},
                ]
            else:
                # initial stop-loss remains open until cancelled
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

    def fetch_ticker(self, symbol):
        price = self.ticker_prices.pop(0) if self.ticker_prices else 0.0
        self.fetch_ticker_calls.append((symbol, price))
        return {"last": price}


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


def test_exit_orders_with_trailing_stop_and_logging():
    exchange = DummyExchange()
    exchange.ticker_prices = [20000, 20050, 20050]
    logs = []
    trader = FuturesTrader("key", "secret", exchange=exchange, trade_logger=logs.append)

    trader.place_market_order("BTC/USDT", "buy", 1, tp=21000, sl=19000)

    # entry + tp + initial sl + trailing stop updates
    assert len(exchange.create_order_calls) >= 5

    # take profit should be reduce-only limit for half the position
    _, tp_order, *rest = exchange.create_order_calls
    assert tp_order[2] == "sell"
    assert tp_order[3] == 0.5
    assert tp_order[5] == {"reduceOnly": True}

    stop_calls = [c for c in exchange.create_order_calls if c[1] == "stop"]
    assert stop_calls[0][5]["stopPrice"] == 19000
    assert stop_calls[-1][5]["stopPrice"] > 19000

    # initial stop should be cancelled after TP1
    assert exchange.cancel_order_calls[0] == ("3", "BTC/USDT")

    # two trade logs: TP1 and trailing exit
    assert len(logs) == 2
    assert logs[0]["exit"] == 21000
    assert logs[1]["exit"] > 19000


def test_retry_on_network_error():
    exchange = DummyExchange()
    exchange.create_order_should_fail_once = True
    trader = FuturesTrader("key", "secret", exchange=exchange)

    trader.place_market_order("BTC/USDT", "buy", 1)

    # first attempt fails then succeeds
    assert len(exchange.create_order_calls) == 1
    # ensure fetch_order eventually called
    assert exchange.fetch_order_calls


def test_market_order_network_failure():
    class FailingExchange(DummyExchange):
        def create_order(self, *args, **kwargs):  # type: ignore[override]
            raise ccxt.NetworkError("timeout")

    exchange = FailingExchange()
    trader = FuturesTrader("key", "secret", exchange=exchange)

    assert not trader.place_market_order("BTC/USDT", "buy", 1)

