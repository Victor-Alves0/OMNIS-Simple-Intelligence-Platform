# OMNIS Intelligence Platform

OMNIS é meu simples laboratório pessoal para coleta, enriquecimento e visualização de sinais em tempo real. O projeto nasce da vontade de replicar algumas ideias usadas por plataformas como Palantir ou pelos times de análise de risco da NSA. Tudo roda com containers, Neo4j como grafo central e um dashboard Streamlit que costura coleta, scoring, playbooks e observabilidade.

## O que ele faz hoje

- **Coletores paralelos**: RSS (com feeds customizados), GDELT (eventos globais), Telegram HUMINT (canais específicos) e buscas no X. Cada coletor normaliza, traduz, calcula severidade e grava o sinal no grafo.
- **Enriquecimento**: pipeline de NER + resolução de entidades que converte textos em atores, organizações e hotspots geográficos.
- **Playbooks e scoring**: regras YAML definem temas, ontologia e casos. O `GlobalRiskScorer` calcula tendências, hotspots e alertas com veredito crítico/médio/baixo.
- **Dashboard**: Streamlit exibe overview, fila de review, temas, mapa geoespacial, grafo Playbook → Case → Entity, aba de previsão (LLM) e módulos de observabilidade.
- **Observabilidade**: Prometheus + Grafana monitoram coletores, resolvers e playbooks; métricas são expostas via exporter próprio.
- **APIs auxiliares**: há um `omnis-api` (FastAPI) para explorar o grafo e um script em `tests/create_telegram_session.py` que automatiza a criação do arquivo `.session` do Telethon.

## Arquitetura (visão rápida)

```text
┌─────────┐    ┌──────────┐    ┌────────────┐
│Feeds/X/ │ -> │Collectors│ -> │Neo4j (grafo)│
│Telegram │    └──────────┘    └─────┬──────┘
└─────────┘                           │
                                      │
                          ┌───────────▼───────────┐
                          │Pipelines & Scoring    │
                          │- NER / Resolver       │
                          │- GlobalRiskScorer     │
                          │- PlaybookEngine       │
                          └───────────┬───────────┘
                                      │
        ┌──────────────┬──────────────┴──────────────┐
        │Dashboard     │Prometheus/Grafana           │API
        │(Streamlit)   │(observabilidade)            │(graph explorer)
```

## Requisitos

- Docker e docker-compose
- Python 3.11 (caso deseje rodar scripts locais)
- Conta de desenvolvedor no Telegram (API ID/HASH)
- Opcional: chave do OpenRouter para traduções e previsões

## Configuração

1. Copie `.env.example` (ou `.env`) e preencha:
   ```ini
   # Configurações do Neo4j
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=DuskCrown5
   NEO4J_URI=bolt://neo4j:7687

   # Configurações do Postgres
   POSTGRES_USER=omnis_admin
   POSTGRES_PASSWORD=Antennae4
   POSTGRES_DB=omnis_metadata

   # Chaves de API
   OPENROUTER_API_KEY=

   # Telegram
   TELEGRAM_API_ID=
   TELEGRAM_API_HASH=
   TELEGRAM_SESSION_NAME=omnis_humint

   # X
   X_CONSUMER_KEY=
   X_CONSUMER_SECRET=
   X_ACCESS_TOKEN=
   X_ACCESS_TOKEN_SECRET=

   # Dashboard
   DASHBOARD_USER=admin
   DASHBOARD_PASS=admin
   ```

2. Gere o arquivo de sessão do Telegram (apenas uma vez):
   ```bash
   python tests/create_telegram_session.py
   ```
   O script pergunta seu telefone/código, salva `volumes/telegram_sessions/omnis_session` e o coletor passa a iniciar sem interação.

3. Ajuste os arquivos YAML em `app/config/`:
   - `sources.yaml`: define coletores e limites.
   - `rules/actors.yaml`, `rules/ontology.yaml`, `rules/themes.yaml`: mapeiam atores, conceitos e temas.
   - `config/playbooks.yaml`: descreve playbooks → casos → gatilhos.

## Executando

```bash
docker compose up -d --build
```

Serviços relevantes:

| Serviço          | Porta | Descrição                           |
|------------------|-------|-------------------------------------|
| `omnis-dashboard`| 8501  | Interface Streamlit                 |
| `omnis-api`      | 8000  | API (FastAPI) para explorar o grafo |
| `neo4j`          | 7474/7687 | Banco e browser Neo4j         |
| `prometheus`     | 9090  | Métricas                             |
| `grafana`        | 3000  | Dashboards de observabilidade        |

Login padrão do dashboard: `admin / nsa-secure-2026` (configure via `.env`).

## Desenvolvimento

- O código usa `PYTHONPATH=/app` dentro dos containers, mas também roda localmente (`pip install -r requirements.txt`).
- Testes unitários básicos estão em `tests/`. Rode com `pytest`.

## Roadmap pessoal

- Trazer novos domínios/temas (além de geopolítica), com ajustes de ontologia e severidade.
- Completar playbooks e reviews automáticos com dados históricos (backfill).
- Adicionar testes de integração end-to-end (pipeline → Neo4j → dashboard).
- Endurecer autenticação (tokens rotativos, rate limiting no dashboard/API).
- Documentar dashboards Grafana de referência.

## Aviso

Este repositório é um estudo pessoal. Ele não representa nenhuma organização oficial e não foi auditado para uso corporativo. Se você decidir usar em produção, revise segurança, compliance e fontes de dados.
