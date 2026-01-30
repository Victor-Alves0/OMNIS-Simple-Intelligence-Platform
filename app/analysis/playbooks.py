from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

from app.utils.omnis_logger import logger
from app.analysis.relationship_engine import RelationshipEngine


@dataclass
class PlaybookRule:
    id: str
    description: str
    min_score: float = 0.0
    min_hotspot: float = 0.0
    min_mentions: int = 0
    actors: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class PlaybookEngine:
    def __init__(self, driver, config_path, relationship_engine: RelationshipEngine | None = None):
        self.driver = driver
        self.config_path = config_path
        self.relationships = relationship_engine or RelationshipEngine(driver)
        self.playbooks: List[PlaybookRule] = []
        self._load_config()

    def _load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            logger.warning("Playbook config not found: %s", self.config_path)
            data = {}
        for entry in data.get("playbooks", []):
            self.playbooks.append(
                PlaybookRule(
                    id=entry.get("id", str(uuid.uuid4())),
                    description=entry.get("description", ""),
                    min_score=float(entry.get("min_score", 0.0)),
                    min_hotspot=float(entry.get("min_hotspot", 0.0)),
                    min_mentions=int(entry.get("min_mentions", 0)),
                    actors=entry.get("actors") or [],
                    tags=entry.get("tags") or [],
                )
            )
        logger.info("PlaybookEngine | Loaded %d playbooks", len(self.playbooks))

    def run(self):
        if not self.playbooks:
            return
        for rule in self.playbooks:
            self._execute_rule(rule)

    def _execute_rule(self, rule: PlaybookRule):
        query = """
        MATCH (e:Entity)-[:HAS_PROFILE]->(p:EntityRiskProfile)
        WHERE p.score >= $min_score
        WITH e, p
        OPTIONAL MATCH (e)<-[:REFERS_TO {role:'LOCATION'}]-(s:Signal)
        WITH e, p, count(s) AS mentions
        WHERE mentions >= $min_mentions
        WITH e, p, mentions, coalesce(e.geo_hotspot_score,0) AS hotspot
        WHERE hotspot >= $min_hotspot
        RETURN e.entity_id AS entity_id,
               e.canonical_name AS name,
               p.score AS score,
               hotspot AS hotspot,
               mentions AS mentions
        """
        with self.driver.session() as session:
            rows = session.run(
                query,
                min_score=rule.min_score,
                min_mentions=rule.min_mentions,
                min_hotspot=rule.min_hotspot,
            ).data()

        if not rows:
            return

        relevant = []
        actor_whitelist = {actor.upper() for actor in (rule.actors or [])}
        for row in rows:
            if actor_whitelist:
                name = (row.get("name") or "").upper()
                if name not in actor_whitelist:
                    continue
            relevant.append(row)

        if not relevant:
            return

        for match in relevant:
            case_id = f"case::{rule.id}::{match['entity_id']}"
            with self.driver.session() as session:
                session.run(
                    """
                    MERGE (c:Case {case_id: $case_id})
                    ON CREATE SET c.playbook_id = $playbook_id,
                                  c.description = $description,
                                  c.status = 'OPEN',
                                  c.created_at = datetime()
                    SET c.last_update = datetime(),
                        c.score = $score,
                        c.hotspot = $hotspot,
                        c.mentions = $mentions,
                        c.tags = $tags
                    WITH c
                    OPTIONAL MATCH (e:Entity {entity_id: $entity_id})
                    MERGE (c)-[:MONITORS]->(e)
                    """,
                    {
                        "case_id": case_id,
                        "playbook_id": rule.id,
                        "description": rule.description,
                        "score": match["score"],
                        "hotspot": match["hotspot"],
                        "mentions": match["mentions"],
                        "tags": rule.tags,
                        "entity_id": match["entity_id"],
                    },
                )
            logger.info(
                "Playbook %s | Case created for %s (score %.2f)",
                rule.id,
                match["name"],
                match["score"],
            )
            self.relationships.sync_case_links(
                case_id=case_id,
                playbook_id=rule.id,
                playbook_description=rule.description,
                playbook_tags=rule.tags,
                entity_id=match["entity_id"],
            )
