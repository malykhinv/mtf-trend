import traceback
from typing import Set

from config.constants import MTF_PROFILE_GLOBAL, MTF_PROFILE_INTRADAY
from runner.scanner import Scanner
from utils.logger import log

if __name__ == "__main__":
    scanner = Scanner()

    while True:
        try:
            scanner.run([MTF_PROFILE_GLOBAL, MTF_PROFILE_INTRADAY])
        except Exception as error:
            log(f"Ошибка во внешнем цикле: {error}\n{traceback.format_exc()}")
            break
