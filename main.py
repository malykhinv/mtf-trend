from runner.scanner import Scanner
from utils.logger import log

if __name__ == "__main__":
    scanner = Scanner()

    while True:
        try:
            scanner.run()
        except Exception as error:
            log(f"Ошибка во внешнем цикле: {error}")
