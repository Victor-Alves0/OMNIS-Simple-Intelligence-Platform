import re
import time
import hashlib
import feedparser
from datetime import datetime, timezone
from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.utils.omnis_logger import logger
from app.utils.translation import translate_to_pt_br
from app.utils.normalizer import clean_text
from app.utils.vectorizer import vectorizer
from app.utils.severity_calculator import severity_calculator
from app.utils.metrics import global_metrics, collector_health

class RssCollector:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver
        self.feeds = CONFIG.get("rss", {}).get("feeds", [])

        self.user_agent = CONFIG.get("global", {}) \
            .get("ingestion", {}) \
            .get("user_agent", "OMNIS-RSS/1.0")

    def _extract_timestamp(self, entry):
        try:
            ts = entry.get("published_parsed") or entry.get("updated_parsed")
            if ts:
                return datetime(*ts[:6], tzinfo=timezone.utc)
        except Exception:
            pass
        return datetime.now(timezone.utc)

    def process_new_signals(self, signals: list):
        if not signals:
            return

        logger.info(f"Processando {len(signals)} novos sinais RSS...")

        query = """
        MATCH (sig:Signal {uid: $uid})
        SET
            sig.translated_content = $translated,
            sig.severity = $severity,
            sig.raw_severity = $raw_severity,
            sig.source_trust = $trust,
            sig.category = $category,
            sig.embedding = $embedding,
            sig.keywords = $keywords,
            sig:Processed
        """

        for sig in signals:
            try:
                raw_text = sig["raw_text"]

                translated = translate_to_pt_br(raw_text)
                if not translated:
                    continue

                translated = clean_text(translated)

                embedding = vectorizer.embed(translated)
                if not embedding:
                    continue

                trust = max(0.3, sig.get("trust_score", 0.7))

                severity = severity_calculator.score(translated, trust)
                raw_severity = severity
                keywords = severity_calculator.extract_keywords(translated)

                if severity >= 0.7:
                    category = "HIGH_RISK"
                elif severity >= 0.4:
                    category = "MEDIUM_RISK"
                else:
                    category = "LOW_RISK"

                with self.driver.session() as session:
                    session.run(
                        query,
                        uid=sig["uid"],
                        translated=translated[:2000],
                        severity=severity,
                        raw_severity=raw_severity,
                        trust=trust,
                        category=category,
                        embedding=embedding,
                        keywords=keywords,
                    )

                log = logger.info if category != "LOW_RISK" else logger.debug
                log(f"RSS | {category} | trust={trust:.2f} | {sig['title'][:50]}")

            except Exception:
                logger.error(
                    f"Erro ao processar RSS {sig.get('uid')}",
                    exc_info=True
                )

    def run(self):
        start_time = time.time()
        global_metrics.inc("rss_runs")
        logger.info(f"Iniciando RSS: {len(self.feeds)} feeds configurados")
        entries = []

        for feed in self.feeds:
            url = feed.get("url")
            if not url:
                continue

            try:
                parsed = feedparser.parse(
                    url,
                    request_headers={"User-Agent": self.user_agent},
                )

                if parsed.bozo:
                    logger.warning(
                        f"RSS parse warning ({url}): {parsed.bozo_exception}"
                    )

                for entry in parsed.entries[:15]:
                    title = (entry.get("title") or "").strip()
                    summary = (
                        entry.get("summary")
                        or entry.get("description")
                        or ""
                    ).strip()

                    match = re.search(r'href="([^"]+)"', summary)
                    link = match.group(1) if match else entry.get("link", "")

                    uid = hashlib.sha256(
                        f"{url}|{title}|{link}".encode()
                    ).hexdigest()

                    raw_text = clean_text(f"{title}. {summary}")
                    if len(raw_text) < 40:
                        continue

                    entries.append(
                        {
                            "uid": uid,
                            "title": title[:500],
                            "link": link,
                            "raw_text": raw_text[:3000],
                            "timestamp": self._extract_timestamp(entry).isoformat(),
                            "source_url": url,
                            "region": feed.get("region", "global"),
                            "source_type": feed.get("category", "rss"),
                            "trust_score": feed.get("trust_score", 0.7),
                        }
                    )

            except Exception:
                logger.error(
                    f"Erro ao ler feed RSS ({url})",
                    exc_info=True
                )

        if not entries:
            logger.info("RSS: nenhum item coletado")
            return

        with self.driver.session() as session:
            result = session.run(
                """
                UNWIND $entries AS entry
                MERGE (src:Source {name: entry.source_url, type: 'RSS'})
                ON CREATE SET
                    src.region = entry.region,
                    src.category = entry.source_type,
                    src.trust_score = entry.trust_score

                MERGE (sig:Signal {uid: entry.uid})
                ON CREATE SET
                    sig.title = entry.title,
                    sig.content = entry.raw_text,
                    sig.url = entry.link,
                    sig.timestamp = datetime(entry.timestamp),
                    sig.created = true

                MERGE (src)-[:PUBLISHED]->(sig)

                WITH sig, entry
                WHERE sig.created = true
                REMOVE sig.created
                RETURN
                    sig.uid AS uid,
                    sig.title AS title,
                    sig.content AS raw_text,
                    entry.trust_score AS trust_score
                """,
                entries=entries,
            )

            new_signals = [r.data() for r in result]

        self.process_new_signals(new_signals)
        duration = time.time() - start_time
        global_metrics.set_gauge("rss_last_duration", duration)
        collector_health.mark_success()
        logger.info("Ciclo RSS finalizado")
