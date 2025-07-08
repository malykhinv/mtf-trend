from data.loader import Loader
from domain.detection.phase_resolver import PhaseResolver
from domain.mtf_analyzer import MTFAnalyzer
from domain.detection.setup_detector import SetupDetector
from domain.models.confidence import Confidence
from config.constants import MTF_PROFILE_GLOBAL


def main():
    symbol = "1INCH/USDT"
    tfs = MTF_PROFILE_GLOBAL

    loader = Loader()
    bars_by_tf = loader.fetch_multiple_timeframes(symbol, tfs)

    atr_by_tf = {tf: sum(abs(b.high - b.low) for b in bars) / len(bars) for tf, bars in bars_by_tf.items()}

    resolver = PhaseResolver(tfs, bars_by_tf, atr_by_tf)
    mtf_states = resolver.resolve()

    swings = mtf_states[tfs.trend].structure

    setup_detector = SetupDetector(
        symbol=symbol,
        tfs=tfs,
        bars_by_tf=bars_by_tf,
        atr_by_tf=atr_by_tf,
        swings=swings,
        mtf_states=mtf_states,
        confidence=Confidence.STRONG
    )

    signal = setup_detector.detect()

    if signal:
        print(f"✅ Сетап найден: {signal.scenario.value}, side: {signal.side.value}, entry: {signal.entry}, SL: {signal.sl}, TP: {signal.tp}, RR: {signal.rr}")
    else:
        print("❌ Сетап не подтверждён.")


if __name__ == "__main__":
    main()
