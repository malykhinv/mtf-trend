from dataclasses import dataclass


@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    api_secret: str


BINANCE = ApiCredentials(api_key="REPLACE_ME", api_secret="REPLACE_ME")
