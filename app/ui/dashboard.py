# app/ui/dashboard.py
import math
import uuid
from pathlib import Path
from textwrap import shorten
from typing import Dict, List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from neo4j import GraphDatabase
from neo4j.time import DateTime
from pyvis.network import Network

from app.analysis.backfill import run_backfill
from app.analysis.playbooks import PlaybookEngine
from app.analysis.predictor import PredictionEngine
from app.config.settings import (
    CONFIG,
    DASHBOARD_PASS,
    DASHBOARD_USER,
    METRICS_PORT,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
)
from app.pipeline_scoring import GlobalRiskScorer
from app.utils.vectorizer import vectorizer
from app.utils.metrics import (
    collector_health,
    resolver_health,
    playbook_health,
    global_metrics,
)
from app.monitoring.prometheus_exporter import start_prometheus_exporter

# Observability
start_prometheus_exporter(METRICS_PORT)

# ────────────────────────────────────────────────────────────────────────────────
# 1. STREAMLIT CONFIG + THEME
# ────────────────────────────────────────────────────────────────────────────────
if "page_config_set" not in st.session_state:
    st.set_page_config(
        page_title="OMNIS | Intelligence Dashboard",
        layout="wide",
        initial_sidebar_state="expanded",
        page_icon="🛰️",
    )
    st.session_state.page_config_set = True

st.markdown(
    """
<style>
html, body, [class*="css"] { background-color: #0e1117 !important; color: #e5e7eb !important; }
[data-testid="stSidebar"] { background-color: #111827 !important; border-right: 1px solid #1f2937; }
.stMetric { background: #1f2937; padding: 14px; border-radius: 8px; border: 1px solid #374151; }
div[data-testid="stDataFrame"] { border: 1px solid #374151; border-radius: 6px; }
.badge { padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; margin-left: 6px; }
.badge-high { background: #dc2626; color: #fff; }
.badge-medium { background: #f97316; color: #1f2937; }
.badge-low { background: #10b981; color: #06281e; }
.stTabs [role="tablist"] { flex-wrap: wrap; gap: 4px; }
.stTabs [role="tab"] {
    flex: 1 0 160px;
    border: 1px solid #1f2937;
    border-radius: 6px;
    margin-bottom: 4px;
    background: #111827;
}
</style>
""",
    unsafe_allow_html=True,
)

# ────────────────────────────────────────────────────────────────────────────────
# 2. ENV & DRIVER
# ────────────────────────────────────────────────────────────────────────────────
PLAYBOOK_PATH = Path(__file__).resolve().parents[1] / "config" / "playbooks.yaml"


@st.cache_resource(show_spinner=False)
def get_driver():
    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        max_connection_lifetime=300,
    )


driver = get_driver()


@st.cache_resource(show_spinner=False)
def get_prediction_engine():
    return PredictionEngine(driver)


def run_query(query: str, params: Dict | None = None) -> List[Dict]:
    """Helper centralizado para Neo4j → python dict."""
    try:
        with driver.session() as session:
            result = session.run(query, params or {})
            rows = []
            for record in result:
                clean = {}
                for key, value in record.data().items():
                    if isinstance(value, DateTime):
                        clean[key] = value.to_native()
                    else:
                        clean[key] = value
                rows.append(clean)
            return rows
    except Exception as exc:  # pragma: no cover
        st.error(f"Database error: {exc}")
        return []


