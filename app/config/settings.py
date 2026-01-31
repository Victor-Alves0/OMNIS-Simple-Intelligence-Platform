import os
import logging
import yaml
from pathlib import Path
from dotenv import load_dotenv

# 1. Configuração Inicial
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OMNIS_CONFIG")

BASE_DIR = Path(__file__).resolve().parent.parent.parent 
CONFIG_DIR = BASE_DIR / "app" / "config"
RULES_DIR = CONFIG_DIR / "rules"

# 2. Função utilitária para carregar YAML
def load_yaml(file_path: Path):
    """Carrega um arquivo YAML com tratamento de erro robusto."""
    if not file_path.exists():
        logger.warning(f"⚠️ Arquivo de configuração não encontrado: {file_path}")
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"❌ Erro crítico ao ler {file_path}: {e}")
        return {}

# 3. Carregar Fontes (Data Ingestion)
SOURCES_PATH = CONFIG_DIR / "sources.yaml"
CONFIG = load_yaml(SOURCES_PATH)

# Ner
NER_ENABLED = CONFIG.get("ner", {}).get("enabled", True)

# Database (Neo4j)
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# LLMs / AI Services
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "http://omnis-intel.internal")

# Telegram Client
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "omnis_session")
TELEGRAM_SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")
TELEGRAM_SESSIONS_DIR = os.getenv("TELEGRAM_SESSIONS_DIR", "/app/sessions")

# Observability
METRICS_PORT = int(os.getenv("METRICS_PORT", "9464"))

# Dashboard / API Credentials
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "nsa-secure")

# Integrations
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SPACY_MODEL = os.getenv("SPACY_MODEL")

# 5. Carregar Regras de Inteligência (Logic Layer)
ACTORS_PATH = RULES_DIR / "actors.yaml"
ONTOLOGY_PATH = RULES_DIR / "ontology.yaml"
THEMES_PATH = RULES_DIR / "themes.yaml"
LOCATIONS_PATH = RULES_DIR / "locations.yaml"

CONFIG["rules"] = {
    "actors": load_yaml(ACTORS_PATH).get("actors", {}),
    "concepts": load_yaml(ONTOLOGY_PATH).get("concepts", {}),
    "themes": load_yaml(THEMES_PATH).get("themes", []),
    "locations": load_yaml(LOCATIONS_PATH).get("locations", [])
}

if not CONFIG.get("rules", {}).get("concepts"):
    logger.warning("⚠️ Ontologia (ontology.yaml) parece vazia ou não foi carregada.")

if not CONFIG.get("rules", {}).get("actors"):
    logger.warning("⚠️ Definição de Atores (actors.yaml) parece vazia.")

logger.info("✅ Configurações OMNIS carregadas com sucesso.")
logger.info(f"   - Fontes: {SOURCES_PATH}")
logger.info(f"   - Regras: {RULES_DIR}")
