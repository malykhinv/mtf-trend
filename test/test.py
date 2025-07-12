# test/test.py

from datetime import datetime
from zoneinfo import ZoneInfo

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
    MTF_PROFILE_5_1
)

def main():
    symbol = "STOUSDT"
    year = 2025
    month = 7
    day = 10
    hour = 6
    minute = 45
    tz = ZoneInfo("Europe/Belgrade")
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
        bars_by_tf = loader.fetch_ohlcv_by_tfs(symbol, tfs, limit=250, to_time=target_time)
        setup_bars = bars_by_tf[tfs.setup]

        # Строим график
        plot = Plot(symbol=symbol, bars=setup_bars)
        plot.plot_main()

        setup_found = False

        for confidence in [Confidence.STRONG, Confidence.MODERATE, Confidence.WEAK]:
            signal = detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf, confidence=confidence)
            if signal:
                log(f"✅ Setup найден! Тип: {signal.confidence.name}")

                plot.mark_pump_start(signal.timestamp)

                if confidence in [Confidence.MODERATE, Confidence.STRONG]:
                    trendline = signal.trendline
                    if trendline:
                        plot.draw_trendline(trendline, trendline.point1_index, trendline.point2_index)

                if confidence == Confidence.STRONG:
                    plot.mark_breakout(len(setup_bars) - 1)

                setup_found = True
                break

        if not setup_found:
            logw("❌ Setup не найден.")

        # Сохраняем график
        filename = f"{symbol}_{profile_name}_plot.png"
        plot.save(filename)

if __name__ == "__main__":
    main()
