import logging
from typing import List, Dict
from functools import lru_cache

import spacy

# CONFIGURAÇÃO
logger = logging.getLogger("omnis.extractor")

MODEL_NAME = "xx_ent_wiki_sm"

# Limite defensivo de caracteres para NER
MAX_TEXT_LENGTH = 1200


class EntityExtractor:
    """
    Extrator de entidades nomeadas (NER) baseado em spaCy.
    Implementado como singleton para reutilização eficiente.
    """

    def __init__(self):
        self.model = self._load_model()

    def _load_model(self):
        try:
            logger.info(f"Carregando modelo NER spaCy '{MODEL_NAME}'...")

            disabled_pipes = ["parser", "tagger", "lemmatizer", "attribute_ruler"]
            model = spacy.load(MODEL_NAME, disable=disabled_pipes)

            logger.info("✅ Modelo NER spaCy carregado com sucesso.")
            return model

        except OSError:
            logger.critical(f"❌ Modelo spaCy '{MODEL_NAME}' não encontrado.")
            logger.critical(
                f"Execute: python -m spacy download {MODEL_NAME}"
            )
            return None

        except Exception:
            logger.critical(
                "❌ Falha crítica ao carregar modelo spaCy.",
                exc_info=True
            )
            return None

    def _sanitize_text(self, text: str) -> str:
        text = " ".join(text.split())
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        return text

    @lru_cache(maxsize=2048)
    def _extract_cached(self, text: str) -> List[Dict]:
        if not self.model:
            return []

        try:
            doc = self.model(text)
            return [
                {"text": ent.text.strip(), "label": ent.label_}
                for ent in doc.ents
                if ent.text and ent.label_
            ]
        except Exception:
            logger.error(
                "Erro ao extrair entidades (conteúdo omitido por segurança).",
                exc_info=True
            )
            return []

    def extract_entities(self, text: str) -> List[Dict]:
        if not text or not isinstance(text, str):
            return []

        sanitized = self._sanitize_text(text)
        if not sanitized:
            return []

        return self._extract_cached(sanitized)

entity_extractor = EntityExtractor()

# TESTE ISOLADO
if __name__ == "__main__":
    logger.info("Executando teste do módulo entity_extractor...")

    frases = [
        "Reuters reported that Apple is looking at buying a U.K. startup in London.",
        "O Irã realizou exercícios militares próximos ao Estreito de Ormuz.",
        "Российские войска продвинулись к границе Украины.",
        "القوات الإسرائيلية نفذت غارة في غزة."
    ]

    for f in frases:
        print(f"\nTexto: {f}")
        ents = entity_extractor.extract_entities(f)
        if ents:
            for e in ents:
                print(f" - {e['text']} ({e['label']})")
        else:
            print(" - Nenhuma entidade detectada.")
