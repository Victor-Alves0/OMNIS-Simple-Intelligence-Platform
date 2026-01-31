import asyncio
import sys
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from app.config.settings import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, METRICS_PORT
from app.utils.omnis_logger import logger
from app.omnis_pipeline import OmnisPipeline
from app.pipeline_scoring import GlobalRiskScorer
from app.utils.vectorizer import vectorizer
from app.monitoring.prometheus_exporter import start_prometheus_exporter

# CONFIGURAÇÃO DE BOOTSTRAP
BOOTSTRAP_DELAY = 10
NEO4J_MAX_RETRIES = 10
SCORING_INTERVAL = 3600  # 1h


# SETUP DO BANCO DE DADOS
async def check_neo4j_health(driver: GraphDatabase.driver):
    logger.info(f"Aguardando {BOOTSTRAP_DELAY}s para inicialização dos serviços...")
    await asyncio.sleep(BOOTSTRAP_DELAY)

    for attempt in range(1, NEO4J_MAX_RETRIES + 1):
        try:
            with driver.session() as session:
                session.run("RETURN 1").consume()
            logger.info("✅ Neo4j Health Check: CONNECTED")
            return
        except ServiceUnavailable:
            logger.warning(f"Neo4j indisponível (tentativa {attempt}/{NEO4J_MAX_RETRIES})...")
            await asyncio.sleep(2 * attempt)

    logger.critical("❌ Neo4j não respondeu após múltiplas tentativas. Abortando.")
    sys.exit(1)


def ensure_vector_index(driver: GraphDatabase.driver):
    index_name = "signal_embedding_index"
    vector_dim = len(vectorizer.embed("test"))

    logger.info(f"Verificando índice vetorial '{index_name}' (dim={vector_dim})...")

    query = f"""
    CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
    FOR (s:Signal) ON (s.embedding)
    OPTIONS {{
        indexConfig: {{
            `vector.dimensions`: {vector_dim},
            `vector.similarity_function`: 'cosine'
        }}
    }}
    """
    try:
        with driver.session() as session:
            session.run(query)
        logger.info(f"✅ Índice vetorial '{index_name}' pronto.")
    except Exception as e:
        logger.error(f"❌ Falha ao criar/verificar índice vetorial: {e}", exc_info=True)


# TAREFA DE SCORING (ANALYTICS)
async def run_analytics_loop(driver: GraphDatabase.driver, stop_event: asyncio.Event):
    scorer = GlobalRiskScorer(driver=driver)
    logger.info("📊 Analytics Scheduler iniciado.")

    while not stop_event.is_set():
        try:
            scorer.run_all()
            logger.info(f"Analytics concluído. Próxima execução em {SCORING_INTERVAL // 60} min.")
            await asyncio.wait_for(stop_event.wait(), timeout=SCORING_INTERVAL)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error(f"Erro no ciclo de Analytics: {e}", exc_info=True)
            await asyncio.sleep(60)

    logger.info("Analytics Scheduler encerrado.")


# MAIN ASYNC LOOP
async def main():
    logger.info("🚀 OMNIS INTELLIGENCE SYSTEM STARTING...")

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        max_connection_lifetime=3600,
        max_connection_pool_size=20,
    )

    start_prometheus_exporter(METRICS_PORT)

    stop_event = asyncio.Event()
    tasks = []

    try:
        await check_neo4j_health(driver)
        ensure_vector_index(driver)

        pipeline = OmnisPipeline(driver=driver)

        tasks.append(asyncio.create_task(pipeline.start(), name="pipeline"))
        tasks.append(asyncio.create_task(run_analytics_loop(driver, stop_event), name="analytics"))

        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        logger.warning("Tarefas canceladas.")
    except KeyboardInterrupt:
        logger.warning("🛑 SIGINT recebido. Iniciando shutdown...")
    finally:
        stop_event.set()

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        driver.close()
        logger.info("🔒 Conexão Neo4j fechada. Shutdown completo.")


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
