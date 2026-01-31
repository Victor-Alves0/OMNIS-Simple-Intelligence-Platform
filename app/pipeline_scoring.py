import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.analysis.playbooks import PlaybookEngine
from app.utils.alert_router import send_alert
from app.utils.metrics import global_metrics, playbook_health
from app.utils.omnis_logger import logger


class GlobalRiskScorer:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver
        self.themes = CONFIG.get('rules', {}).get('themes', [])
        self.theme_ids = [t.get("id") for t in self.themes if t.get("id")]
        config_path = Path(__file__).resolve().parent / "config" / "playbooks.yaml"
        self.playbooks = PlaybookEngine(driver=self.driver, config_path=config_path)

    def compute_theme_trends(self):
        if not self.theme_ids:
            return

        logger.info("Scoring | Calculando tendências de temas...")

        query = """
        MATCH (t:Theme)
        WHERE t.id IN $theme_ids
        OPTIONAL MATCH (t)<-[:MATCHES_THEME]-(s24:Signal)
        WHERE s24.timestamp > datetime() - duration({hours: 24})
        WITH t, count(s24) AS vol_24h, coalesce(avg(s24.severity), 0.0) AS sev_24h
        OPTIONAL MATCH (t)<-[:MATCHES_THEME]-(s7d:Signal)
        WHERE s7d.timestamp > datetime() - duration({days: 7})
        WITH t, vol_24h, sev_24h, count(s7d) AS vol_7d
        WITH t, vol_24h, sev_24h,
             CASE WHEN vol_7d < 7 THEN 0
                  ELSE (vol_24h - (vol_7d / 7.0)) / (vol_7d / 7.0)
             END AS momentum
        SET t.volume_24h = vol_24h,
            t.avg_severity_24h = sev_24h,
            t.momentum = momentum,
            t.last_updated = datetime()
        RETURN t.id AS id, vol_24h, momentum
        """

        try:
            with self.driver.session() as session:
                result = session.run(query, theme_ids=self.theme_ids)
                for row in result:
                    if row["vol_24h"] > 0:
                        logger.info(
                            f"   Theme [{row['id']}]: "
                            f"Vol24h={row['vol_24h']} | Momentum={row['momentum']:.2f}"
                        )
        except Exception as e:
            logger.error("Erro ao calcular tendências de temas", exc_info=True)

    # PERFIL DE RISCO DE ATORES
    def compute_actor_risks(self):
        logger.info("Scoring | Atualizando perfis de risco de entidades (ACTOR)...")

        query = """
        MATCH (s:Signal)-[:REFERS_TO {role: 'ACTOR'}]->(e:Entity)
        WHERE s.timestamp > datetime() - duration({days: 3})
        WITH e,
             count(s) AS mentions,
             coalesce(avg(s.severity), 0.0) AS avg_severity,
             sum(CASE WHEN s.category IN ['CRITICAL_RISK','HIGH_RISK'] THEN 1 ELSE 0 END) AS high_risk
        WHERE mentions >= 3
        WITH e, mentions, avg_severity, high_risk,
             (mentions * 0.12) +
             (avg_severity * 5.0) +
             (high_risk * 1.8) AS risk_score
        MERGE (e)-[:HAS_PROFILE]->(p:EntityRiskProfile)
        SET p.score = risk_score,
            p.mentions_3d = mentions,
            p.high_risk_3d = high_risk,
            p.last_updated = datetime(),
            p.level = CASE
                WHEN risk_score > 10 THEN 'CRITICAL'
                WHEN risk_score > 5 THEN 'HIGH'
                ELSE 'MODERATE'
            END
        RETURN e.canonical_name AS name, risk_score, p.level AS level
        ORDER BY risk_score DESC
        LIMIT 10
        """

        try:
            with self.driver.session() as session:
                result = session.run(query)
                for row in result:
                    logger.info(
                        f"   Actor [{row['name']}]: "
                        f"Score={row['risk_score']:.1f} ({row['level']})"
                    )
        except Exception as e:
            logger.error("Erro ao atualizar perfis de atores", exc_info=True)

    # HOTSPOTS GEOGRÁFICOS
    def compute_geo_hotspots(self):
        logger.info("Scoring | Atualizando hotspots geográficos...")

        query = """
        MATCH (s:Signal)-[:REFERS_TO {role:'LOCATION'}]->(e:Entity)
        WHERE s.timestamp > datetime() - duration({days: 7})
        WITH e, count(s) AS vol, coalesce(avg(s.severity), 0.0) AS sev
        SET e.geo_active_signals_7d = vol,
            e.geo_avg_severity_7d = sev,
            e.geo_hotspot_score = vol * sev,
            e.geo_last_updated = datetime()
        """

        try:
            with self.driver.session() as session:
                session.run(query)
        except Exception as e:
            logger.error("Erro ao atualizar hotspots geográficos", exc_info=True)

    # EXECUÇÃO CONTROLADA
    def run_all(self):
        logger.info("Scoring | Iniciando ciclo global de análise de risco...")
        start = time.time()
        self.compute_theme_trends()
        self.compute_actor_risks()
        self.compute_geo_hotspots()
        self.compute_entity_alerts()
        try:
            self.playbooks.run()
            playbook_health.mark_success()
        except Exception as exc:
            playbook_health.mark_error(str(exc))
            raise

        global_metrics.set_gauge("scoring_last_duration", time.time() - start)
        global_metrics.inc("scoring_runs")
        logger.info("Scoring | Ciclo global concluído com sucesso.")

    # ALERTAS DE ENTIDADES
    def compute_entity_alerts(self):
        logger.info("Scoring | Atualizando alertas de entidades...")

        query = """
        MATCH (e:Entity)-[:HAS_PROFILE]->(p:EntityRiskProfile)
        WITH e, p, coalesce(e.geo_hotspot_score, 0) AS hotspot
        WHERE p.score >= 6 OR hotspot >= 25
        MERGE (alert:EntityAlert {entity_id: e.entity_id})
        SET alert.name = e.canonical_name,
            alert.score = p.score,
            alert.level = p.level,
            alert.hotspot = hotspot,
            alert.updated_at = datetime(),
            alert.status = 'ACTIVE'
        """

        cleanup = """
        MATCH (alert:EntityAlert)
        WHERE alert.updated_at < datetime() - duration({hours: 12})
        SET alert.status = 'STALE'
        """

        try:
            with self.driver.session() as session:
                session.run(query)
                session.run(cleanup)
                alerts = session.run(
                    """
                    MATCH (alert:EntityAlert)
                    WHERE alert.status = 'ACTIVE' AND alert.updated_at > datetime() - duration({hours:1})
                    RETURN alert.name AS name, alert.score AS score, alert.level AS level
                    LIMIT 5
                    """
                ).data()
        except Exception as exc:
            logger.error("Erro ao atualizar alertas de entidades", exc_info=True)
            return
        for alert in alerts or []:
            send_alert(
                title=f"Entity Alert: {alert['name']}",
                message=f"Score {alert['score']:.1f} ({alert['level']})",
                severity="critical",
            )
