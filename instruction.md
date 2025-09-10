# README — Шорт-бот «несостоявшийся памп» для Binance UM Perp

## Цель
Детерминированная спецификация бота (Python, строгая типизация, без `getattr/hasattr`, без «свободных словарей» во внешних сигнатурах). Все значения — из `constants.py` и профиль-конфига. Бот отслеживает все символы `*USDT`, детектит аномальный **лонг-импульс**, проверяет отсутствие толпы, шортит, ведёт позицию (TP1/TP2/трейлинг), выходит при «возврате лонгов».

---

## Структура проекта
```
domain/
  models/
    enums.py
    config.py
    market_data.py
    metrics.py
    signals.py
    trading.py
    state.py
config/
  credentials.py
constants.py
main.py
data/
  runtime/
  logs/
  cache/
```

---

## Зависимости (фиксированные)
- stdlib: `asyncio`, `dataclasses`, `enum`, `typing`, `statistics`, `math`, `time`, `sqlite3`, `json`, `logging`, `collections`, `contextlib`.
- external: `httpx==0.27.*`, `websockets==12.*`, `pydantic==2.*`.

---

## Константы (обязательно в `constants.py`)
```python
from typing import Final

BINANCE_FAPI_WS: Final[str] = "wss://fstream.binance.com/stream"
BINANCE_FAPI_REST: Final[str] = "https://fapi.binance.com"

HTTP_TIMEOUT_CONNECT_SEC: Final[float] = 5.0
HTTP_TIMEOUT_READ_SEC: Final[float] = 15.0
HTTP_TIMEOUT_WRITE_SEC: Final[float] = 5.0
HTTP_TIMEOUT_POOL_SEC: Final[float] = 5.0

UNIVERSE_MIN_24H_USDT: Final[float] = 20_000_000.0
UNIVERSE_MAX_SPREAD_BPS: Final[float] = 4.0
UNIVERSE_MIN_TOP10_BID_USDT: Final[float] = 80_000.0

EWMA_HALF_LIFE_MIN: Final[int] = 45
Z_BASE_WINDOW_MIN: Final[int] = 60

REST_POLL_SEC_OI: Final[int] = 20
REST_POLL_SEC_TAKER: Final[int] = 20
REST_POLL_SEC_PREMIUM: Final[int] = 20

COOLDOWN_AFTER_TRADE_SEC: Final[int] = 900
GLOBAL_BTC_PAUSE_Z: Final[float] = 3.0
GLOBAL_BTC_PAUSE_SEC: Final[int] = 180

RISK_PER_TRADE_USDT: Final[float] = 100.0  # настрой под депозит
```

`HTTP_TIMEOUT_*` параметры задают таймауты REST‑клиента (connect/read/write/pool, сек).

---

## Профили и параметры (цель — высокий winrate при нормальной частоте)

### Термины
- `σ₁м` — EWMA std 1-мин лог-доходностей (half-life = 45 мин).
- `z_px`, `z_vol` — z-score 1м хода и объёма относительно 60 последних 1м баров.
- `close_pos` — (Close−Low)/(High−Low) импульсной 1м-свечи.
- `LiqS z` — z-score суммы ликвидаций шортов за 0–60с vs 60 мин.
- `ask_imb` — ask/(ask+bid) по top-10 L2; `top5ask_vs_base` — топ-5 ask vs медианы до импульса.
- `TBRS` — taker buy/sell (5м при наличии; иначе 1м).
- `premium` — премиум-индекс в % (≈ перегрев лонгов).
- `AVWAP_impulse` — anchored VWAP от начала импульса.

