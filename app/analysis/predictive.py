from __future__ import annotations

from neo4j import GraphDatabase

from app.config.settings import (
    CONFIG,
    NEO4J_URI,
    NEO4J_USER,
    NEO4J_PASSWORD,
)
from app.utils.omnis_logger import logger


class PredictiveAnalyzer:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def _count_events(
        self,
        session,
        *,
        hours: int,
        min_severity: float,
        offset_hours: int = 0,
    ) -> int:
        if offset_hours <= 0:
            result = session.run(
                """
                MATCH (sig:Signal)
                WHERE sig.timestamp > datetime() - duration({hours: $hours})
                  AND sig.severity >= $min_severity
                RETURN count(sig) AS c
                """,
                hours=hours,
                min_severity=min_severity,
            ).single()
        else:
            result = session.run(
                """
                MATCH (sig:Signal)
                WHERE sig.timestamp <= datetime() - duration({hours: $offset_start})
                  AND sig.timestamp > datetime() - duration({hours: $offset_end})
                  AND sig.severity >= $min_severity
                RETURN count(sig) AS c
                """,
                offset_start=offset_hours,
                offset_end=offset_hours + hours,
                min_severity=min_severity,
            ).single()
        return int(result["c"])

    def calculate_probability(self, theme_id: str) -> float:
        themes = CONFIG.get("rules", {}).get("themes", [])
        theme = next((t for t in themes if t.get("id") == theme_id), None)
        if not theme:
            return 0.0

        hours = int(theme.get("time_window_hours", 72))
        min_severity = float(theme.get("min_severity", 0.5))

        with self.driver.session() as session:
            recent_events = self._count_events(
                session, hours=hours, min_severity=min_severity
            )
            historical_events = self._count_events(
                session,
                hours=hours,
                min_severity=min_severity,
                offset_hours=hours,
            )

        historical_avg = max(1, historical_events)
        prob = min(1.0, recent_events / historical_avg)
        logger.info(
            "PredictiveAnalyzer | Theme %s | recent=%d | hist=%d | prob=%.2f",
            theme_id,
            recent_events,
            historical_events,
            prob,
        )
        return prob

