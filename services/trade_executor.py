from typing import Literal
from config.settings.constants import POSITION_USDT
from ccxt import binance


class TradeExecutor:
    def __init__(self, client: binance):
        self.client = client

    def execute(
        self,
        symbol: str,
        direction: Literal['long', 'short'],
        entry: float,
        sl: float,
        tp: float,
        amount_usdt: float = POSITION_USDT
    ):
        """
        Открывает рыночный ордер и выставляет SL/TP через params
        """
        try:
            market_symbol = symbol.replace("USDT", "/USDT")

            side: Literal['buy', 'sell'] = 'buy' if direction == 'long' else 'sell'
            opposite: Literal['buy', 'sell'] = 'sell' if direction == 'long' else 'buy'

            ticker = self.client.fetch_ticker(market_symbol)
            price = ticker['last']
            market = self.client.market(market_symbol)
            amount = round(amount_usdt / price, int(market['precision']['amount']))

            # Market order
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=side,
                amount=amount
            )

            # TP
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=opposite,
                amount=amount,
                params={
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': round(tp, market['precision']['price']),
                    'closePosition': True
                }
            )

            # SL
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=opposite,
                amount=amount,
                params={
                    'type': 'STOP_MARKET',
                    'stopPrice': round(sl, market['precision']['price']),
                    'closePosition': True
                }
            )

            print(f"Order: {symbol} {direction.upper()}\nEntry {entry}\nSL {sl}\nTP {tp}")

        except Exception as e:
            print(f"Ошибка при исполнении ордера по {symbol}: {e}")
