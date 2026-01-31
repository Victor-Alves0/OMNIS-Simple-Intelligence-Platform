from typing import List, Dict, Set
from functools import lru_cache

from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.entity_resolution.resolver import EntityResolver
from app.utils.omnis_logger import logger
from app.utils.entity_extractor import entity_extractor
from app.utils.normalizer import normalize_actor


class NerEnricher:

    def __init__(self, driver: GraphDatabase.driver, batch_size: int = 200):
        self.driver = driver
        self.batch_size = batch_size
        self.resolver = EntityResolver(driver=self.driver)

        self.actor_labels = {"PERSON", "ORG", "GPE"}
        self.location_labels = {"GPE", "LOC"}

        raw_map = (
            CONFIG.get("rules", {})
            .get("actors", {})
            .get("normalization", {})
        )
        self.actor_norm_map = {k.lower(): v for k, v in raw_map.items()}

    def run(self, limit: int = 1000):
        logger.info("NER Enricher iniciado")

        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (s:Signal:Processed)
                WHERE NOT s:NER_DONE
                RETURN
                    s.uid AS uid,
                    coalesce(s.translated_content, s.original_content, '') AS content
                LIMIT $limit
                """,
                limit=limit,
            )

            signals = [r.data() for r in result]

        if not signals:
            logger.info("NER Enricher: nenhum Signal pendente")
            return
        logger.info(f"NER Enricher: {len(signals)} Signals para processar")

        for i in range(0, len(signals), self.batch_size):
            batch = signals[i:i + self.batch_size]
            self._process_batch(batch)

        logger.info("NER Enricher finalizado com sucesso")

    def _process_batch(self, batch: List[Dict]):
        enriched = []

        for sig in batch:
            uid = sig["uid"]
            text = sig.get("content") or ""

            if not text or len(text) < 20:
                enriched.append(
                    {"uid": uid, "actors": [], "locations": []}
                )
                continue

            actors, locations = self._extract_entities(text)

            enriched.append(
                {
                    "uid": uid,
                    "actors": list(actors),
                    "locations": list(locations),
                }
            )
        self._persist(enriched)

    def _extract_entities(self, text: str) -> (Set[str], Set[str]):
        actors = set()
        locations = set()

        entities = entity_extractor.extract_entities(text)

        for ent in entities:
            label = ent.get("label")
            raw_text = ent.get("text")

            if not raw_text:
                continue

            normalized = self._normalize_actor(raw_text)

            if label in self.actor_labels:
                actors.add(normalized)

            if label in self.location_labels:
                locations.add(normalized)

        return actors, locations

    @lru_cache(maxsize=4096)
    def _normalize_actor(self, text: str) -> str:
        norm = normalize_actor(text)
        return self.actor_norm_map.get(norm.lower(), norm)

    def _persist(self, batch: List[Dict]):
        self.resolver.process_batch(batch)

        with self.driver.session() as session:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (s:Signal {uid: row.uid})
                SET s:NER_DONE
                """,
                rows=[{"uid": row["uid"]} for row in batch],
            )

        logger.info(f"NER Enricher: batch de {len(batch)} Signals resolvido")
