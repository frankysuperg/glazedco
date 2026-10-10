"""Anomalías estadísticas: una alerta no demuestra tendencia ni causalidad."""

from .config import AnomalyConfig, load_config
from .detect import detect_anomalies

__all__ = ["AnomalyConfig", "load_config", "detect_anomalies"]