### Таблица порогов
| Профиль | Триггер «аномальный лонг» (1м) | Усилители | Подтверждение (60–180с, нужно ≥3, из них ≥1 из LOB/CVD/Latency) | Вход | SL | TP/Ведение |
|---|---|---|---|---|---|---|
| **CONSERVATIVE** | `z_px≥3.5 ∧ z_vol≥3.8 ∧ Δp≥max(2.8·σ₁м,1.0%) ∧ close_pos≥0.75` | `LiqS z≥2.5` обязат. | `ΔOI≤+0.2%`; `TBRS≤1.03`; `premium≤0`; **Latency** `≥120с`; **LOB** `ask_imb≥0.74` или `top5ask≥2.5×`; **CVDdiv** `gap≥0.05% ∧ ≥90с` | Пробой лоу консолидации **и** потеря AVWAP | `High+max(0.22%,3.0·σ₁м)` | TP1=AVWAP/50% (50%), TP2=61.8%/pre-imp (30%), хвост 20% трейлинг (High 3×1м + `max(0.12%,1.3·σ₁м)`) |
| **BALANCED** | `z_px≥3.2 ∧ z_vol≥3.3 ∧ Δp≥max(2.6·σ₁м,0.9%) ∧ close_pos≥0.70` | `LiqS z≥2.0` желателен | `ΔOI≤+0.3%`; `TBRS≤1.05`; `premium≤0`; **Latency** `≥90с`; **LOB** `ask_imb≥0.72` или `top5ask≥2.2×`; **CVDdiv** `gap≥0.05% ∧ ≥60–90с` | Пробой лоу **или** потеря AVWAP | `High+max(0.20%,2.8·σ₁м)` | TP1 50%; TP2 30%; хвост 20–25% трейлинг (High 3×1м + `max(0.12%,1.25·σ₁м)`) |
| **ACTIVE** | `z_px≥2.9 ∧ z_vol≥3.0 ∧ Δp≥max(2.3·σ₁м,0.8%) ∧ close_pos≥0.65` | `LiqS z≥1.8` учитыв. | `ΔOI≤+0.5%`; `TBRS≤1.08`; `premium≤+0.01%`; **Latency** `≥60с`; **LOB** `ask_imb≥0.70` или `top5ask≥2.0×`; **CVDdiv** `≥60с` | Пробой лоу **или** потеря AVWAP | `High+max(0.18%,2.5·σ₁м)` | TP1 40–50%; TP2 25–30%; хвост 20–35% трейлинг (High 3×1м + `max(0.10%,1.1·σ₁м)`) |

---

## Енамы (`domain/models/enums.py`)
```python
from enum import Enum, StrEnum, auto

class Profile(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    ACTIVE = "ACTIVE"

class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

class CandleInterval(StrEnum):
    M1 = "1m"

class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

class BotState(Enum):
    IDLE = auto()
    WATCHING = auto()
    CONFIRMING = auto()
    ENTERED = auto()
    EXITING = auto()
    COOLDOWN = auto()

class ExitReason(StrEnum):
    TP1 = "TP1"
    TP2 = "TP2"
    TRAIL = "TRAIL"
    LONGS_RETURNED = "LONGS_RETURNED"
    INVALIDATED = "INVALIDATED"
    STOP = "STOP"

class LiquidationSide(StrEnum):
    SHORT = "SHORT"
    LONG = "LONG"

class ImbalanceSide(StrEnum):
    ASK = "ASK"
    BID = "BID"

class SymbolTier(StrEnum):
    TOP = "TOP"
    MID = "MID"
    LOW = "LOW"
```
---

## Конфиги профиля (`domain/models/config.py`)
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PumpDetectorParams:
    z_px: float
    z_vol: float
    sigma_mult_min: float
    abs_move_min_pct: float
    close_pos_min: float
    liq_z_required: bool
    liq_z_min: float

@dataclass(frozen=True)
class ConfirmationParams:
    d_oi_max_pct: float
    tbrs_max: float
    premium_max_pct: float
    no_new_high_sec: int
    ask_imb_min: float
    top5ask_mult_min: float
    cvd_price_gap_min_pct: float
    cvd_div_min_sec: int

