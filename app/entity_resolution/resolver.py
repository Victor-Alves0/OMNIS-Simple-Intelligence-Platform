from __future__ import annotations

from difflib import SequenceMatcher
import time
from typing import Dict, List, Set

import pycountry
from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.entity_resolution.enrichers import GeoEnricher, OrgEnricher
from app.entity_resolution.models import EntityMention, EntityType
from app.utils.normalizer import normalize_actor, normalize_country
from app.utils.omnis_logger import logger
from app.utils.metrics import resolver_health, global_metrics


class EntityResolver:
    """Centraliza normalização, resolução e enriquecimento básico."""

    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver
        self.geo_enricher = GeoEnricher()
        self.org_enricher = OrgEnricher()
        self.location_alias_map = self._build_location_alias_map()
        self._ensure_constraints()

    def _ensure_constraints(self) -> None:
        queries = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:EntityReview) REQUIRE r.review_id IS UNIQUE",
        ]
        with self.driver.session() as session:
            for q in queries:
                session.run(q)
        logger.info("EntityResolver | Constraints checked")

    # ------------------------------------------------------------------
    # Mention builders
    # ------------------------------------------------------------------
    COMMON_ACRONYMS = {
        "USA",
        "UK",
        "UAE",
        "NATO",
        "IDF",
        "IRGC",
        "PLA",
        "EU",
        "UN",
        "IMF",
        "BBC",
        "CNN",
        "FBI",
        "FED",
        "TPP",
        "KMT",
        "RSF",
        "CNA",
        "PM",
    }

    def _build_actor_mention(self, signal_uid: str, actor: str) -> EntityMention | None:
        raw = (actor or "").strip()
        if len(raw) < 2:
            return None
        norm = normalize_actor(raw)
        if not norm or norm == "UNKNOWN":
            return None
        similarity = SequenceMatcher(None, raw.upper(), norm.upper()).ratio()
        confidence = 0.9 if similarity > 0.92 else 0.72 * similarity
        needs_review = False
        reason = None
        if len(raw) <= 3 and norm not in self.COMMON_ACRONYMS:
            needs_review = True
            reason = "short_label"
        elif similarity < 0.7:
            needs_review = True
            reason = "fuzzy_match"
        return EntityMention(
            signal_uid=signal_uid,
            raw_text=raw,
            normalized=norm,
            entity_type=EntityType.ACTOR,
            confidence=confidence,
            needs_review=needs_review,
            review_reason=reason,
        )

    def _build_location_mention(self, signal_uid: str, location: str) -> EntityMention | None:
        raw = (location or "").strip()
        if len(raw) < 2:
            return None
        norm = self._normalize_location(raw)
        if not norm or norm == "UNKNOWN":
            return None
        upper = raw.upper()
        iso2_match = pycountry.countries.get(alpha_2=upper) if len(upper) == 2 else None
        high_confidence = False
        if iso2_match and iso2_match.alpha_3 == norm:
            high_confidence = True
        elif upper == norm or len(raw) > 3:
            high_confidence = True
        confidence = 0.9 if high_confidence else 0.75
        needs_review = not high_confidence
        reason = "ambiguous_geo" if needs_review else None
        return EntityMention(
            signal_uid=signal_uid,
            raw_text=raw,
            normalized=norm,
            entity_type=EntityType.LOCATION,
            confidence=confidence,
            needs_review=needs_review,
            review_reason=reason,
        )

    def build_mentions(self, row: Dict) -> List[EntityMention]:
        mentions: List[EntityMention] = []
        signal_uid = row.get("uid")
        for actor in row.get("actors", []):
            mention = self._build_actor_mention(signal_uid, actor)
            if mention:
                mentions.append(mention)
        for loc in row.get("locations", []):
            mention = self._build_location_mention(signal_uid, loc)
            if mention:
                mentions.append(mention)
        return mentions

    # ------------------------------------------------------------------
    def _mention_to_dict(self, mention: EntityMention) -> Dict:
        review_reason = mention.review_reason or ("low_confidence" if mention.needs_review else None)
        return {
            "signal_uid": mention.signal_uid,
            "raw_text": mention.raw_text,
            "normalized": mention.normalized,
            "entity_type": mention.entity_type.value,
            "confidence": mention.confidence,
            "source": mention.source,
            "entity_id": f"{mention.entity_type.value.lower()}::{mention.normalized}",
            "needs_review": mention.needs_review,
            "review_reason": review_reason,
            "review_id": f"{mention.entity_type.value.lower()}::{mention.normalized}::{mention.signal_uid}",
        }

    def process_batch(self, batch: List[Dict]) -> None:
        start = time.time()
        mentions: List[Dict] = []
        for row in batch:
            for mention in self.build_mentions(row):
                mentions.append(self._mention_to_dict(mention))

        if not mentions:
            return

        review_items = [m for m in mentions if m["needs_review"]]
        entity_ids: Set[str] = {m["entity_id"] for m in mentions}

        query = """
        UNWIND $mentions AS mention
        MATCH (s:Signal {uid: mention.signal_uid})
        MERGE (e:Entity {entity_id: mention.entity_id})
          ON CREATE SET
            e.type = mention.entity_type,
            e.canonical_name = mention.normalized,
            e.aliases = [mention.raw_text],
            e.created_at = datetime(),
            e.updated_at = datetime(),
            e.mention_count = 0
        SET e.aliases =
            CASE
                WHEN mention.raw_text IN coalesce(e.aliases, []) THEN e.aliases
                ELSE coalesce(e.aliases, []) + mention.raw_text
            END,
            e.updated_at = datetime(),
            e.last_seen_signal = mention.signal_uid,
            e.mention_count = coalesce(e.mention_count, 0) + 1
        MERGE (s)-[rel:REFERS_TO {role: mention.entity_type}]->(e)
        SET rel.confidence = mention.confidence,
            rel.source = mention.source,
            rel.created_at = coalesce(rel.created_at, datetime()),
            rel.updated_at = datetime()

        FOREACH (_ IN CASE WHEN mention.entity_type = 'ACTOR' THEN [1] ELSE [] END |
            MERGE (a:Actor {uid: mention.normalized})
            ON CREATE SET a.name = mention.raw_text
            MERGE (s)-[:MENTIONS]->(a)
        )

        FOREACH (_ IN CASE WHEN mention.entity_type = 'LOCATION' THEN [1] ELSE [] END |
            MERGE (l:Location {uid: mention.normalized})
            ON CREATE SET l.name = mention.raw_text
            MERGE (s)-[:MENTIONS]->(l)
        )
        """

        with self.driver.session() as session:
            result = session.run(query, mentions=mentions)
            summary = result.consume()
            if review_items:
                session.run(
                    """
                    UNWIND $reviews AS review
                    MATCH (s:Signal {uid: review.signal_uid})
                    MERGE (r:EntityReview {review_id: review.review_id})
                      ON CREATE SET r.entity_id = review.entity_id,
                                    r.entity_type = review.entity_type,
                                    r.raw_text = review.raw_text,
                                    r.normalized = review.normalized,
                                    r.status = 'PENDING',
                                    r.created_at = datetime()
                    SET r.updated_at = datetime(),
                        r.reason = review.review_reason,
                        r.confidence = review.confidence
                    MERGE (s)-[:NEEDS_REVIEW]->(r)
                    """,
                    reviews=[
                        {
                            **item,
                            "review_reason": item.get("review_reason") or "ambiguous",
                        }
                        for item in review_items
                    ],
                )

        # Enriquecimento e métricas
        try:
            self.geo_enricher.enrich(self.driver, entity_ids)
        except Exception:
            logger.warning("EntityResolver | Geo enrichment failed", exc_info=True)
        try:
            self.org_enricher.enrich(self.driver, entity_ids)
        except Exception:
            logger.warning("EntityResolver | Org enrichment failed", exc_info=True)
        self._record_metrics(
            mentions_count=len(mentions),
            entity_count=len(entity_ids),
            review_count=len(review_items),
            nodes_created=summary.counters.nodes_created,
            rels_created=summary.counters.relationships_created,
        )

        resolver_health.mark_success()
        global_metrics.set_gauge("resolver_last_duration", time.time() - start)
        logger.info(
            "EntityResolver | Persisted %d mentions (%d entities, %d reviews)",
            len(mentions),
            len(entity_ids),
            len(review_items),
        )

    def _record_metrics(
        self,
        *,
        mentions_count: int,
        entity_count: int,
        review_count: int,
        nodes_created: int,
        rels_created: int,
    ) -> None:
        query = """
        MERGE (m:EntityResolverStats {id: 'global'})
        SET m.last_batch_mentions = $mentions,
            m.last_batch_entities = $entities,
            m.last_batch_reviews = $reviews,
            m.last_batch_nodes_created = $nodes,
            m.last_batch_relationships_created = $rels,
            m.last_updated = datetime(),
            m.total_mentions = coalesce(m.total_mentions, 0) + $mentions,
            m.total_entities = coalesce(m.total_entities, 0) + $entities,
            m.total_reviews = coalesce(m.total_reviews, 0) + $reviews
        """
        with self.driver.session() as session:
            session.run(
                query,
                mentions=mentions_count,
                entities=entity_count,
                reviews=review_count,
                nodes=nodes_created,
                rels=rels_created,
            )
    def _build_location_alias_map(self) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        locations = CONFIG.get("rules", {}).get("locations", [])
        for entry in locations:
            iso = entry.get("id", "").upper()
            if not iso:
                continue
            alias_map[iso] = iso
            for alias in entry.get("aliases", []):
                alias_map[str(alias).upper()] = iso
        return alias_map

    def _normalize_location(self, raw: str) -> str:
        upper = raw.strip().upper()
        if upper in self.location_alias_map:
            return self.location_alias_map[upper]
        norm = normalize_country(raw)
        if norm != "UNKNOWN":
            return norm
        return self.location_alias_map.get(upper, "UNKNOWN")
