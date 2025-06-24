from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings.constants import UPDATE_INTERVAL_MINUTES
from data.binance_client import get_binance_client
from runner.scanner import Scanner

class Scheduler:
    def __init__(self):
        self.binance = get_binance_client()
        self.scanner = Scanner(self.binance)
        self.scheduler = BlockingScheduler(timezone="UTC")
        self.interval_minutes = UPDATE_INTERVAL_MINUTES

    def start(self):
        self.scheduler.add_job(lambda: self.scanner.run(), "cron", minute=f"*/{self.interval_minutes}", id="scanner")
        print(f"Scheduler started, scanning every {self.interval_minutes} minutes")
        self.scheduler.start()
