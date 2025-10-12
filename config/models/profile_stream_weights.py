from dataclasses import dataclass

from .profile_weights import ProfileWeights


@dataclass(frozen=True)
class ProfileStreamWeights:
    depth: ProfileWeights
    trades: ProfileWeights
    book_ticker: ProfileWeights


__all__ = ["ProfileStreamWeights"]
