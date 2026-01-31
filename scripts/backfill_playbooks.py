import argparse

from neo4j import GraphDatabase

from app.config.settings import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from app.analysis.backfill import run_backfill


def main():
    parser = argparse.ArgumentParser(description="Backfill playbooks using historical signals")
    parser.add_argument("--dry-run", action="store_true", help="Somente rodar consultas, sem executar scorer")
    parser.add_argument("--days", type=int, default=30, help="Janela de histórico (dias)")
    args = parser.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        run_backfill(driver, days=args.days, run_playbooks=not args.dry_run)
    finally:
        driver.close()
