import traceback

from config import constants
from runner.scanner import Scanner
from utils.logger import log

if __name__ == "__main__":
    scanner = Scanner()

    while True:
        try:
            tfss = [
                constants.MTF_PROFILE_5_3,
                constants.MTF_PROFILE_5_1,
                constants.MTF_PROFILE_3_3,
                constants.MTF_PROFILE_3_1,
                constants.MTF_PROFILE_1_1,
            ]
            scanner.run(tfss)
        except Exception as error:
            log(f"Ошибка во внешнем цикле: {error}\n{traceback.format_exc()}")
            break
