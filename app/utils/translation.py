import openai

from app.config.settings import OPENROUTER_API_KEY, OPENROUTER_SITE_URL
from app.utils.omnis_logger import logger

# CONFIGURAÇÃO OPENROUTER
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Modelo primário 
PRIMARY_TRANSLATION_MODEL = "openai/gpt-4o-mini"

# Modelo de fallback
FALLBACK_TRANSLATION_MODEL = "google/gemini-flash-1.5"

openrouter_client = None
if OPENROUTER_API_KEY:
    try:
        openrouter_client = openai.OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": OPENROUTER_SITE_URL,
                "X-Title": "OMNIS Intelligence System"
            }
        )
        logger.info(f"Cliente OpenRouter inicializado. Modelo primário: {PRIMARY_TRANSLATION_MODEL}")
    except Exception as e:
        logger.error(f"Erro ao inicializar cliente OpenRouter: {e}")
else:
    logger.warning("OPENROUTER_API_KEY não encontrada. Serviços de tradução indisponíveis.")

def _build_prompt(text: str, source_lang: str = None) -> str:
    lang_hint = f" do idioma {source_lang}" if source_lang else ""
    
    return f"""
Você é um tradutor profissional neutro e preciso, especializado em textos de notícias e inteligência militar.

Traduza o seguinte texto para português do Brasil (PT-BR):
- Mantenha termos técnicos, siglas, nomes próprios e jargão militar inalterados
- Preserve tom factual e objetivo, sem opinião ou adição de conteúdo
- Mantenha estrutura, pontuação e significado exatos do original
- Traduza de forma natural para PT-BR

Texto original{lang_hint}:
{text}

Responda apenas com a tradução final (sem introduções ou explicações).
"""

def _translate_with_openrouter(text: str, prompt: str, model_name: str) -> str:
    if not openrouter_client:
        raise RuntimeError("Cliente OpenRouter não configurado")

    response = openrouter_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "Você é um tradutor especializado em inteligência militar."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        max_tokens=2048
    )
    return response.choices[0].message.content.strip()

def translate_to_pt_br(text: str, source_lang: str = None) -> str:
    if not text or not text.strip():
        return text

    prompt = _build_prompt(text, source_lang)
    translated = None
    provider_used = None
    try:
        translated = _translate_with_openrouter(text, prompt, model_name=PRIMARY_TRANSLATION_MODEL)
        provider_used = PRIMARY_TRANSLATION_MODEL
    except Exception as e:
        logger.warning(f"Falha na tradução via {PRIMARY_TRANSLATION_MODEL}: {e}. Tentando fallback...")
        
        #Tentar Modelo de Fallback
        try:
            translated = _translate_with_openrouter(text, prompt, model_name=FALLBACK_TRANSLATION_MODEL)
            provider_used = FALLBACK_TRANSLATION_MODEL
        except Exception as e_fallback:
            logger.error(f"Falha no fallback com {FALLBACK_TRANSLATION_MODEL}: {e_fallback}. Retornando texto original.")
            return text

    # Limpeza final do resultado
    if translated:
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1].strip()
        
        logger.info(f"Tradução OK via {provider_used}: {translated[:50]}...")
        return translated
    
    return text

# Testes isolados
if __name__ == "__main__":
    if not openrouter_client:
        print("Chave da API OpenRouter não configurada. Impossível testar.")
    else:
        test_text = "The quick brown fox jumps over the lazy dog."
        print(f"Original: {test_text}")
        print(f"Traduzido: {translate_to_pt_br(test_text, 'en')}")
