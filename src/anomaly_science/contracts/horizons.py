from __future__ import annotations

SUPPORTED_RESEARCH_HORIZONS: tuple[int, ...] = (15, 30, 60, 120, 180)
FUTURE_PATH_DIAGNOSTIC_HORIZONS: tuple[int, ...] = (5,)
FUTURE_PATH_RETURN_HORIZONS: tuple[int, ...] = (*FUTURE_PATH_DIAGNOSTIC_HORIZONS, *SUPPORTED_RESEARCH_HORIZONS)


def supported_research_horizon_error_message(field_name: str = "horizon_minutes") -> str:
    allowed = ", ".join(str(horizon) for horizon in SUPPORTED_RESEARCH_HORIZONS)
    return f"{field_name} must be one of {allowed}"


def is_supported_research_horizon(horizon_minutes: object) -> bool:
    return type(horizon_minutes) is int and horizon_minutes in SUPPORTED_RESEARCH_HORIZONS


def validate_supported_research_horizon(
    horizon_minutes: int,
    *,
    field_name: str = "horizon_minutes",
) -> None:
    if not is_supported_research_horizon(horizon_minutes):
        raise ValueError(supported_research_horizon_error_message(field_name))


def validate_supported_research_horizons(
    horizons_minutes: tuple[int, ...],
    *,
    field_name: str = "horizons_minutes",
) -> None:
    if type(horizons_minutes) is not tuple or not horizons_minutes:
        raise ValueError(f"{field_name} must be a non-empty tuple")
    seen: set[int] = set()
    for horizon_minutes in horizons_minutes:
        validate_supported_research_horizon(horizon_minutes, field_name=field_name)
        if horizon_minutes in seen:
            raise ValueError(f"{field_name} must not contain duplicate horizons")
        seen.add(horizon_minutes)

def research_horizon_label_column(horizon_minutes: int) -> str:
    validate_supported_research_horizon(horizon_minutes)
    return f"scenario_{horizon_minutes}m"


def research_horizon_label_available_column(horizon_minutes: int) -> str:
    validate_supported_research_horizon(horizon_minutes)
    return f"label_available_{horizon_minutes}m"
