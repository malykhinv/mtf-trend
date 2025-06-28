from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.models.timeframe import Timeframe
from domain.risk_filters import is_low_liquidity, is_abnormal_spike, is_anomalous_trend
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from utils.logger import log


class Scanner:
    def __init__(self):
        self.loader = Loader()
        self.orders_notifier = TelegramNotifier(TELEGRAM_ORDERS_BOT_TOKEN)
        self.events_notifier = TelegramNotifier(TELEGRAM_EVENTS_BOT_TOKEN)
        self.tracker = PositionTrackerService()
        self.trade_executor = TradeExecutor(self.loader.binance, self.tracker)

    def run(self):
        log("Запущен цикл сканирования.")
        symbols = self.loader.get_filtered_symbols()
        log(f"Отобрано {len(symbols)} символов для анализа.")

        for symbol in symbols:
            try:
                print()
                log(f"{symbol}")
                bars_by_tf = self.loader.fetch_multiple_timeframes(
                    symbol,
                    [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15]
                )

                if is_low_liquidity(bars_by_tf[Timeframe.D1]):
                    log(f"Низкая ликвидность по {symbol}. Пропускаем.")
                    continue

                if is_abnormal_spike(bars_by_tf[Timeframe.H1]):
                    log(f"Аномальный всплеск по {symbol}. Пропускаем.")
                    continue

                atr_by_tf = {
                    tf: sum([abs(b.high - b.low) for b in bars]) / len(bars)
                    for tf, bars in bars_by_tf.items()
                }

                if is_anomalous_trend(bars_by_tf[Timeframe.H1], atr_by_tf[Timeframe.H1]):
                    log(f"Аномально сильный тренд по {symbol}. Пропускаем.")
                    continue

                detector = SetupDetector(symbol, bars_by_tf, atr_by_tf)
                signal = detector.detect()

                if signal is not None:
                    message = format_message(signal)
                    if signal.is_order_signal:
                        self.trade_executor.execute(
                            symbol=signal.symbol,
                            scenario=signal.scenario,
                            side=signal.side,
                            sl=signal.sl,
                            tp=signal.tp
                        )
                        self.orders_notifier.send_message(message)
                    elif signal.is_event_signal:
                        self.events_notifier.send_message(message)
                else:
                    log(f"Сетап по {symbol} не подтверждён.")

            except Exception as error:
                log(f"Ошибка при обработке {symbol}: {error}")
                raise

        log("Цикл сканирования завершён.")
