import signal
import sys
from concurrent.futures import ThreadPoolExecutor

from crypto_screener.utils.logger import log


def handle_sig(executor: ThreadPoolExecutor) -> None:
    # noinspection PyUnusedLocal,PyShadowingNames
    def shutdown(sig: int, frame) -> None:  # type: ignore[override]
        log.i("Получен сигнал %s, завершаем работу", sig)
        executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
