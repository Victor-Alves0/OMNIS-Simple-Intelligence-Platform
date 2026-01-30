from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from neo4j import GraphDatabase

from app.config.settings import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, METRICS_PORT
from app.monitoring.prometheus_exporter import start_prometheus_exporter

start_prometheus_exporter(METRICS_PORT)

app = FastAPI(title="OMNIS Graph Explorer")


def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


@app.get("/entities")
def list_entities(limit: int = 50, q: str | None = None):
    driver = get_driver()
    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (e:Entity)
                WHERE $q IS NULL OR toLower(e.canonical_name) CONTAINS toLower($q)
                RETURN e.entity_id AS id,
                       e.canonical_name AS name,
                       e.type AS type,
                       e.geo_country AS country,
                       e.sector AS sector,
                       e.mention_count AS mentions
                ORDER BY e.mention_count DESC
                LIMIT $limit
                """,
                {"limit": limit, "q": q},
            ).data()
        return rows
    finally:
        driver.close()


@app.get("/entities/{entity_id}")
def entity_detail(entity_id: str):
    driver = get_driver()
    try:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (e:Entity {entity_id: $id})
                OPTIONAL MATCH (e)<-[:MONITORS]-(c:Case)
                OPTIONAL MATCH (s:Signal)-[:REFERS_TO]->(e)
                RETURN e AS entity, collect(distinct c.case_id) AS cases, collect(distinct s.uid) AS signals
                """,
                {"id": entity_id},
            ).single()
        if not row:
            raise HTTPException(status_code=404, detail="Entity not found")
        data = row["entity"]._properties
        data["cases"] = row["cases"]
        data["signals"] = row["signals"]
        return JSONResponse(data)
    finally:
        driver.close()


@app.get("/cases")
def list_cases(limit: int = 50):
    driver = get_driver()
    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (c:Case)
                RETURN c.case_id AS id,
                       c.playbook_id AS playbook,
                       c.status AS status,
                       c.score AS score,
                       c.created_at AS created_at,
                       c.tags AS tags
                ORDER BY c.last_update DESC
                LIMIT $limit
                """,
                {"limit": limit},
            ).data()
        return rows
    finally:
        driver.close()
