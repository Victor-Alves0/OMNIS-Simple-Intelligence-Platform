# app/utils/vectorizer.py
import logging
import threading
from typing import List

from sentence_transformers import SentenceTransformer
from tenacity import retry, stop_after_attempt, wait_fixed

# ────────────────────────────────────────────────
# CONFIGURAÇÃO
# ────────────────────────────────────────────────

logger = logging.getLogger("omnis.vectorizer")

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Limite defensivo de caracteres para evitar:
# - lentidão excessiva
# - truncamento silencioso do modelo
# - consumo exagerado de CPU
MAX_TEXT_LENGTH = 1200


class TextVectorizer:
    """
    Carrega um modelo de SentenceTransformer e gera embeddings vetoriais.
    Implementado como singleton global para evitar recarga custosa.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.model = self._load_model()

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
    def _load_model(self) -> SentenceTransformer:
        """
        Carrega o modelo de embedding com retry para falhas transitórias.
        """
        try:
            logger.info(
                f"Carregando modelo de embedding '{MODEL_NAME}' "
                "(primeira execução pode ser lenta)..."
            )
            model = SentenceTransformer(MODEL_NAME)
            logger.info("✅ Modelo de embedding carregado com sucesso.")
            return model
        except Exception as e:
            logger.critical(
                "❌ Falha crítica ao carregar modelo de embedding",
                exc_info=True
            )
            raise

    def _sanitize_text(self, text: str) -> str:
        """
        Sanitização mínima defensiva:
        - remove whitespace excessivo
        - aplica truncamento
        """
        text = " ".join(text.split())
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        return text

    def embed(self, text: str) -> List[float]:
        """
        Gera embedding vetorial para um texto.

        Retorna:
            List[float] compatível com Neo4j.
            Retorna lista vazia em caso de falha.
        """
        if not self.model:
            logger.error("Modelo de embedding não carregado.")
            return []

        if not text or not isinstance(text, str):
            return []

        try:
            clean_text = self._sanitize_text(text)

            # SentenceTransformer não é garantidamente thread-safe
            # Lock leve evita race conditions raras em async/multithread
            with self._lock:
                vector = self.model.encode(
                    clean_text,
                    convert_to_tensor=False,
                    show_progress_bar=False
                )

            return vector.tolist()

        except Exception:
            logger.error(
                "Erro ao gerar embedding (conteúdo omitido por segurança).",
                exc_info=True
            )
            return []


# ────────────────────────────────────────────────
# SINGLETON GLOBAL
# ────────────────────────────────────────────────

vectorizer = TextVectorizer()


# ────────────────────────────────────────────────
# TESTE ISOLADO
# ────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("Executando teste do vectorizer...")

    frases = [
        "Um míssil balístico foi lançado hoje.",
        "Foguete de longo alcance foi disparado esta manhã.",
        "As negociações de paz avançaram no oriente médio."
    ]

    vetores = [vectorizer.embed(f) for f in frases]

    print(f"Dimensão do vetor: {len(vetores[0])}")

    try:
        import numpy as np

        def cosine_similarity(v1, v2):
            return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

        sim_1_2 = cosine_similarity(vetores[0], vetores[1])
        sim_1_3 = cosine_similarity(vetores[0], vetores[2])

        print(f"Similaridade 1↔2: {sim_1_2:.4f} (esperado: alta)")
        print(f"Similaridade 1↔3: {sim_1_3:.4f} (esperado: baixa)")

        assert sim_1_2 > 0.7
        assert sim_1_3 < 0.5

        print("✅ Teste de similaridade passou.")

    except ImportError:
        print("Numpy não instalado. Pulando teste de similaridade.")
    except Exception as e:
        print(f"❌ Erro no teste: {e}")
