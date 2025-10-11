from dataclasses import dataclass


@dataclass(frozen=True)
class MetricsSettings:
    enabled: bool = True
    prometheus_port: int = 9100
    daily_report_hour_utc: int = 0
    resubscribe_ratio_threshold: float = 0.35
    resubscribe_window_minutes: int = 5
    silence_timeout_minutes: int = 5
    backpressure_ratio_threshold: float = 0.7
    emit_json_logs: bool = False

__all__ = ["MetricsSettings"]
