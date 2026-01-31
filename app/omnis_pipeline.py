import asyncio
from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.utils.omnis_logger import logger

from app.collectors.gdelt import GdeltIngestor
from app.collectors.rss import RssCollector
from app.collectors.telegram import TelegramHumintCollector
from app.collectors.x_search import XSearchCollector

# NER centralizado
from app.pipeline.ner_enricher import NerEnricher


class OmnisPipeline:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver

        # Coletores
        self.rss = RssCollector(driver=self.driver)
        self.gdelt = GdeltIngestor(driver=self.driver)
        self.telegram = TelegramHumintCollector(driver=self.driver)
        self.x_search = XSearchCollector(driver=self.driver)

        # NER centralizado
        self.ner = NerEnricher(driver=self.driver)

        # Regras de correlação
        self.themes = CONFIG.get("rules", {}).get("themes", [])

    def close(self):
        try:
            if self.driver:
                self.driver.close()
                logger.info("Pipeline | Driver Neo4j fechado com sucesso.")
        except Exception:
            logger.warning("Pipeline | Erro ao fechar driver Neo4j.", exc_info=True)

    # INTELIGÊNCIA / CORRELAÇÃO
    def run_theme_analysis(self):
        logger.info("Pipeline | Executando análise de Temas (Correlation Engine)...")

        if not self.themes:
            return

        for theme in self.themes:
            t_id = theme.get("id")
            logic = theme.get("logic", {})

            must_actors = logic.get("actors", [])
            must_concepts = logic.get("concepts", [])

            if not must_actors and not must_concepts:
                continue

            query = """
            MATCH (s:Signal)
            WHERE s.timestamp > datetime() - duration({hours: 24})
            AND NOT (s)-[:MATCHES_THEME]->(:Theme {id: $theme_id})

            WITH s
            WHERE size($actors) = 0 OR EXISTS {
                MATCH (s)-[:MENTIONS]->(a:Actor)
                WHERE a.uid IN $actors
            }

            WITH s
            WHERE size($concept_keywords) = 0
               OR any(k IN coalesce(s.keywords, []) WHERE k IN $concept_keywords)

            MERGE (t:Theme {id: $theme_id})
            ON CREATE SET
                t.description = $desc,
                t.severity_boost = $boost
            MERGE (s)-[:MATCHES_THEME]->(t)
            RETURN count(s) AS detected
            """

            concept_keywords = []
            all_concepts = CONFIG.get("rules", {}).get("concepts", {})

            for c_name in must_concepts:
                keywords_data = all_concepts.get(c_name, {}).get("keywords", {})
                for lang_list in keywords_data.values():
                    concept_keywords.extend(
                        kw.lower() for kw in lang_list
                    )

            params = {
                "theme_id": t_id,
                "desc": theme.get("description", ""),
                "boost": theme.get("severity_boost", 1.0),
                "actors": must_actors,
                "concept_keywords": list(set(concept_keywords)),
            }

            try:
                with self.driver.session() as session:
                    res = session.run(query, params).single()
                    if res and res["detected"] > 0:
                        logger.info(
                            f"⚡ TEMA DETECTADO: {t_id} "
                            f"({res['detected']} novos sinais)"
                        )
            except Exception:
                logger.error(
                    f"Erro analisando tema {t_id}",
                    exc_info=True
                )

    async def run_scheduler(self):
        logger.info("Pipeline | Scheduler iniciado (RSS / GDELT / X)")

        while True:
            logger.info("Pipeline | Iniciando novo ciclo de coleta...")

            tasks = [
                asyncio.to_thread(self._safe_run, self.gdelt.run, "GDELT"),
                asyncio.to_thread(self._safe_run, self.rss.run, "RSS"),
                asyncio.to_thread(self._safe_run, self.x_search.run, "X_SEARCH"),
            ]

            await asyncio.gather(*tasks)

            # NER centralizado
            logger.info("Pipeline | Coleta concluída. Rodando NER...")
            await asyncio.to_thread(self.ner.run, 1000)

            logger.info("Pipeline | Rodando correlação de temas...")
            await asyncio.to_thread(self.run_theme_analysis)

            logger.info("Pipeline | Ciclo completo. Aguardando 30 min...")
            await asyncio.sleep(1800)

    def _safe_run(self, fn, name: str):
        try:
            fn()
        except Exception:
            logger.error(
                f"Pipeline | Falha no coletor {name}",
                exc_info=True
            )

    # START GLOBAL
    async def start(self):
        logger.info("🛡️ OMNIS INTELLIGENCE SYSTEM STARTING...")

        try:
            telegram_task = asyncio.create_task(self.telegram.run())
            scheduler_task = asyncio.create_task(self.run_scheduler())

            await asyncio.gather(telegram_task, scheduler_task)

        finally:
            self.close()