@dataclass(frozen=True)
class RiskParams:
    sl_abs_min_pct: float
    sl_sigma_mult: float
    entry_requires_avwap_break: bool
    min_reward_r1: float
    cost_mult_min: float

@dataclass(frozen=True)
class ManagementParams:
    tp1_share: float
    tp2_share: float
    tail_share: float
    trail_lookback_candles: int
    trail_gap_abs_min_pct: float
    trail_gap_sigma_mult: float

@dataclass(frozen=True)
class UniverseParams:
    min_24h_usdt: float
    max_spread_bps: float
    min_top10_bid_usdt: float

@dataclass(frozen=True)
class ProfileConfig:
    pump: PumpDetectorParams
    confirm: ConfirmationParams
    risk: RiskParams
    mgmt: ManagementParams
    universe: UniverseParams
```
### Фабрика BALANCED
```python
from .config import *
def make_profile_config_balanced() -> ProfileConfig:
    return ProfileConfig(
        pump=PumpDetectorParams(3.2,3.3,2.6,0.9,0.70,False,2.0),
        confirm=ConfirmationParams(0.3,1.05,0.0,90,0.72,2.2,0.05,60),
        risk=RiskParams(0.20,2.8,False,1.5,5.0),
        mgmt=ManagementParams(0.50,0.30,0.20,3,0.12,1.25),
        universe=UniverseParams(20_000_000.0,4.0,80_000.0)
    )
```
(Сделай аналогичные `make_profile_config_conservative()` и `make_profile_config_active()` строго по таблице.)

---

## Рыночные датаклассы (`domain/models/market_data.py`)
```python
from dataclasses import dataclass
from .enums import LiquidationSide

@dataclass(frozen=True)
class AggTrade:
    ts_ms: int
    price: float
    qty: float
    is_buyer_maker: bool  # True => агрессор SELL

@dataclass(frozen=True)
class DepthLevel:
    price: float
    qty: float

@dataclass(frozen=True)
class DepthSnapshot:
    ts_ms: int
    bids: tuple[DepthLevel, ...]  # сорт. по убыванию цены
    asks: tuple[DepthLevel, ...]  # сорт. по возрастанию

@dataclass(frozen=True)
class LiquidationEvent:
    ts_ms: int
    side: LiquidationSide
    price: float
    qty: float
    usdt_value: float

@dataclass(frozen=True)
class OIShot:
    ts_ms: int
    open_interest: float  # контрактная валюта

@dataclass(frozen=True)
class TakerShot:
    ts_ms: int
    taker_buy_volume: float
    taker_sell_volume: float

@dataclass(frozen=True)
class PremiumShot:
    ts_ms: int
    premium_pct: float
```
---

## Метрики/сигналы/трейдинг-план
`domain/models/metrics.py`
```python
from dataclasses import dataclass

@dataclass
class VolatilityState:
    sigma_1m: float
    vol1m_median: float

@dataclass
class PumpWindow:
    start_ts_ms: int
    high_price: float
    low_price: float
    close_pos: float

@dataclass
class LOBMetrics:
    ask_imbalance: float
    top5ask_vs_base: float

@dataclass
class FlowMetrics:
    d_oi_pct: float
    tbrs: float
    premium_pct: float
    liq_z: float

@dataclass
class CVDMetrics:
    cvd_buy_slope_pos: bool
    price_gap_pct: float
    div_sec: int
```
`domain/models/signals.py`
```python
from dataclasses import dataclass
from .enums import Side

@dataclass(frozen=True)
class PumpSignal:
    symbol: str
    ts_ms: int

@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    ts_ms: int
    side: Side
    entry_price: float

@dataclass(frozen=True)
class ExitSignal:
    symbol: str
    ts_ms: int
    reason: str
    exit_price: float
```
`domain/models/trading.py`
```python
from dataclasses import dataclass
from .enums import Side, OrderType

@dataclass(frozen=True)
class OrderSpec:
    symbol: str
    side: Side
    order_type: OrderType
    qty: float
    price: float | None

