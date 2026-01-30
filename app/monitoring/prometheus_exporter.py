"""Prometheus exporter for OMNIS metrics."""
from __future__ import annotations

from typing import Dict

from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from app.config.settings import METRICS_PORT
from app.utils.metrics import (
    global_metrics,
    collector_health,
    resolver_health,
    playbook_health,
)
from app.utils.omnis_logger import logger


class _OmnisCollector:
    """Bridge the internal metrics registry to Prometheus."""

    def collect(self):  # pragma: no cover - Prometheus interface
        snapshot = global_metrics.snapshot()
        counters: Dict[str, float] = snapshot.get("counters", {})
        gauges: Dict[str, float] = snapshot.get("gauges", {})

        for name, value in counters.items():
            metric = CounterMetricFamily(
                f"omnis_{name}_total",
                "OMNIS counter exported via custom registry",
                value=value,
            )
            yield metric

        for name, value in gauges.items():
            metric = GaugeMetricFamily(
                f"omnis_{name}_gauge",
                "OMNIS gauge exported via custom registry",
                value=value,
            )
            yield metric

        health_map = {
            "collector": collector_health,
            "resolver": resolver_health,
            "playbook": playbook_health,
        }
        status_metric = GaugeMetricFamily(
            "omnis_health_status",
            "Component health status (1=OK, 0=ERROR)",
            labels=["component"],
        )
        last_success_metric = GaugeMetricFamily(
            "omnis_health_last_success_timestamp",
            "Unix timestamp of the last healthy execution per component",
            labels=["component"],
        )
        for component, monitor in health_map.items():
            status_metric.add_metric([component], 1 if monitor.status == "OK" else 0)
            last_success_metric.add_metric([component], monitor.last_success_ts)
        yield status_metric
        yield last_success_metric


_exporter_started = False


def start_prometheus_exporter(port: int | None = None):
    """Start the Prometheus HTTP exporter once per process."""
    global _exporter_started
    if _exporter_started:
        return

    resolved_port = port or METRICS_PORT
    try:
        start_http_server(resolved_port, addr="0.0.0.0")
        REGISTRY.register(_OmnisCollector())
        _exporter_started = True
        logger.info("Prometheus exporter listening on %s", resolved_port)
    except OSError as exc:
        logger.warning(
            "Prometheus exporter could not bind to %s (perhaps already running?): %s",
            resolved_port,
            exc,
        )
