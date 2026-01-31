import re
import tweepy
from neo4j import GraphDatabase

from app.config.settings import CONFIG, NER_ENABLED, X_BEARER_TOKEN
from app.utils.omnis_logger import logger
from app.utils.translation import translate_to_pt_br
from app.utils.normalizer import (
    clean_text,
    normalize_timestamp,
    normalize_country,
)
from app.utils.vectorizer import vectorizer
from app.ner.service import ner_service

# CREDENCIAIS X
# ENGINE DE REGRAS (NER + KEYWORDS)
class XRuleEngine:
    def __init__(self):
        rules = CONFIG.get("rules", {})
        self.concepts = rules.get("concepts", {})
        self.actors_norm_map = {
            k.lower(): v
            for k, v in rules.get("actors", {})
                            .get("normalization", {})
                            .items()
        }

        self.actor_labels = {"PERSON", "ORG"}
        self.location_labels = {"GPE", "LOC"}

        self._compile_concept_patterns()

    def _compile_concept_patterns(self):
        self.keyword_severity_map = {}
        keywords = []

        for c_data in self.concepts.values():
            sev = float(c_data.get("severity", 0.5))
            for lang_keywords in c_data.get("keywords", {}).values():
                for kw in lang_keywords:
                    norm_kw = kw.strip().lower()
                    self.keyword_severity_map[norm_kw] = sev
                    keywords.append(re.escape(norm_kw))

        self.global_pattern = (
            re.compile(r"\b(" + "|".join(keywords) + r")\b", re.IGNORECASE)
            if keywords else None
        )

    def analyze(self, text: str):
        if not text:
            return 0.0, [], [], []

        severity = 0.0
        actors = set()
        locations = set()

        entities = []

        if NER_ENABLED:
            entities = ner_service.extract(text)
            
        for ent in entities:
            label = ent["label"]
            raw = ent["text"].strip()
            raw_l = raw.lower()

            if label in self.actor_labels:
                actor = self.actors_norm_map.get(raw_l, raw.upper())
                actors.add(actor)
                severity = max(severity, 0.2)

            elif label in self.location_labels:
                loc = normalize_country(raw)
                if loc != "UNKNOWN":
                    locations.add(loc)

        keywords = []
        if self.global_pattern:
            matches = self.global_pattern.findall(text)
            keywords = list(set(m.lower() for m in matches))

            for kw in keywords:
                severity = max(severity, self.keyword_severity_map.get(kw, 0.0))

        return severity, list(actors)[:5], list(locations)[:3], keywords[:10]


# COLETOR X
class XSearchCollector:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver

        if not X_BEARER_TOKEN:
            logger.warning("X_BEARER_TOKEN ausente. Coletor X desativado.")
            self.client = None
            return

        self.client = tweepy.Client(
            bearer_token=X_BEARER_TOKEN,
            wait_on_rate_limit=True
        )

        config_x = CONFIG.get("x_search", {})
        self.engine = XRuleEngine()
        self.queries = config_x.get("queries", [])
        self.limit = int(config_x.get("limit", 10))
        self.min_severity = 0.4
        self.trust_score = float(config_x.get("trust_score", 0.4))

    def is_duplicate(self, uid: str) -> bool:
        with self.driver.session() as session:
            res = session.run(
                "MATCH (s:Signal {uid:$uid}) RETURN count(s) AS c",
                uid=uid
            ).single()
            return res["c"] > 0

    def ingest_tweet(self, tweet, user):
        uid = f"x_{tweet.id}"
        if self.is_duplicate(uid):
            return

        text = tweet.text or ""

        lang = tweet.lang or "en"
        translated = clean_text(
            translate_to_pt_br(text, source_lang=lang)
        )

        embedding = vectorizer.embed(translated)
        severity, actors, locations, keywords = self.engine.analyze(translated)

        if severity < self.min_severity:
            return

        category = "HIGH_RISK" if severity >= 0.7 else "MEDIUM_RISK"
        timestamp = normalize_timestamp(tweet.created_at)
        username = user.username if user else "unknown"

        query = """
        MERGE (src:Source {name:$source, type:'X'})
        ON CREATE SET src.trust_score = $trust
        SET src.trust_score = coalesce(src.trust_score, $trust)
        MERGE (s:Signal {uid:$uid})
        ON CREATE SET s.created = true
        SET
            s.content = $text,
            s.translated_content = $translated,
            s.timestamp = datetime($timestamp),
            s.severity = $severity,
            s.raw_severity = $raw_severity,
            s.source_trust = $trust,
            s.category = $category,
            s.language = $lang,
            s.keywords = $keywords,
            s.embedding = $embedding,
            s:Processed
        MERGE (src)-[:PUBLISHED]->(s)
        WITH s
        UNWIND $actors AS aid
            MERGE (a:Actor {uid:aid})
            MERGE (s)-[:MENTIONS]->(a)
        WITH s
        UNWIND $locations AS lid
            MERGE (l:Location {uid:lid})
            MERGE (s)-[:MENTIONS]->(l)
        """

        params = {
            "uid": uid,
            "source": f"@{username}",
            "text": text[:2000],
            "translated": translated[:2000],
            "timestamp": timestamp,
            "severity": severity,
            "raw_severity": severity,
            "category": category,
            "lang": lang,
            "keywords": keywords,
            "actors": actors,
            "locations": locations,
            "embedding": embedding,
            "trust": self.trust_score,
        }

        try:
            with self.driver.session() as session:
                session.run(query, params)
            logger.info(
                f"X | @{username} | {category} | sev={severity:.2f} | "
                f"actors={len(actors)} locs={len(locations)}"
            )
        except Exception as e:
            logger.error("Erro ao persistir tweet X", exc_info=True)

    def run(self):
        if not self.client:
            return
        if not CONFIG.get("x_search", {}).get("enabled", False):
            logger.info("Coletor X desativado via config.")
            return

        logger.info(f"Coletando X ({len(self.queries)} queries)...")

        for q in self.queries:
            try:
                resp = self.client.search_recent_tweets(
                    query=q,
                    max_results=min(self.limit, 100),
                    tweet_fields=["created_at", "lang", "author_id"],
                    expansions=["author_id"],
                )

                users = {
                    u.id: u for u in (resp.includes or {}).get("users", [])
                }

                for tweet in resp.data or []:
                    self.ingest_tweet(tweet, users.get(tweet.author_id))

            except tweepy.TooManyRequests:
                logger.warning("Rate limit X atingido.")
                break
            except Exception:
                logger.error(f"Erro na query X '{q}'", exc_info=True)
