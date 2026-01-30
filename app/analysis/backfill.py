from __future__ import annotations

from typing import List

from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.pipeline_scoring import GlobalRiskScorer
from app.utils.omnis_logger import logger


def _match_themes(driver, days: int = 30) -> None:
    themes = CONFIG.get("rules", {}).get("themes", [])
    concepts_cfg = CONFIG.get("rules", {}).get("concepts", {})
    if not themes:
        return

    for theme in themes:
        t_id = theme.get("id")
        logic = theme.get("logic", {})
        actors = logic.get("actors", []) or []
        concepts = logic.get("concepts", []) or []

        keywords: List[str] = []
        for c_name in concepts:
            for lang_list in concepts_cfg.get(c_name, {}).get("keywords", {}).values():
                keywords.extend(kw.lower() for kw in lang_list)

        query = """
        MATCH (s:Signal)
        WHERE s.timestamp > datetime() - duration({days: $days})
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
          ON CREATE SET t.description = $desc,
                        t.severity_boost = $boost
        MERGE (s)-[:MATCHES_THEME]->(t)
        RETURN count(s) AS detected
        """
        params = {
            "days": days,
            "theme_id": t_id,
            "actors": actors,
            "concept_keywords": list(set(keywords)),
            "desc": theme.get("description", ""),
            "boost": theme.get("severity_boost", 1.0),
        }
        with driver.session() as session:
            res = session.run(query, params).single()
        if res and res["detected"]:
            logger.info("Backfill | Theme %s linked to %d signals", t_id, res["detected"])


def run_backfill(driver: GraphDatabase.driver, *, days: int = 30, run_playbooks: bool = True) -> None:
    _match_themes(driver, days=days)
    if run_playbooks:
        scorer = GlobalRiskScorer(driver)
        scorer.run_all()