@dataclass
class PositionPlan:
    symbol: str
    side: Side
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tail_trailing: bool
    qty_total: float
    qty_tp1: float
    qty_tp2: float
    qty_tail: float

@dataclass
class PositionRuntime:
    plan: PositionPlan
    open_ts_ms: int
    remaining_qty: float
    be_price: float
```
`domain/models/state.py`
```python
from dataclasses import dataclass
from .enums import BotState, Profile, SymbolTier
from .metrics import VolatilityState, PumpWindow

@dataclass
class SymbolState:
    symbol: str
    tier: SymbolTier
    state: BotState
    vol: VolatilityState
    pump: PumpWindow | None
    last_trade_ts_ms: int | None
    cooldown_until_ms: int | None

@dataclass
class GlobalState:
    profile: Profile
    btc_pause_until_ms: int | None
```
---

## Эндпоинты и потоки (фиксировано)
- WS (combined): `@aggTrade`, `@depth@100ms`, `!forceOrder@arr`.
- REST (только по кандидат-символам в фазах WATCHING/ENTERED):
  - `/fapi/v1/openInterest`
  - `/futures/data/takerlongshortRatio`
  - `/fapi/v1/premiumIndex` (или экв. премия/фандинг)
- Интервалы REST: `REST_POLL_SEC_*` с джиттером ±20%. Повторные попытки с экспоненциальной паузой.

---

## Вычисления (жёстко)
- EWMA std: `α = 1 - exp(-ln(2)/half_life)`, обновление на каждом 1м баре.
- Z-score: `(x_t − mean_60) / std_60`, окно — 60 значений.
- `ask_imbalance = sum(ask_qty_top10) / (sum(ask_top10) + sum(bid_top10))`.
- `top5ask_vs_base = sum(ask_top5) / median(sum_ask_top5 за 60 мин до импульса)`.
- `TBRS = taker_buy / max(taker_sell, 1e-9)` (приоритет окну 5м).
- `ΔOI% = 100*(OI_now − OI_pre_impulse)/max(OI_pre_impulse,1e-9)`.
- CVD: `+qty` если `is_buyer_maker=False` (покупатель — агрессор), иначе `−qty`.
- `AVWAP_impulse` — объёмно-взвешенная цена с анкором на старт импульса (по `@aggTrade`).

---

## Машина состояний (на символ)
1. **IDLE** → при триггере детектора → **CONFIRMING** (фиксируем `PumpWindow`, открываем 180с окно).
2. **CONFIRMING** → если `score≥3` (из блока подтверждения) и выполнен входной триггер — **ENTERED** (создаём `PositionPlan`, размещаем ордера). Иначе отмена по таймауту или при новом хая.
3. **ENTERED** → управление TP/SL/трейлинг; досрочные выходы.
4. При полном закрытии → **COOLDOWN** `COOLDOWN_AFTER_TRADE_SEC` → **IDLE**.

---

## Алгоритм (строго пошагово)

### 1) Вселенная и фильтры
- Раз в 5 мин обновлять список `*USDT`:
  - `24h_quote_volume ≥ UNIVERSE_MIN_24H_USDT`
  - текущий спред ≤ `UNIVERSE_MAX_SPREAD_BPS` б.п.
  - сумма qty в top-10 bid ≥ `UNIVERSE_MIN_TOP10_BID_USDT`
- Не прошёл фильтр → исключить из подписки и состояний.

### 2) Подписки WS
- Combined stream для всех выбранных: `aggTrade`, `depth@100ms`, глобально `!forceOrder@arr`.
- Для `BTCUSDT`: считать `z_px(1м)`; если `z ≥ GLOBAL_BTC_PAUSE_Z` **вверх** → глобальная пауза шортов на `GLOBAL_BTC_PAUSE_SEC`.

### 3) Бар-агрегация 1м
- Собираем OHLCV, логи доходностей, медианы объёмов (60м), z-скоры.
- Сохраняем базу в SQLite: `metrics(symbol TEXT, ts_min INTEGER, ret REAL, vol REAL, trades REAL)`.

### 4) Детектор «аномальный лонг»
- На закрытии 1м бара: вычислить `Δp`, `z_px`, `z_vol`, `close_pos`.
- Сравнить с `ProfileConfig.pump`.
- TRUE → `SymbolState.state=CONFIRMING`, `pump=PumpWindow(...)`, старт окна 180с.

### 5) Подтверждение «толпа не пришла / flip не состоялся» (60–180с)
- Булевы признаки из `ProfileConfig.confirm`:
  - `ΔOI ≤ d_oi_max_pct`
  - `TBRS ≤ tbrs_max`
  - `premium ≤ premium_max_pct`
  - `no_new_high ≥ no_new_high_sec`
  - `ask_imbalance ≥ ask_imb_min` **или** `top5ask_vs_base ≥ top5ask_mult_min`
  - `CVDdiv`: `cvd_buy_slope_pos=True` **и** `price_gap_pct ≥ cvd_price_gap_min_pct` **и** `div_sec ≥ cvd_div_min_sec`
- Счётчик `score`. Требование: `score ≥ 3`, при этом **хотя бы один** из блока LOB/CVD/Latency.

### 6) Вход (SHORT)
- Триггер A: пробой **low** локальной консолидации (30–90с) по midprice.
- Триггер B: потеря `AVWAP_impulse`.
- Для профиля `CONSERVATIVE`: A **и** B; для `BALANCED/ACTIVE`: A **или** B.
- Исполнение:
  - 35% — `OrderType.MARKET` немедленно.
  - 65% — `OrderType.LIMIT` на ретест (mid + 0.03%), GTC.
- Если до входа сформирован **новый хай** — отмена сетапа.

### 7) SL, размер, TP
- `sl_price = High_impulse + max(risk.sl_abs_min_pct, risk.sl_sigma_mult * σ₁м)` в % от цены.
- Стоимость издержек `C = 2*спред% + 2*такер% + 0.5*спред%`.
- Условие допуска к сделке: `SL_distance ≥ risk.cost_mult_min * C` **и** ожидаемый `R(TP1) ≥ risk.min_reward_r1`.
- Объём: `qty = RISK_PER_TRADE_USDT / (SL_distance_% * entry_price)`.
- Цели:
  - `tp1_price = min(AVWAP_impulse, 50% retrace)`
  - `tp2_price = max(61.8% retrace, pre-impulse open)`
  - доли — из `ManagementParams`.

### 8) Ведение
- При исполнении TP1: перевести SL → `break-even + спред`.
- Трейлинг хвоста: каждые 1м:
  - `trail_price = max(high последних N баров) + max(trail_gap_abs_min_pct, trail_gap_sigma_mult*σ₁м)`
  - SL не ниже `trail_price`.

### 9) Досрочные выходы «лонги вернулись»
- Закрыть всё MARKET, если ИЛИ:
  1) `ΔOI ≥ +0.8%` за 120с **и** `TBRS ≥ 1.18`;
  2) `premium > 0` **и** новый хай за ≤60с;
  3) Ликвидации **лонгов** ускоряются две подряд 30с-подвыборки на падении (sign flip против позиции).

### 10) Кулдаун/антишум
- Кулдаун на символ: `COOLDOWN_AFTER_TRADE_SEC` после полной фиксации.
- Глобальная пауза по BTC — см. п.2.
- Повторный вход по тому же импульсу в окне 10 мин запрещён.

---

## Интерфейсы сервисов (строго)

### Trader
```python
from domain.models.trading import OrderSpec
class Trader:
    def place(self, order: OrderSpec) -> None: ...
    def cancel(self, symbol: str, all_for_symbol: bool) -> None: ...
