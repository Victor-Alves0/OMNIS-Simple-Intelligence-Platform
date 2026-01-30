import threading
from typing import Dict, List

import spacy

from app.config.settings import CONFIG, SPACY_MODEL
from app.utils.omnis_logger import logger

DEFAULT_MODEL = "xx_ent_wiki_sm"
FALLBACK_MODELS = ["en_core_web_md"]

_NLP = None
_NLP_LOCK = threading.Lock()


def _candidate_models() -> List[str]:
    """Resolve the list of spaCy models to try, in priority order."""
    configured = SPACY_MODEL or CONFIG.get("ner", {}).get("model") or DEFAULT_MODEL

    seen = set()
    ordered: List[str] = []
    for name in [configured, *FALLBACK_MODELS]:
        if name and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def _load_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP

    with _NLP_LOCK:
        if _NLP is not None:
            return _NLP

        errors = []
        for model_name in _candidate_models():
            try:
                logger.info(f"Loading spaCy NER model: {model_name}")
                _NLP = spacy.load(model_name)
                return _NLP
            except Exception as exc:
                logger.warning(f"spaCy model {model_name} indisponivel: {exc}")
                errors.append((model_name, exc))

        raise RuntimeError(
            "Nenhum modelo spaCy pode ser carregado. "
            + "; ".join(f"{name}: {err}" for name, err in errors)
        )


class NerService:
    LABEL_MAP = {
        "NORP": "ORG",
        "FAC": "LOC",
        "GPE": "LOC",
    }

    def _normalize_label(self, label: str) -> str:
        if not label:
            return "MISC"
        return self.LABEL_MAP.get(label, label)

    def extract(self, text: str) -> List[Dict]:
        if not text or not text.strip():
            return []

        try:
            nlp = _load_nlp()
            doc = nlp(text)

            return [
                {
                    "text": ent.text,
                    "label": self._normalize_label(ent.label_),
                    "start": ent.start_char,
                    "end": ent.end_char,
                }
                for ent in doc.ents
            ]
        except Exception as exc:
            logger.error(f"NER failure: {exc}", exc_info=True)
            return []


ner_service = NerService()