@st.cache_data(ttl=300)
def get_top_entities(limit: int = 50) -> List[str]:
    rows = run_query(
        """
        MATCH (e:Entity)
        RETURN e.canonical_name AS name
        ORDER BY coalesce(e.mention_count, 0) DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return [row["name"] for row in rows if row.get("name")]


def list_cases(status: str = "OPEN", limit: int = 30):
    rows = run_query(
        """
        MATCH (c:Case)
        WHERE $status = 'ALL' OR c.status = $status
        RETURN c.case_id AS id,
               c.status AS status,
               c.score AS score,
               c.hotspot AS hotspot,
               c.playbook_id AS playbook,
               c.created_at AS created_at,
               c.last_update AS last_update,
               c.tags AS tags
        ORDER BY c.last_update DESC
        LIMIT $limit
        """,
        {"status": status, "limit": limit},
    )
    return rows


def get_case_detail(case_id: str):
    row = run_query(
        """
        MATCH (c:Case {case_id:$id})
        OPTIONAL MATCH (c)-[:MONITORS]->(e:Entity)
        OPTIONAL MATCH (n:CaseNote)-[:NOTE_FOR]->(c)
        WITH c, e, n
        ORDER BY n.created_at DESC
        WITH c, e, collect(n) AS notes
        OPTIONAL MATCH (e)<-[:REFERS_TO]-(s:Signal)
        RETURN c AS case_node,
               e AS entity_node,
               notes AS notes,
               collect(DISTINCT s) AS signals
        """,
        {"id": case_id},
    )
    if not row:
        return None
    data = row[0]
    case = data["case_node"]._properties if data.get("case_node") else {}
    entity = data["entity_node"]._properties if data.get("entity_node") else {}
    notes = []
    for note in data.get("notes") or []:
        if note:
            notes.append(note._properties)
    signals = []
    for sig in data.get("signals") or []:
        if sig:
            signals.append(sig._properties)
    return {"case": case, "entity": entity, "notes": notes, "signals": signals}


def add_case_note(case_id: str, author: str, content: str):
    if not content.strip():
        return
    note_id = f"note::{uuid.uuid4()}"
    with driver.session() as session:
        session.run(
            """
            MATCH (c:Case {case_id:$id})
            CREATE (n:CaseNote {
                note_id:$note_id,
                author:$author,
                content:$content,
                created_at:datetime()
            })
            MERGE (n)-[:NOTE_FOR]->(c)
            """,
            {"id": case_id, "note_id": note_id, "author": author or "analyst", "content": content.strip()},
        )


def update_case_status(case_id: str, status: str):
    with driver.session() as session:
        session.run(
            """
            MATCH (c:Case {case_id:$id})
            SET c.status = $status,
                c.last_update = datetime()
            """,
            {"id": case_id, "status": status},
        )


def get_entity_graph(hours: int = 24, limit: int = 100, min_score: float = 0.0, types: List[str] | None = None):
    types = types or []
    nodes = run_query(
        """
        MATCH (s:Signal)-[:REFERS_TO]->(e:Entity)
        WHERE s.timestamp > datetime() - duration({hours:$hours})
        OPTIONAL MATCH (e)-[:HAS_PROFILE]->(p:EntityRiskProfile)
        WITH e, coalesce(p.score,0) AS score
        WHERE score >= $min_score AND ($types = [] OR e.type IN $types)
        RETURN e.entity_id AS id,
               e.canonical_name AS name,
               e.type AS type,
               score AS score
        ORDER BY score DESC, name ASC
        LIMIT $limit
        """,
        {"hours": hours, "limit": limit, "min_score": min_score, "types": types},
    )
    node_ids = [node["id"] for node in nodes]
    if not node_ids:
        return nodes, []
    edges = run_query(
        """
        MATCH (s:Signal)-[:REFERS_TO]->(e:Entity)
        WHERE s.timestamp > datetime() - duration({hours:$hours})
          AND e.entity_id IN $ids
        WITH s, collect(DISTINCT e) AS ents
        UNWIND ents AS e1
        UNWIND ents AS e2
        WITH e1, e2
        WHERE e1.entity_id < e2.entity_id
        RETURN e1.entity_id AS source_id,
               e2.entity_id AS target_id,
               count(*) AS weight
        """,
        {"hours": hours, "ids": node_ids},
    )
    return nodes, edges


def plot_entity_graph(nodes: List[Dict], edges: List[Dict]):
    net = Network(height="620px", bgcolor="#0e1117", font_color="#e5e7eb")
    net.barnes_hut()
    color_map = {
        "ACTOR": "#f97316",
        "LOCATION": "#0ea5e9",
        "ORG": "#a3e635",
    }
    for node in nodes:
        net.add_node(
            node["id"],
            label=node["name"],
            title=f"{node['name']} ({node.get('type','')})<br>Score {node.get('score',0):.2f}",
            color=color_map.get(node.get("type"), "#c084fc"),
            value=max(node.get("score", 0), 1.0),
        )
    for edge in edges:
        net.add_edge(edge["source_id"], edge["target_id"], value=edge["weight"])
    return net.generate_html()


def get_case_graph(limit: int = 30):
    rows = run_query(
        """
        MATCH (p:Playbook)-[:GENERATED]->(c:Case)-[:MONITORS]->(e:Entity)
        OPTIONAL MATCH (c)-[:LINKS_SIGNAL]->(s:Signal)
        OPTIONAL MATCH (c)-[:TAGGED_THEME]->(t:Theme)
        RETURN p.id AS playbook,
               coalesce(p.tags, []) AS playbook_tags,
               c.case_id AS case_id,
               c.status AS status,
               c.score AS score,
               c.last_update AS last_update,
               e.canonical_name AS entity,
               e.entity_id AS entity_id,
               count(DISTINCT s) AS signal_count,
               collect(DISTINCT s.uid) AS signal_ids,
               collect(DISTINCT t.id) AS themes
        ORDER BY c.last_update DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return rows


def plot_case_graph(rows: List[Dict]):
    net = Network(height="620px", bgcolor="#0e1117", font_color="#e5e7eb")
    net.force_atlas_2based()
    for row in rows:
        playbook_id = row["playbook"]
        case_id = row["case_id"]
        entity_id = row["entity_id"]
        themes = [t for t in row.get("themes", []) if t]
        tags = row.get("playbook_tags") or []
        score = float(row.get("score") or 0.0)
        signals_total = int(row.get("signal_count") or 0)
        tooltip = (
            f"Case {case_id}<br>Status: {row.get('status')}<br>"
            f"Score: {score:.1f}<br>"
            f"Signals: {signals_total}<br>"
            f"Themes: {', '.join(themes) or '—'}"
        )

        net.add_node(
            f"playbook::{playbook_id}",
            label=f"Playbook: {playbook_id}",
            title=f"Tags: {', '.join(tags) or '—'}",
            shape="box",
            color="#9333ea",
        )
        net.add_node(
            f"case::{case_id}",
            label=f"Case: {case_id.split('::')[-1]}",
            title=tooltip,
            color="#f97316" if row.get("status") != "CLOSED" else "#22c55e",
            value=max(score, 1.0),
        )
        net.add_node(
            f"entity::{entity_id}",
            label=row.get("entity") or entity_id,
            title=f"Entity {row.get('entity')} (cases linked)",
            color="#38bdf8",
        )
        net.add_edge(f"playbook::{playbook_id}", f"case::{case_id}", color="#e0f2fe")
        net.add_edge(f"case::{case_id}", f"entity::{entity_id}", color="#fed7aa")
    return net.generate_html()


def resolve_review(review_id: str) -> None:
    """Marca um item da fila de revisão como resolvido."""
    with driver.session() as session:
        session.run(
            """
            MATCH (r:EntityReview {review_id: $id})
            SET r.status = 'RESOLVED',
                r.resolved_at = datetime()
            """,
            {"id": review_id},
        )


# ────────────────────────────────────────────────────────────────────────────────
# 3. AUTH
# ────────────────────────────────────────────────────────────────────────────────
def check_auth() -> bool:
    if st.session_state.get("authenticated"):
        return True

    st.markdown("<h2 style='text-align:center'>🛰️ OMNIS LOGIN</h2>", unsafe_allow_html=True)
    with st.form("login"):
        user = st.text_input("User")
        pwd = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        if user == DASHBOARD_USER and pwd == DASHBOARD_PASS:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid credentials")
    return False


if not check_auth():
    st.stop()

# ────────────────────────────────────────────────────────────────────────────────
# 4. SIDEBAR CONTROLS
# ────────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🛰️ OMNIS INTEL")

    if st.button("🔄 Refresh"):
        st.rerun()

    if st.button("📈 Run Analytics"):
        with st.spinner("Running analytics..."):
            GlobalRiskScorer(driver).run_all()
        st.success("Analytics completed")
        st.rerun()

    st.divider()
    st.success("System: OPERATIONAL")

    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

# ────────────────────────────────────────────────────────────────────────────────
# 5. HIGHLIGHTS (KPIs, TOP THEME, TRUST BOARD, ALERTS)
# ────────────────────────────────────────────────────────────────────────────────
kpi = run_query(
    """
MATCH (s:Signal)
WHERE s.timestamp > datetime() - duration({hours:24})
RETURN
  count(s) as total,
  sum(CASE WHEN s.category IN ['CRITICAL_RISK','HIGH_RISK'] THEN 1 ELSE 0 END) as critical,
  coalesce(avg(s.severity),0) as avg_sev
"""
)
kpi_data = kpi[0] if kpi else {"total": 0, "critical": 0, "avg_sev": 0}

last_ts = run_query("MATCH (s:Signal) RETURN max(s.timestamp) AS ts")
last_update = last_ts[0]["ts"] if last_ts and last_ts[0]["ts"] else None

c1, c2 = st.columns([3, 1])
c1.title("Global Threat Monitor")
c2.caption(f"Last DB Update: {last_update or 'N/A'}")

k1, k2, k3 = st.columns(3)
k1.metric("Signals (24h)", kpi_data["total"])
k2.metric("Critical/High", kpi_data["critical"])
k3.metric("Avg Severity", f"{kpi_data['avg_sev']:.2f}")

resolver_stats = run_query(
    """
MATCH (m:EntityResolverStats {id:'global'})
RETURN m.last_batch_mentions AS last_mentions,
       m.last_batch_entities AS last_entities,
       m.last_batch_reviews AS last_reviews,
       m.last_updated AS last_updated
LIMIT 1
"""
)
resolver_data = resolver_stats[0] if resolver_stats else None
if resolver_data:
    rs1, rs2, rs3 = st.columns(3)
    rs1.metric("Resolver Mentions (batch)", resolver_data.get("last_mentions", 0))
    rs2.metric("Entities touched", resolver_data.get("last_entities", 0))
    rs3.metric("Review queue (batch)", resolver_data.get("last_reviews", 0))
    if resolver_data.get("last_updated"):
        st.caption(f"Resolver last run: {resolver_data['last_updated']}")

theme_activity = run_query(
    """
MATCH (t:Theme)
OPTIONAL MATCH (t)<-[:MATCHES_THEME]-(s:Signal)
WITH t,
     sum(CASE WHEN s.timestamp > datetime() - duration({hours:24}) THEN 1 ELSE 0 END) AS vol24,
     sum(CASE WHEN s.timestamp > datetime() - duration({days:7}) THEN 1 ELSE 0 END) AS vol7,
     avg(CASE WHEN s.timestamp > datetime() - duration({days:7}) THEN s.severity ELSE NULL END) AS sev7,
     max(s.timestamp) AS last_ts
RETURN t.id AS Theme,
       t.description AS Description,
       t.severity_boost AS Boost,
       vol24 AS Volume24h,
       vol7 AS Volume7d,
       coalesce(sev7, 0) AS Severity7d,
       last_ts AS LastSignal
"""
)
theme_df = pd.DataFrame(theme_activity)
if theme_df.empty:
    config_themes = CONFIG.get("rules", {}).get("themes", [])
    if config_themes:
        theme_df = pd.DataFrame(
            [
                {
                    "Theme": t.get("id"),
                    "Description": t.get("description"),
                    "Boost": t.get("severity_boost", 1.0),
                    "Volume24h": 0,
                    "Volume7d": 0,
                    "Severity7d": 0.0,
                    "LastSignal": "n/a",
                }
                for t in config_themes
            ]
        )
top_theme = None
if not theme_df.empty:
    top_theme = (
        theme_df.sort_values(["Volume24h", "Volume7d", "Boost"], ascending=False)
        .iloc[0]
        .to_dict()
    )

trust_board = run_query(
    """
MATCH (src:Source)-[:PUBLISHED]->(s:Signal)
WHERE s.timestamp > datetime() - duration({hours:24})
RETURN src.name AS Source,
       round(avg(s.source_trust),2) AS Trust,
       count(*) AS Volume
ORDER BY Trust DESC, Volume DESC
LIMIT 5
"""
)

entity_spotlight = run_query(
    """
MATCH (e:Entity {type:'ACTOR'})-[:HAS_PROFILE]->(p:EntityRiskProfile)
RETURN e.canonical_name AS Name,
       coalesce(e.sector, 'Unknown') AS Sector,
       round(p.score,2) AS Score,
       p.level AS Level,
       p.mentions_3d AS Mentions
ORDER BY p.score DESC
LIMIT 1
"""
)

entity_alerts = run_query(
    """
MATCH (a:EntityAlert)
WHERE a.status = 'ACTIVE'
RETURN a.name AS Name,
       coalesce(a.score,0) AS Score,
       coalesce(a.hotspot,0) AS Hotspot,
       coalesce(a.level,'HIGH') AS Level,
       a.updated_at AS UpdatedAt
ORDER BY Score DESC, Hotspot DESC
LIMIT 5
"""
)

signal_alerts = run_query(
    """
MATCH (s:Signal)<-[:PUBLISHED]-(src:Source)
WHERE s.timestamp > datetime() - duration({hours:24})
RETURN s.uid AS uid,
       src.name AS source,
       s.severity AS severity,
       s.category AS category,
       coalesce(s.translated_content, s.content) AS text,
       s.timestamp AS ts
ORDER BY s.severity DESC
LIMIT 2
"""
)

st.divider()
highlight_tabs = st.tabs(["🔥 Themes", "🏅 Sources", "🛰️ Entities", "🚨 Signals"])

with highlight_tabs[0]:
    if top_theme:
        sev_value = float(top_theme.get("Severity7d", 0) or 0)
        badge = (
            "badge-high"
            if sev_value >= 0.7
            else "badge-medium"
            if sev_value >= 0.4
            else "badge-low"
        )
        volume24 = int(top_theme.get("Volume24h", 0) or 0)
        volume7 = int(top_theme.get("Volume7d", 0) or 0)
        st.markdown(
            f"""
**{top_theme['Theme']}**
<span class="badge {badge}">{sev_value:.2f} sev (7d)</span>  
Volume 24h: **{volume24}** | Volume 7d: **{volume7}**
""",
            unsafe_allow_html=True,
        )
        last_signal = top_theme.get("LastSignal") or "n/a"
        st.caption(f"{top_theme.get('Description') or 'Sem descrição'} — último sinal: {last_signal}")
    else:
        st.info("Nenhum tema com atividade recente.")

with highlight_tabs[1]:
    if trust_board:
        df_trust = pd.DataFrame(trust_board)
        st.dataframe(df_trust, use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados suficientes.")

with highlight_tabs[2]:
    spotlight = entity_spotlight[0] if entity_spotlight else None
    if spotlight:
        st.markdown(
            f"**{spotlight['Name']}** · setor {spotlight['Sector']}  \n"
            f"Score: {spotlight['Score']:.2f} ({spotlight['Level']}) · menções 3d: {spotlight['Mentions']}"
        )
    else:
        st.info("Nenhuma entidade com perfil disponível.")

    st.caption("Alertas recentes")
    if entity_alerts:
        entity_alert_df = pd.DataFrame(entity_alerts)
        st.dataframe(entity_alert_df, use_container_width=True, hide_index=True)
    else:
        st.info("Sem alertas de entidade ativos.")

with highlight_tabs[3]:
    if signal_alerts:
        alerts_df = pd.DataFrame(signal_alerts)
        alerts_df["severity_fmt"] = alerts_df["severity"].apply(
            lambda sev: f"{sev:.2f}" if isinstance(sev, (int, float)) else "N/A"
        )
        st.dataframe(
            alerts_df[["source", "severity_fmt", "category", "text"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Nenhum alerta crítico nas últimas 24h.")


st.divider()

# ────────────────────────────────────────────────────────────────────────────────
# 6. TABS
# ────────────────────────────────────────────────────────────────────────────────
tabs = st.tabs(
    [
        "🎯 Themes",
        "🌍 Geo",
        "🧑 Entities",
        "📡 Signals",
        "📈 Trends",
        "🔎 Search",
        "🗂 Cases",
        "🤖 Predictions",
        "🕸 Graph",
        "📝 Reviews",
        "🛠 Ops",
        "📊 Observability",
    ]
)

# ── THEMES TAB ──────────────────────────────────────────────────────────────────
with tabs[0]:
    if theme_df.empty:
        st.info("Sem temas configurados.")
    else:
        theme_df_display = theme_df.copy()
        theme_df_display["LastSignal"] = theme_df_display["LastSignal"].fillna("n/a")
        recent_df = theme_df_display[theme_df_display["Volume24h"] > 0]
        if recent_df.empty:
            st.warning("Nenhum tema com volume nas últimas 24h. Exibindo atividade de 7 dias.")
            recent_df = (
                theme_df_display.sort_values("Volume7d", ascending=False)
                .head(10)
            )
        fig = px.bar(
            recent_df.sort_values("Volume7d"),
            x="Volume7d",
            y="Theme",
            color="Severity7d",
            color_continuous_scale="Reds",
            orientation="h",
            labels={"Volume7d": "Volume 7d", "Severity7d": "Sev 7d"},
        )
        fig.update_layout(
            height=520,
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="#e5e7eb",
            coloraxis_colorbar=dict(title="Sev 7d"),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Watchlist de Temas")
        st.dataframe(
            theme_df_display[
                [
                    "Theme",
                    "Volume24h",
                    "Volume7d",
                    "Severity7d",
                    "LastSignal",
                    "Description",
                ]
            ].sort_values(["Volume24h", "Volume7d"], ascending=False),
            use_container_width=True,
            hide_index=True,
        )

# ── GEO TAB ─────────────────────────────────────────────────────────────────────
with tabs[1]:
    geo_df = pd.DataFrame(
        run_query(
            """
    MATCH (s:Signal)-[:MENTIONS]->(l:Location)
    WHERE s.timestamp > datetime() - duration({days:7})
    RETURN l.uid AS ISO3, count(*) AS Signals
    """
        )
    )
    if not geo_df.empty:
        fig = px.choropleth(
            geo_df,
            locations="ISO3",
            locationmode="ISO-3",
            color="Signals",
            color_continuous_scale="Blues",
        )
        fig.update_geos(
            showcountries=True,
            countrycolor="#374151",
            showcoastlines=True,
            coastlinecolor="#1f2937",
            projection_type="natural earth",
            bgcolor="#0e1117",
        )
        fig.update_layout(
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="#e5e7eb",
            coloraxis_colorbar=dict(title="Signals"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem menções geográficas nos últimos 7 dias.")

# ── ACTORS TAB ──────────────────────────────────────────────────────────────────
with tabs[2]:
    actors = pd.DataFrame(
        run_query(
            """
    MATCH (e:Entity {type:'ACTOR'})-[:HAS_PROFILE]->(p:EntityRiskProfile)
    RETURN e.canonical_name AS Entity,
           coalesce(e.sector, 'Unknown') AS Sector,
           round(p.score,2) AS Score,
           p.level AS Level,
           p.mentions_3d AS Mentions
    ORDER BY Score DESC
    LIMIT 20
    """
        )
    )
    if not actors.empty:
        actors.rename(columns={"Entity": "Actor"}, inplace=True)
        fig = px.bar(
            actors,
            x="Score",
            y="Actor",
            color="Level",
            orientation="h",
            color_discrete_map={
                "CRITICAL": "#dc2626",
                "HIGH": "#f97316",
                "MODERATE": "#2563eb",
            },
        )
        fig.update_layout(
            height=520,
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="#e5e7eb",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(actors, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum ator com perfil calculado.")

# ── SIGNALS TAB ─────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Signals Feed")
    col_filters = st.columns(3)
    hours = col_filters[0].selectbox("Janela", ["24h", "48h", "72h", "7d"], index=0)
    hours_map = {"24h": 24, "48h": 48, "72h": 72, "7d": 168}
    min_sev = col_filters[1].slider("Severity mínima", 0.0, 1.0, 0.5, 0.05)
    limit = col_filters[2].slider("Máx. registros", 50, 500, 150, 25)

    signals = pd.DataFrame(
        run_query(
            """
    MATCH (s:Signal)<-[:PUBLISHED]-(src:Source)
    WHERE s.timestamp > datetime() - duration({hours:$hours})
      AND s.severity >= $min_sev
    OPTIONAL MATCH (s)-[:MATCHES_THEME]->(t:Theme)
    OPTIONAL MATCH (s)-[:REFERS_TO]->(ent:Entity)
    WITH s, src, t, collect(DISTINCT ent.canonical_name) AS entities
    RETURN
        s.timestamp AS Time,
        src.name AS Source,
        s.category AS Category,
        round(s.severity,2) AS Severity,
        round(s.source_trust,2) AS Trust,
        coalesce(t.id, '—') AS Theme,
        coalesce(s.translated_content, s.content) AS Content,
        entities AS Entities
    ORDER BY s.severity DESC, s.timestamp DESC
    LIMIT $limit
    """,
            {"hours": hours_map[hours], "min_sev": float(min_sev), "limit": int(limit)},
        )
    )

    if not signals.empty:
        signals["Entities"] = signals["Entities"].apply(
            lambda ents: ", ".join(sorted(e for e in ents if e)) if isinstance(ents, list) else ""
        )
        signals["Snippet"] = signals["Content"].apply(
            lambda txt: shorten(txt or "", width=200, placeholder="…")
        )
        st.dataframe(
            signals[["Time", "Source", "Category", "Severity", "Trust", "Theme", "Entities", "Snippet"]],
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "Exportar CSV",
            data=signals.to_csv(index=False).encode("utf-8"),
            mime="text/csv",
            file_name="signals.csv",
        )
    else:
        st.info("Nenhum sinal com os filtros selecionados.")

# ── TRENDS TAB ──────────────────────────────────────────────────────────────────
with tabs[4]:
    trends = pd.DataFrame(
        run_query(
            """
    MATCH (s:Signal)
    WHERE s.timestamp > datetime() - duration({days:14})
    WITH date(s.timestamp) AS d, s.category AS cat
    RETURN toString(d) AS Date, cat AS Category, count(*) AS Count
    """
        )
    )
    if not trends.empty:
        fig = px.area(
            trends,
            x="Date",
            y="Count",
            color="Category",
            color_discrete_map={
                "CRITICAL_RISK": "#dc2626",
                "HIGH_RISK": "#f97316",
                "MEDIUM_RISK": "#facc15",
                "LOW_RISK": "#22d3ee",
            },
        )
        fig.update_layout(
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="#e5e7eb",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados suficientes para a série de 14 dias.")

# ── SEARCH TAB ──────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Busca semântica + contextual")
    query = st.text_input("Termo ou entidade")
    top_k = st.slider("Top K", min_value=5, max_value=100, value=15, step=5)
    min_similarity = st.slider("Simil. mínima", 0.5, 1.0, 0.7, 0.05)
    entity_options = ["(nenhum)"] + get_top_entities(100)
    entity_filter = st.selectbox("Filtrar por entidade", entity_options, index=0)
    entity_filter_value = "" if entity_filter == "(nenhum)" else entity_filter

    if st.button("Pesquisar"):
        if not query.strip():
            st.warning("Digite um termo para buscar.")
        else:
            vec = vectorizer.embed(query)
            if not vec:
                st.error("Não foi possível gerar embedding para o termo informado.")
            else:
                results = pd.DataFrame(
                    run_query(
                        """
        CALL db.index.vector.queryNodes('signal_embedding_index', $k, $vec)
        YIELD node, score
        WHERE score >= $min_sim
        MATCH (node)<-[:PUBLISHED]-(src:Source)
        OPTIONAL MATCH (node)-[:MATCHES_THEME]->(t:Theme)
        OPTIONAL MATCH (node)-[:REFERS_TO]->(ent:Entity)
        WITH node, score, src,
             collect(distinct t.id) AS themes,
             collect(distinct ent.canonical_name) AS entities
        WHERE
            ($entity = '' OR any(e IN entities WHERE e = $entity)) AND
            (
                $term = ''
                OR toLower(coalesce(node.translated_content, node.content, '')) CONTAINS toLower($term)
                OR any(k IN coalesce(node.keywords, []) WHERE toLower(k) CONTAINS toLower($term))
            )
        RETURN
            node.timestamp AS Time,
            src.name AS Source,
            round(node.severity,2) AS Severity,
            round(node.source_trust,2) AS Trust,
            themes AS Themes,
            score AS Similarity,
            entities AS Entities,
            coalesce(node.translated_content, node.content) AS Content
        ORDER BY score DESC
        LIMIT 100
        """,
                        {
                            "k": int(top_k),
                            "vec": vec,
                            "term": query.strip(),
                            "min_sim": float(min_similarity),
                            "entity": entity_filter_value,
                        },
                    )
                )

                if not results.empty:
                    results["Entities"] = results["Entities"].apply(
                        lambda ents: ", ".join(sorted(e for e in ents if e)) if isinstance(ents, list) else ""
                    )
                    results["Snippet"] = results["Content"].apply(
                        lambda txt: shorten(txt or "", width=220, placeholder="…")
                    )
                    st.dataframe(
                        results[
                            ["Time", "Source", "Severity", "Trust", "Themes", "Entities", "Similarity", "Snippet"]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("Nenhum resultado encontrado para esse termo/limiar.")

# ─── REVIEW TAB ────────────────────────────────────────────────────────────────
with tabs[6]:
    st.subheader("Case Board (Exploração Guiada)")
    case_col1, case_col2 = st.columns(2)
    status_filter = case_col1.selectbox("Status", ["OPEN", "INVESTIGATING", "CLOSED", "ALL"])
    case_limit = case_col2.slider("Qtd. casos", 5, 100, 20, 5)
    cases = list_cases(status=status_filter, limit=case_limit)
    if not cases:
        st.info("Nenhum caso disponível.")
    else:
        df_cases = pd.DataFrame(cases)
        st.dataframe(df_cases, use_container_width=True, hide_index=True)

        labels = {c["id"]: f"{c['id']} ({c['status']})" for c in cases}
        selected_case = st.selectbox("Selecione um caso", list(labels.keys()), format_func=lambda x: labels.get(x, x))
        details = get_case_detail(selected_case) if selected_case else None
        if details:
            case_data = details["case"]
            entity_data = details["entity"]

            st.markdown(f"### Caso {case_data.get('case_id')}")
            c_metrics = st.columns(3)
            c_metrics[0].metric("Score", f"{case_data.get('score', 0):.1f}")
            c_metrics[1].metric("Hotspot", f"{case_data.get('hotspot', 0):.1f}")
            c_metrics[2].metric("Status", case_data.get("status", "N/A"))
            st.caption(f"Playbook: {case_data.get('playbook_id')} | Criado: {case_data.get('created_at')} | Última atualização: {case_data.get('last_update')}")
            st.caption(f"Tags: {', '.join(case_data.get('tags') or []) or '—'}")

            st.markdown("#### Entidade monitorada")
            if entity_data:
                st.write(f"**{entity_data.get('canonical_name')}** ({entity_data.get('type')})")
                st.write(f"Setor: {entity_data.get('sector', '—')} | País: {entity_data.get('geo_country', '—')}")
            else:
                st.write("Sem entidade associada.")

            signals = details.get("signals") or []
            if signals:
                st.markdown("#### Sinais ligados ao caso")
                sig_df = pd.DataFrame(
                    [
                        {
                            "UID": s.get("uid"),
                            "Timestamp": s.get("timestamp"),
                            "Severity": s.get("severity"),
                            "Category": s.get("category"),
                        }
                        for s in signals[:10]
                    ]
                )
                st.dataframe(sig_df, use_container_width=True, hide_index=True)

            st.markdown("#### Notas colaborativas")
            notes = details.get("notes") or []
            if not notes:
                st.info("Nenhuma nota registrada.")
            else:
                for note in notes:
                    st.markdown(
                        f"**{note.get('author','analyst')}** ({note.get('created_at')}):\n\n{note.get('content')}"
                    )

            with st.form(f"note-form-{selected_case}"):
                st.markdown("Adicionar nota")
                author = st.text_input("Analista", value="analyst")
                note_content = st.text_area("Conteúdo")
                if st.form_submit_button("Registrar nota"):
                    add_case_note(selected_case, author, note_content)
                    st.success("Nota adicionada.")
                    st.rerun()

            status_col1, status_col2 = st.columns(2)
            new_status = status_col1.selectbox(
                "Atualizar status",
                ["OPEN", "INVESTIGATING", "CLOSED"],
                index=["OPEN", "INVESTIGATING", "CLOSED"].index(case_data.get("status", "OPEN")),
            )
            if status_col2.button("Salvar status", key=f"status-{selected_case}"):
                update_case_status(selected_case, new_status)
                st.success("Status atualizado.")
                st.rerun()

# ───────── PREDICTIONS TAB ─────────────────────────────────────────────────────
with tabs[7]:
    st.subheader("Predições assistidas por IA")
    predictor = get_prediction_engine()
    if not predictor.available:
        st.info("Configure OPENROUTER_API_KEY para habilitar predições.")
    else:
        question = st.text_area(
            "Pergunta",
            placeholder="Qual a chance de um novo ataque ocorrer na Somália nos próximos dias?",
        )
        pred_hours = st.slider("Janela (horas)", 24, 240, 96, 12)
        context_limit = st.slider("Sinais para contexto", 10, 50, 25, 5)
        if st.button("Gerar previsão"):
            with st.spinner("Consultando modelo..."):
                try:
                    result = predictor.ask(question, hours=pred_hours, limit=context_limit)
                    st.markdown(result["answer"])
                    with st.expander("Contexto usado"):
                        st.text(result["context"])
                except RuntimeError as err:
                    st.error(str(err))
                except Exception as exc:
                    st.error(f"Falha ao gerar previsão: {exc}")

# ───────── GRAPH TAB ───────────────────────────────────────────────────────────
with tabs[8]:
    st.subheader("Graph Explorer")
    graph_mode = st.radio(
        "Modo",
        options=["Entidades", "Playbooks/Casos"],
        horizontal=True,
        key="graph_mode",
    )

    if graph_mode == "Entidades":
        graph_col1, graph_col2, graph_col3 = st.columns(3)
        graph_hours = graph_col1.slider("Janela (horas)", 6, 168, 48, 6, key="graph_hours")
        min_score = graph_col2.slider("Score mínimo", 0.0, 20.0, 5.0, 0.5, key="graph_score")
        edge_cap = graph_col3.slider("Máx. de entidades", 20, 200, 80, 10, key="graph_nodes")
        filter_types = st.multiselect(
            "Tipos",
            options=["ACTOR", "LOCATION", "ORG"],
            default=["ACTOR", "LOCATION"],
        )

        nodes, edges = get_entity_graph(
            hours=graph_hours,
            limit=edge_cap,
            min_score=min_score,
            types=filter_types,
        )
        if not nodes:
            st.info("Nenhum relacionamento suficiente para montar o grafo nessa janela.")
        else:
            html = plot_entity_graph(nodes, edges)
            components.html(html, height=650, scrolling=True)
            st.caption("Arraste os nós para explorar. Passe o mouse para ver score/type. Valores baseados em coocorrências recentes.")
    else:
        case_limit = st.slider("Casos exibidos", 5, 50, 20, 5, key="case_graph_limit")
        case_rows = get_case_graph(case_limit)
        if not case_rows:
            st.info("Nenhum caso ativo para exibir na camada Playbook → Case → Entity.")
        else:
            html = plot_case_graph(case_rows)
            components.html(html, height=650, scrolling=True)
            st.caption("Nós roxos são Playbooks, laranja são Casos e azul são Entidades. Explore para entender o encadeamento de relacionamentos.")

# ───────── REVIEW TAB ───────────────────────────────────────────────────────────
with tabs[9]:
    st.subheader("Entity Review Queue")
    reviews = pd.DataFrame(
        run_query(
            """
    MATCH (s:Signal)-[:NEEDS_REVIEW]->(r:EntityReview)
    WHERE r.status = 'PENDING'
    RETURN r.review_id AS ReviewID,
           r.entity_type AS Type,
           r.raw_text AS RawText,
           r.normalized AS Candidate,
           r.reason AS Reason,
           r.confidence AS Confidence,
           r.created_at AS CreatedAt,
           s.uid AS SignalUID,
           s.timestamp AS SignalTs
    ORDER BY r.created_at DESC
    LIMIT 50
    """
        )
    )
    if reviews.empty:
        st.success("Sem pendências na fila de revisão. 🎉")
    else:
        for _, row in reviews.iterrows():
            title = f"{row['RawText']} → {row['Candidate']} ({row['Type']})"
            confidence = row.get("Confidence")
            if pd.isna(confidence):
                conf_text = "N/A"
            else:
                conf_text = f"{confidence:.2f}"
            with st.expander(title):
                st.write(
                    f"- Signal: `{row['SignalUID']}` ({row['SignalTs']})\n"
                    f"- Reason: {row['Reason']} | Confidence: {conf_text}\n"
                )
                if st.button("Marcar como resolvido", key=f"resolve-{row['ReviewID']}"):
                    resolve_review(row["ReviewID"])
                    st.success("Revisão marcada como resolvida.")
                    st.rerun()

# ───────── OPS TAB ──────────────────────────────────────────────────────────────
with tabs[10]:
    st.subheader("Operations Center")
    st.caption("Execute tarefas de backfill e playbooks diretamente do dashboard.")
    col_ops = st.columns(2)

    with col_ops[0]:
        if st.button("Backfill 30d + Playbooks"):
            with st.spinner("Executando backfill..."):
                run_backfill(driver, days=30, run_playbooks=True)
            st.success("Backfill completo. Playbooks disparados.")
            st.rerun()

    with col_ops[1]:
        if st.button("Executar Playbooks agora"):
            engine = PlaybookEngine(driver, PLAYBOOK_PATH)
            with st.spinner("Executando playbooks..."):
                engine.run()
            st.success("Playbooks executados.")

# ───────── HEALTH TAB ──────────────────────────────────────────────────────────
with tabs[11]:
    st.subheader("Observabilidade")
    metrics = run_query(
        """
        CALL dbms.listConfig()
        YIELD name, value
        WHERE name STARTS WITH 'metrics.'
        RETURN name, value
        """
    )
    st.caption("Health monitors")
    health_col1, health_col2, health_col3 = st.columns(3)
    health_col1.metric("Collector", collector_health.status, collector_health.last_error or "")
    health_col2.metric("Resolver", resolver_health.status, resolver_health.last_error or "")
    health_col3.metric("Playbooks", playbook_health.status, playbook_health.last_error or "")
    st.caption("Raw metrics snapshot")
    snapshot = global_metrics.snapshot()
    st.json(snapshot)
