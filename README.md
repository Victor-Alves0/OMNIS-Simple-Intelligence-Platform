🧠 OMNIS Intelligence Platform
> Real-time intelligence, signal analysis and knowledge graph platform.
---
📖 Overview
OMNIS is a real-time intelligence and signal analysis platform designed to collect, enrich, analyze and visualize signals from multiple open data sources.
The platform integrates data collection pipelines, NLP enrichment, entity resolution, graph intelligence, risk scoring and observability into a unified system built around a graph data model.
OMNIS was designed as a modular intelligence pipeline architecture inspired by intelligence fusion systems, risk analysis platforms and event monitoring infrastructures.
---
🎯 Problem
Monitoring global events, risks, signals and information streams is difficult because data is:
Distributed across multiple platforms
Mostly unstructured text
Difficult to correlate
Difficult to visualize relationships
Hard to prioritize events by severity
Most dashboards only show data, but do not provide:
Entity resolution
Relationship graphs
Risk scoring
Automated playbooks
Pipeline observability
OMNIS was created to solve this problem using a graph-based intelligence pipeline architecture.
---
💡 Solution
OMNIS provides:
Multi-source data collection
NLP enrichment and entity extraction
Entity resolution
Knowledge graph storage
Risk scoring engine
Playbook automation
Real-time dashboard
Pipeline observability
Graph exploration API
The entire platform runs in containers and uses Neo4j as the central intelligence graph.
---
🏗️ Architecture
Data Sources → Collectors → Enrichment → Entity Resolution → Neo4j Graph  
                                                                     ↓  
                                                              Scoring Engine  
                                                                     ↓  
                                     Dashboard / API / Observability
---
🧰 Tech Stack
Layer	Technology
Backend	Python, FastAPI
Pipelines	Async Python Collectors
NLP	NER, Entity Resolution
Database	Neo4j, PostgreSQL
Dashboard	Streamlit
Observability	Prometheus, Grafana
Containers	Docker, Docker Compose
APIs	REST
Data Sources	RSS, GDELT, Telegram, X
---
⚙️ Features
📡 Data Collection
RSS ingestion
GDELT global events
Telegram HUMINT channels
X search collector
Parallel collectors
Signal normalization
Translation
Severity classification
🧠 Enrichment Pipeline
Named Entity Recognition
Entity resolution
Actor extraction
Organization detection
Geographic hotspot detection
🕸️ Graph Intelligence
Neo4j knowledge graph
Entity relationships
Playbook → Case → Entity graph model
📊 Risk Scoring
GlobalRiskScorer
Trend detection
Hotspot analysis
Alert classification
🖥️ Dashboard
Overview metrics
Review queue
Themes and ontology
Geospatial map
Graph visualization
Forecast module
Observability metrics
📈 Observability
Prometheus metrics exporter
Grafana dashboards
Pipeline monitoring
🔌 API
Graph exploration API
Entity queries
Case queries
Relationship queries
---
📂 Project Structure
omnis/
├── app/
├── tests/
├── docker/
├── volumes/
├── docker-compose.yml
└── README.md
---
🚀 Getting Started
Requirements
Docker
Docker Compose
Python 3.11 (optional)
Telegram Developer Account
Optional: OpenRouter API Key
---
▶️ Running the Platform
docker compose up -d --build
---
📈 Observability
Metrics are collected by Prometheus and visualized in Grafana dashboards.
---
🗺️ Roadmap
Add new domains beyond geopolitics
Historical data backfill
End-to-end integration tests
Authentication hardening
Rate limiting
Streaming pipeline architecture
Event correlation engine
---
📜 License
MIT License
---
👨‍💻 Author
Software Engineering Project  
Data Engineering • Graph Systems • Intelligence Platforms • Distributed Systems
