from data.loader import Loader
from config.constants import MTF_PROFILE_5_1


def main():
    symbol = "1INCH/USDT"
    tfs = MTF_PROFILE_5_1

    loader = Loader()
    bars_by_tf = loader.fetch_ohlcv_by_tfs(symbol, tfs)



if __name__ == "__main__":
    main()
