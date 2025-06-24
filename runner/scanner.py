from data.binance_client import get_binance_client
from domain.setup_detector import SetupDetector
from notifier.formatter import format_signal
from config.settings.constants import TF_MAP
from data.loader import Loader
from domain.risk_filters import is_low_liquidity, is_abnormal_spike
from notifier.telegram import TelegramNotifier
from utils.logger import log


class Scanner:
    def __init__(self):
        self.loader = Loader()
        self.notifier = TelegramNotifier()

    def run(self):
        log("Запущен цикл сканирования.")
        symbols = self.loader.get_filtered_symbols()
        log(f"Отобрано {len(symbols)} символов для анализа.")

        for symbol in symbols:
            try:
                log(f"Анализ символа {symbol}...")
                bars_by_tf = self.loader.fetch_multiple_timeframes(symbol, TF_MAP)

                if is_low_liquidity(bars_by_tf['1d']):
                    log(f"Низкая ликвидность по {symbol}. Пропускаем.")
                    continue

                if is_abnormal_spike(bars_by_tf['1h']):
                    log(f"Аномальный всплеск по {symbol}. Пропускаем.")
                    continue

                atr_by_tf = {
                    tf: sum([abs(b.high - b.low) for b in bars]) / len(bars)
                    for tf, bars in bars_by_tf.items()
                }

                detector = SetupDetector(symbol, bars_by_tf, atr_by_tf)
                signal = detector.detect()

                if signal:
                    message = format_signal(signal)
                    self.notifier.send_message(message)
                else:
                    log(f"Сетап по {symbol} не подтверждён.")

            except Exception as error:
                log(f"Ошибка при обработке {symbol}: {error}")

        log("Цикл сканирования завершён.")
