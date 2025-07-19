# test/test.py

from datetime import datetime

from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.models.confidence import Confidence
from utils.plot import Plot
from utils.logger import log, logw

# Импортируем твои MTF-профили
from config.constants import (
    MTF_PROFILE_1_1,
    MTF_PROFILE_3_1,
    MTF_PROFILE_3_3,
    MTF_PROFILE_5_3,
    MTF_PROFILE_5_1,
    BELGRADE_TZ
)

def main():
    symbol = "ACXUSDT"
    year = 2025
    month = 7
    day = 19
    hour = 9
    minute = 20
    tz = BELGRADE_TZ
    target_time = datetime(year, month, day, hour, minute, tzinfo=tz)

    mtf_profiles = [
        # MTF_PROFILE_1_1,
        # MTF_PROFILE_3_1,
        # MTF_PROFILE_3_3,
        # MTF_PROFILE_5_3,
        MTF_PROFILE_5_1,
    ]

    loader = Loader()
    detector = SetupDetector()

    for tfs in mtf_profiles:
        profile_name = f"{tfs.macro.value}-{tfs.setup.value}-{tfs.entry.value}"
        print()
        log(f"Проверка {profile_name}")

        # Загружаем бары по профилю
        bars_by_tf = loader.fetch_ohlcvi_by_tfs(symbol, tfs, limit=200, to_time=target_time)
        setup_bars = bars_by_tf[tfs.setup]

        # Строим график
        plot = Plot(symbol=symbol, bars=setup_bars, tf=tfs.setup)
        plot.plot_main()

        setup_found = False

        for confidence in [Confidence.STRONG, Confidence.MODERATE, Confidence.WEAK]:
            signal = detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
            if signal:
                log(f"✅ Setup найден! Тип: {signal.confidence.name}")

                plot.mark_pump_start(signal.timestamp)

                if confidence in [Confidence.MODERATE, Confidence.STRONG]:
                    trendline = signal.trendline
                    if trendline:
                        plot.draw_trendline(trendline)

                setup_found = True
                break

        if not setup_found:
            logw("❌ Setup не найден.")

        # Сохраняем график
        filename = f"{symbol}_{profile_name}_plot.png"
        plot.save(filename)

if __name__ == "__main__":
    main()
