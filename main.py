import asyncio
import traceback

from config.constants import MTF_PROFILE_5_3, MTF_PROFILE_3_3, MTF_PROFILE_3_1, MTF_PROFILE_1_1, MTF_PROFILE_5_1
from runner.scanner import Scanner
from utils.logger import log

scanner = Scanner()

async def main():
    while True:
        try:
            await scanner.run([
                MTF_PROFILE_5_3, MTF_PROFILE_5_1,
                MTF_PROFILE_3_3, MTF_PROFILE_3_1, MTF_PROFILE_1_1
            ])
        except Exception as error:
            log(f"Ошибка во внешнем цикле: {error}\n{traceback.format_exc()}")
            break

if __name__ == "__main__":
    asyncio.run(main())
