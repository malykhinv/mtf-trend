from domain.models.trading import OrderSpec, PositionPlan
from domain.models.state import SymbolState
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent
from domain.models import metrics as M, trading as T, signals as S


class Trader:
    def place(self, order: OrderSpec) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def cancel(self, symbol: str, all_for_symbol: bool) -> None:  # pragma: no cover
        raise NotImplementedError


class SymbolRegistry:
    def get(self, symbol: str) -> SymbolState:  # pragma: no cover
        raise NotImplementedError

    def put(self, state: SymbolState) -> None:  # pragma: no cover
        raise NotImplementedError

    def update(self, symbol: str, new_state: SymbolState) -> None:  # pragma: no cover
        raise NotImplementedError

    def all_symbols(self) -> tuple[str, ...]:  # pragma: no cover
        raise NotImplementedError


class RestClient:
    def get_open_interest(self, symbol: str) -> float:  # pragma: no cover
        raise NotImplementedError

    def get_taker_ratio(self, symbol: str) -> tuple[float, float]:  # pragma: no cover
        raise NotImplementedError

    def get_premium_pct(self, symbol: str) -> float:  # pragma: no cover
        raise NotImplementedError

    def get_24h_stats(self, symbol: str) -> tuple[float, float]:  # pragma: no cover
        raise NotImplementedError


class WsClient:
    async def stream(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:  # pragma: no cover
        raise NotImplementedError

    def next_agg_trade(self) -> AggTrade | None:  # pragma: no cover
        raise NotImplementedError

    def next_depth(self) -> DepthSnapshot | None:  # pragma: no cover
        raise NotImplementedError

    def next_liquidation(self) -> LiquidationEvent | None:  # pragma: no cover
        raise NotImplementedError


class SignalEngine:
    def on_minute_close(self, symbol: str) -> S.PumpSignal | None:  # pragma: no cover
        raise NotImplementedError

    def confirm_failure(self, symbol: str, window: M.PumpWindow) -> bool:  # pragma: no cover
        raise NotImplementedError

    def make_entry(self, symbol: str, window: M.PumpWindow) -> S.EntrySignal | None:  # pragma: no cover
        raise NotImplementedError


class RiskManager:
    def build_plan(self, symbol: str, entry_price: float, window: M.PumpWindow) -> T.PositionPlan | None:  # pragma: no cover
        raise NotImplementedError

    def allow_trade(self, plan: T.PositionPlan) -> bool:  # pragma: no cover
        raise NotImplementedError


class TradeManager:
    def open_position(self, plan: T.PositionPlan) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_tick_manage(self, symbol: str) -> tuple[list[S.ExitSignal], T.PositionPlan | None]:  # pragma: no cover
        raise NotImplementedError
