from domain.models.enums import Profile
from domain.models.state import GlobalState
from config import make_profile_config_balanced
from domain.services import RestClient, WsClient, Trader, SymbolRegistry


def main() -> None:
    # 1) load profile
    cfg = make_profile_config_balanced()
    gstate = GlobalState(profile=Profile.BALANCED, btc_pause_until_ms=None)

    # 2) build universe and subscriptions
    registry = SymbolRegistry()
    rest = RestClient()
    ws = WsClient()
    trader = Trader()

    # 3) coroutines: ws_stream, bar_maker, rest_pollers, fsm_loop
    #    - strict separation: input→metrics→signals→trading
    #    - all parameters are taken only from cfg and constants
    _ = (cfg, gstate, registry, rest, ws, trader)


if __name__ == "__main__":
    main()