```

### SymbolRegistry
```python
from domain.models.state import SymbolState
class SymbolRegistry:
    def get(self, symbol: str) -> SymbolState: ...
    def put(self, state: SymbolState) -> None: ...
    def update(self, symbol: str, new_state: SymbolState) -> None: ...
    def all_symbols(self) -> tuple[str, ...]: ...
```

### RestClient (строго фиксированные методы)
```python
class RestClient:
    def get_open_interest(self, symbol: str) -> float: ...
    def get_taker_ratio(self, symbol: str) -> tuple[float, float]: ...  # (buy, sell)
    def get_premium_pct(self, symbol: str) -> float: ...
    def get_24h_stats(self, symbol: str) -> tuple[float, float]: ...    # (quote_vol, last_price)
```

### WsClient
```python
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent
class WsClient:
    async def stream(self) -> None: ...
    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None: ...
    def next_agg_trade(self) -> AggTrade | None: ...
    def next_depth(self) -> DepthSnapshot | None: ...
    def next_liquidation(self) -> LiquidationEvent | None: ...
```

### SignalEngine / RiskManager / TradeManager
```python
from domain.models import metrics as M, trading as T, signals as S, state as ST

class SignalEngine:
    def on_minute_close(self, symbol: str) -> S.PumpSignal | None: ...
    def confirm_failure(self, symbol: str) -> bool: ...
    def make_entry(self, symbol: str, window: M.PumpWindow) -> S.EntrySignal | None: ...

