from domain.setup_detector import SetupDetector
from notifier.formatter import format_signal
from config.settings.constants import TF_MAP
from data.symbols import get_filtered_symbols
from data.loader import Loader
from domain.risk_filters import is_low_liquidity, is_news_spike, is_in_dead_hours
from datetime import datetime

from notifier.telegram import TelegramNotifier


class Scanner:
    def __init__(self, binance):
        self.binance = binance
        self.loader = Loader(binance)
        self.notifier = TelegramNotifier()

    def run(self):
        print("[SCAN] Start")
        symbols = get_filtered_symbols(self.binance)

        for symbol in symbols:
            try:
                bars_by_tf = self.loader.fetch_multiple_timeframes(symbol, TF_MAP)

                if is_low_liquidity(bars_by_tf['1d']):
                    continue
                if is_news_spike(bars_by_tf['1h']):
                    continue
                if is_in_dead_hours(datetime.utcnow().hour):
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

            except Exception as e:
                print(f"[ERROR] {symbol}: {e}")

        print("[SCAN] Done")