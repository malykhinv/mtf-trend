# main.py
import traceback, time, queue
from datetime import datetime, timedelta
from config import constants
from config.constants import TIMEZONE
from runner.scanner import Scanner
from utils.logger import log
from data.market_radar import MarketRadar

if __name__ == "__main__":
    scanner = Scanner()

    # печень символов для радара — как у вас, но один раз
    universe = set(scanner.loader.get_filtered_symbols())
    radar = MarketRadar(watchlist=universe)
    radar.start()

    tfss = [
        constants.MTF_PROFILE_MACRO_5,
        constants.MTF_PROFILE_MACRO_3,
        constants.MTF_PROFILE_MACRO_1,
    ]

    last_full_sweep: datetime | None = None
    FULL_SWEEP = timedelta(seconds=0)  # 0 => отключено

    while True:
        try:
            # 1) Обрабатываем события радара
            try:
                event = radar.events.get(timeout=1.0)  # может быть пусто — это нормально
            except queue.Empty:
                event = None

            if event is not None:
                scanner.process_radar_event(event, tfss)

            # 2) Периодический полный проход (надёжность/страховка)
            now = datetime.now(tz=TIMEZONE)
            if FULL_SWEEP.total_seconds() > 0 and (last_full_sweep is None or now - last_full_sweep >= FULL_SWEEP):
                scanner.run(tfss)
                last_full_sweep = now

        except Exception as error:
            log(f"Ошибка во внешнем цикле: {error}\n{traceback.format_exc()}")
            time.sleep(0.5)  # не падаем из‑за редких ошибок, чуть притормаживаем и продолжаем
