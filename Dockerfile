FROM python:3.11-slim

# ────────────────────────────────────────────────────────────────────────────────
# Environment
# ────────────────────────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ────────────────────────────────────────────────────────────────────────────────
# System dependencies (mínimo)
# ────────────────────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ────────────────────────────────────────────────────────────────────────────────
# Python deps (camada cacheável)
# ────────────────────────────────────────────────────────────────────────────────
COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel && \
    pip install \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt

# ────────────────────────────────────────────────────────────────────────────────
# spaCy models necessários pelos módulos do projeto
# ────────────────────────────────────────────────────────────────────────────────
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
      https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl \
      https://github.com/explosion/spacy-models/releases/download/xx_ent_wiki_sm-3.8.0/xx_ent_wiki_sm-3.8.0-py3-none-any.whl

# ────────────────────────────────────────────────────────────────────────────────
# Application code (última camada pra não invalidar deps)
# ────────────────────────────────────────────────────────────────────────────────
COPY . .

CMD ["python", "app/main.py"]
