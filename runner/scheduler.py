from apscheduler.schedulers.blocking import BlockingScheduler
from config.settings.constants import UPDATE_INTERVAL_MINUTES
from data.binance_client import get_binance_client
from runner.scanner import Scanner
from utils.logger import log
import threading


class Scheduler:
    def __init__(self):
        self.binance = get_binance_client()
        self.scanner = Scanner(self.binance)
        self.scheduler = BlockingScheduler(timezone="UTC")
        self.interval_minutes = UPDATE_INTERVAL_MINUTES
        self._lock = threading.Lock()

    def _safe_run_scan(self):
        if self._lock.locked():
            log("Предыдущий цикл сканирования ещё не завершён. Пропускаем запуск.")
            return

        with self._lock:
            self.scanner.run()

    def start(self):
        log(f"Планировщик запущен. Сканирование каждые {self.interval_minutes} минут.")
        self.scheduler.add_job(self._safe_run_scan, "cron", minute=f"*/{self.interval_minutes}", id="scanner")
        self.scheduler.start()
