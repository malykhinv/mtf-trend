from typing import Literal
from config.settings.constants import POSITION_USDT, MIN_RR
from ccxt import binance


class TradeExecutor:
    def __init__(self, client: binance):
        self.client = client

    def execute(
        self,
        symbol: str,
        direction: Literal['long', 'short'],
        sl: float,
        tp: float,
        amount_usdt: float = POSITION_USDT
    ):
        """
        Открывает рыночный ордер и выставляет SL/TP, пересчитывая RR по фактической цене.
        """
        try:
            market_symbol = symbol.replace("USDT", "/USDT")

            side: Literal['buy', 'sell'] = 'buy' if direction == 'long' else 'sell'
            opposite: Literal['buy', 'sell'] = 'sell' if direction == 'long' else 'buy'

            # Получаем текущую цену
            ticker = self.client.fetch_ticker(market_symbol)
            actual_entry = ticker['last']
            market = self.client.market(market_symbol)

            # Пересчёт RR
            risk = abs(actual_entry - sl)
            reward = abs(tp - actual_entry)
            actual_rr = reward / risk if risk > 0 else 0

            if actual_rr < MIN_RR:
                print(f"[ОТМЕНА] Новый RR={actual_rr:.2f} ниже {MIN_RR}. Сделка не открыта по {symbol}.")
                return

            # Проверка адекватности TP и SL
            if (direction == 'long' and (sl >= actual_entry or tp <= actual_entry)) or \
               (direction == 'short' and (sl <= actual_entry or tp >= actual_entry)):
                print(f"[ОТМЕНА] SL/TP не соответствуют направлению сделки по {symbol}.")
                return

            # Рассчитываем объём
            amount = round(amount_usdt / actual_entry, int(market['precision']['amount']))

            # Рыночный вход
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=side,
                amount=amount
            )

            # Take Profit (TP)
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

            # Stop Loss (SL)
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

            print(f"[ВХОД] {symbol} {direction.upper()} @ {actual_entry}\nSL: {sl}, TP: {tp}, RR: {actual_rr:.2f}")

        except Exception as e:
            print(f"[ОШИБКА] Исполнение ордера по {symbol} не удалось: {e}")
