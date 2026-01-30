from __future__ import annotations

import threading
import time
from typing import Optional

from app.utils.omnis_logger import logger


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}
        self._gauges = {}

    def inc(self, name: str, amount: float = 1.0):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float):
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }


global_metrics = MetricsRegistry()


class HealthMonitor:
    def __init__(self):
        self.status = "OK"
        self.last_error: Optional[str] = None
        self.last_success_ts: float = time.time()

    def mark_success(self):
        self.status = "OK"
        self.last_success_ts = time.time()
        self.last_error = None

    def mark_error(self, message: str):
        self.status = "ERROR"
        self.last_error = message
        logger.error("HealthMonitor | %s", message)

    def to_dict(self):
        return {
            "status": self.status,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
        }


collector_health = HealthMonitor()
resolver_health = HealthMonitor()
playbook_health = HealthMonitor()
