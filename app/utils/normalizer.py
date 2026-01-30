# app/utils/normalizer.py
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import pycountry

# ============================================================
# Normalize country names / codes
# ============================================================
@lru_cache(maxsize=512)
def normalize_country(name_or_code: Optional[str]) -> str:
    """
    Converte nome ou código de país para ISO 3166 alpha-3.
    Ex:
        'US' -> 'USA'
        'United States' -> 'USA'
        'br' -> 'BRA'
    """
    if not name_or_code:
        return "UNKNOWN"

    value = str(name_or_code).strip().upper()
    if not value:
        return "UNKNOWN"

    # Alpha-2
    country = pycountry.countries.get(alpha_2=value)
    if country:
        return country.alpha_3

    # Alpha-3
    country = pycountry.countries.get(alpha_3=value)
    if country:
        return country.alpha_3

    # Nome completo / fuzzy
    try:
        country = pycountry.countries.lookup(value)
        return country.alpha_3
    except LookupError:
        return "UNKNOWN"


# ============================================================
# Normalize actors
# ============================================================
def normalize_actor(name: Optional[str], actors_mapping: dict | None = None) -> str:
    """
    Normaliza nomes de atores:
    - remove pontuação lateral
    - normaliza espaços
    - aplica mapping opcional
    """
    if not name:
        return "UNKNOWN"

    cleaned = re.sub(r"[^\w\s\-]", "", name).strip().lower()

    if not cleaned:
        return "UNKNOWN"

    if actors_mapping:
        return actors_mapping.get(cleaned, cleaned.upper())

    return cleaned.upper()


# ============================================================
# Normalize timestamps
# ============================================================
def normalize_timestamp(ts) -> datetime:
    """
    Converte timestamps variados para datetime UTC tz-aware.
    Aceita:
    - datetime (naive ou aware)
    - string ISO / epoch
    - pandas.Timestamp (fallback)
    """
    if ts is None:
        return datetime.now(timezone.utc)

    # Datetime nativo
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    # String ou numérico
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # Fallback opcional para pandas (se instalado)
    try:
        import pandas as pd

        dt = pd.to_datetime(ts, utc=True)
        return dt.to_pydatetime()
    except Exception:
        return datetime.now(timezone.utc)


# ============================================================
# Clean text (dedup / NLP prep)
# ============================================================
def clean_text(text: Optional[str]) -> str:
    """
    Limpa texto para NLP e deduplicação:
    - remove caracteres invisíveis Unicode
    - normaliza espaços
    """
    if not text:
        return ""

    # Remove zero-width / BOM chars
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # Normaliza espaços
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# Teste rápido
# ============================================================
if __name__ == "__main__":
    print(normalize_country("us"))          # USA
    print(normalize_country("Brazil"))      # BRA
    print(normalize_actor(" Hamas "))       # HAMAS
    print(clean_text("Hello\u200b   world"))