class RiskManager:
    def build_plan(self, symbol: str, entry_price: float, window: M.PumpWindow) -> T.PositionPlan | None: ...
    def allow_trade(self, plan: T.PositionPlan) -> bool: ...

class TradeManager:
    def open_position(self, plan: T.PositionPlan, side: Side) -> None: ...
    def on_tick_manage(
        self, symbol: str, price: float | None = None
    ) -> tuple[list[S.ExitSignal], T.PositionPlan | None]: ...
```

---

## `main.py` — канонический поток
```python
def main() -> None:
    # 1) загрузка профиля
    cfg = make_profile_config_balanced()
    gstate = GlobalState(profile=Profile.BALANCED, btc_pause_until_ms=None)

    # 2) построение вселенной и подписки
    registry = SymbolRegistry()
    rest = RestClient()
    ws = WsClient()
    trader = Trader()

    # 3) корутины: ws_stream, bar_maker, rest_pollers, fsm_loop
    #   - строгое разделение: ввод→метрики→сигналы→торговля
    #   - все параметры берутся только из cfg и constants
```
---

## Логирование/персист
- `data/logs/` с ротацией 50 МБ, уровни: `INFO` — события, `DEBUG` — метрики/решения.
- `data/cache/baseline.sqlite` — окно 60 мин по символам для z-скоров/медиан.
- `data/runtime/` — PID, оффсеты, чекпоинты.

---

## Жёсткие запреты
- Никаких «магических» констант вне `constants.py`/профиля.
- Никаких динамических `dict` в сигнатурах/возвратах публичных методов.
- Никаких `getattr/hasattr`.
- Никаких тестов (по требованию заказчика).

---

## Конфиденшиалы (`config/credentials.py`)
```python
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    api_secret: str

api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
# при отсутствии переменных — RuntimeError
BINANCE = ApiCredentials(api_key=api_key, api_secret=api_secret)
```

Ключ и секрет читаются из переменных окружения `BINANCE_API_KEY` и `BINANCE_API_SECRET`.

---

## Итоговая логика (коротко)
```
PumpDetect(z_px,z_vol,Δp,close_pos,LiqS) ⇒
Confirm(ΔOI,TBRS,premium,Latency,LOB,CVD,score≥3) ⇒
Entry(пробой лоу/AVWAP) ⇒
Plan(SL=High+max(abs,σ), TP1/TP2/Trail, size по риску) ⇒
Manage(TP1→BE, трейлинг, выход при возврате лонгов) ⇒
Cooldown.
```
