"""Relationship layer utilities for OMNIS graph."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from neo4j import GraphDatabase

from app.utils.omnis_logger import logger


class RelationshipEngine:
    """Centraliza a criação de relacionamentos entre Playbooks, Casos, Entidades e Sinais."""

    def __init__(
        self,
        driver: GraphDatabase.driver,
        signal_window_hours: int = 168,
        signal_limit: int = 50,
    ):
        self.driver = driver
        self.signal_window_hours = signal_window_hours
        self.signal_limit = signal_limit

    def sync_case_links(
        self,
        *,
        case_id: str,
        playbook_id: str,
        playbook_description: str,
        playbook_tags: Sequence[str],
        entity_id: str,
    ) -> None:
        """Garante que casos estejam ligados aos playbooks e sinais/temas relevantes."""

        tags = list(playbook_tags or [])
        with self.driver.session() as session:
            session.run(
                """
                MERGE (p:Playbook {id: $playbook_id})
                  ON CREATE SET p.description = $description,
                                p.created_at = datetime(),
                                p.tags = $tags
                SET p.updated_at = datetime(),
                    p.description = coalesce($description, p.description),
                    p.tags = CASE WHEN size($tags) > 0 THEN $tags ELSE p.tags END

                MATCH (c:Case {case_id: $case_id})
                MERGE (p)-[:GENERATED]->(c)
                """,
                {
                    "playbook_id": playbook_id,
                    "description": playbook_description,
                    "tags": tags,
                    "case_id": case_id,
                },
            )

            session.run(
                """
                MATCH (c:Case {case_id: $case_id})
                MATCH (e:Entity {entity_id: $entity_id})
                MATCH (s:Signal)-[:REFERS_TO]->(e)
                WHERE s.timestamp > datetime() - duration({hours: $hours})
                WITH c, s
                ORDER BY s.timestamp DESC
                LIMIT $limit
                MERGE (c)-[:LINKS_SIGNAL]->(s)
                """,
                {
                    "case_id": case_id,
                    "entity_id": entity_id,
                    "hours": self.signal_window_hours,
                    "limit": self.signal_limit,
                },
            )

            session.run(
                """
                MATCH (c:Case {case_id: $case_id})
                MATCH (e:Entity {entity_id: $entity_id})
                MATCH (e)<-[:REFERS_TO]-(s:Signal)-[:MATCHES_THEME]->(t:Theme)
                WHERE s.timestamp > datetime() - duration({hours: $hours})
                WITH c, t, count(*) AS volume
                MERGE (c)-[rel:TAGGED_THEME]->(t)
                SET rel.volume = volume,
                    rel.last_update = datetime()
                """,
                {
                    "case_id": case_id,
                    "entity_id": entity_id,
                    "hours": self.signal_window_hours,
                },
            )

        logger.debug(
            "RelationshipEngine | Case %s linked to playbook %s and related signals/themes",
            case_id,
            playbook_id,
        )
