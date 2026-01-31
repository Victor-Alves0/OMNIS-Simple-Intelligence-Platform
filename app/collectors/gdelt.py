import io
import math
import zipfile
import requests
import pandas as pd
import pycountry

from functools import lru_cache
from typing import Optional
from datetime import timezone

from tenacity import retry, stop_after_attempt, wait_exponential
from neo4j import GraphDatabase

from app.config.settings import CONFIG
from app.utils.omnis_logger import logger
from app.utils.normalizer import normalize_actor
from app.utils.vectorizer import vectorizer
from app.utils.severity_calculator import severity_calculator


GDELT_LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

GDELT_COLUMNS = [
    "GlobalEventID", "Day", "MonthYear", "Year", "FractionDate", "Actor1Code",
    "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode", "Actor1EthnicCode",
    "Actor1Religion1Code", "Actor1Religion2Code", "Actor1Type1Code",
    "Actor1Type2Code", "Actor1Type3Code", "Actor2Code", "Actor2Name",
    "Actor2CountryCode", "Actor2KnownGroupCode", "Actor2EthnicCode",
    "Actor2Religion1Code", "Actor2Religion2Code", "Actor2Type1Code",
    "Actor2Type2Code", "Actor2Type3Code", "IsRootEvent", "EventCode",
    "EventBaseCode", "EventRootCode", "QuadClass", "GoldsteinScale",
    "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_Fullname", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code", "Actor1Geo_Lat",
    "Actor1Geo_Long", "Actor1Geo_FeatureID", "Actor2Geo_Type",
    "Actor2Geo_Fullname", "Actor2Geo_CountryCode", "Actor2Geo_ADM1Code",
    "Actor2Geo_ADM2Code", "Actor2Geo_Lat", "Actor2Geo_Long",
    "Actor2Geo_FeatureID", "ActionGeo_Type", "ActionGeo_Fullname",
    "ActionGeo_CountryCode", "ActionGeo_ADM1Code", "ActionGeo_ADM2Code",
    "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL"
]


class GdeltIngestor:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver

        raw_map = CONFIG.get("rules", {}).get("actors", {}).get("normalization", {})
        self.actor_map = {k.lower(): v for k, v in raw_map.items()}

        self.trust_score = CONFIG.get("gdelt", {}).get("trust_score", 0.75)

    def ensure_constraints(self):
        queries = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Signal) REQUIRE s.uid IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Actor) REQUIRE a.uid IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (l:Location) REQUIRE l.uid IS UNIQUE",
        ]
        with self.driver.session() as session:
            for q in queries:
                session.run(q)
        logger.info("Constraints GDELT verificadas no Neo4j")

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=4, max=60))
    def get_latest_events_url(self) -> Optional[str]:
        r = requests.get(GDELT_LASTUPDATE_URL, timeout=15)
        r.raise_for_status()
        for line in r.text.strip().splitlines():
            if "export" in line and ".zip" in line:
                return line.split()[2]
        return None

    def _create_sentence_from_row(self, row) -> str:
        a1 = row.get("Actor1Name") or "um ator desconhecido"
        a2 = row.get("Actor2Name")
        loc = row.get("ActionGeo_Fullname") or "um local não especificado"

        if a1 and a2 and a2 != "UNKNOWN":
            return f"{a1} interagiu com {a2} em {loc}."
        return f"{a1} realizou uma ação em {loc}."

    def download_and_parse(self, url: str) -> Optional[pd.DataFrame]:
        logger.info(f"Baixando GDELT: {url}")
        r = requests.get(url, timeout=90)
        r.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(
                    f,
                    sep="\t",
                    header=None,
                    names=GDELT_COLUMNS,
                    dtype=str,
                    engine="python",
                    on_bad_lines="warn",
                )
        for c in ["GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df["DATEADDED"] = (
            pd.to_datetime(df["DATEADDED"], format="%Y%m%d%H%M%S", errors="coerce")
            .dt.tz_localize(timezone.utc)
        )
        @lru_cache(maxsize=256)
        def to_iso3(code):
            if not code or pd.isna(code):
                return "UNKNOWN"
            country = pycountry.countries.get(alpha_2=code)
            return country.alpha_3 if country else code.upper()

        for c in ["Actor1CountryCode", "Actor2CountryCode", "ActionGeo_CountryCode"]:
            df[c] = df[c].fillna("UNKNOWN").apply(to_iso3)

        for c in ["Actor1Name", "Actor2Name"]:
            df[c] = df[c].fillna("UNKNOWN").apply(normalize_actor)
            df[c] = df[c].apply(lambda x: self.actor_map.get(x.lower(), x))

        df = df.dropna(subset=["GlobalEventID", "DATEADDED"])
        df["GlobalEventID"] = df["GlobalEventID"].astype(str)

        for f in CONFIG.get("gdelt", {}).get("base_filters", []):
            try:
                df = df.query(f, engine="python")
            except Exception as e:
                logger.warning(f"Filtro inválido '{f}': {e}")

        logger.info(f"Eventos após filtros base: {len(df)}")

        df = df[
            (df["Actor1Name"] != "UNKNOWN")
            & (df["Actor2Name"] != "UNKNOWN")
            & (df["Actor1Name"] != df["Actor2Name"])
        ].copy()

        logger.info(f"Eventos válidos após validação estrutural: {len(df)}")
        return df

    def process_new_events(self, df: pd.DataFrame):
        if df.empty:
            return

        df["sentence"] = df.apply(self._create_sentence_from_row, axis=1)
        df["keywords"] = df["sentence"].apply(severity_calculator.extract_keywords)
        df["keywords"] = df["keywords"].apply(lambda kws: kws or [])
        df["embedding"] = df["sentence"].apply(vectorizer.embed)
        df = df[df["embedding"].apply(len) > 0]

        records = df.to_dict("records")
        batch_size = 500

        query = """
        UNWIND $batch AS row
        MATCH (sig:Signal {uid: 'gdelt_' + row.GlobalEventID})
        SET
            sig.content = row.sentence,
            sig.raw_severity = row.GoldsteinScale,
            sig.severity = row.scaled_severity,
            sig.source_trust = $trust,
            sig.category =
                CASE
                    WHEN row.GoldsteinScale <= -7 THEN 'CRITICAL_RISK'
                    WHEN row.GoldsteinScale <= -4 THEN 'HIGH_RISK'
                    WHEN row.GoldsteinScale < 0 THEN 'MEDIUM_RISK'
                    ELSE 'LOW_RISK'
                END,
            sig.embedding = row.embedding,
            sig.keywords = row.keywords,
            sig:Processed

        MERGE (a1:Actor {uid: row.Actor1Name}) SET a1.name = row.Actor1Name
        MERGE (sig)-[:MENTIONS]->(a1)

        MERGE (a2:Actor {uid: row.Actor2Name}) SET a2.name = row.Actor2Name
        MERGE (sig)-[:MENTIONS]->(a2)

        WITH sig, row
        WHERE row.ActionGeo_CountryCode <> 'UNKNOWN'
        MERGE (l:Location {uid: row.ActionGeo_CountryCode})
        SET l.name = row.ActionGeo_CountryCode
        MERGE (sig)-[:MENTIONS]->(l)
        """

        with self.driver.session() as session:
            for i in range(0, len(records), batch_size):
                batch = []
                for row in records[i:i + batch_size]:
                    try:
                        goldstein = float(row.get("GoldsteinScale") or 0.0)
                    except (TypeError, ValueError):
                        goldstein = 0.0
                    if math.isnan(goldstein):
                        goldstein = 0.0
                    scaled = max(0.0, min((goldstein / 10.0) * self.trust_score, 1.0))
                    row["scaled_severity"] = scaled
                    batch.append(row)
                session.run(
                    query,
                    batch=batch,
                    trust=self.trust_score,
                )

    def run(self, limit_to_recent: bool = True):
        self.ensure_constraints()

        url = self.get_latest_events_url()
        if not url:
            return

        df = self.download_and_parse(url)
        if df is None or df.empty:
            return

        if limit_to_recent:
            cutoff = pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(hours=24)
            df = df[df["DATEADDED"] >= cutoff]

        if df.empty:
            return

        events = [
            {**e, "DATEADDED": e["DATEADDED"].isoformat()}
            for e in df.to_dict("records")
        ]

        with self.driver.session() as session:
            result = session.run(
                """
                UNWIND $events AS event
                MERGE (src:Source {name: 'GDELT'})
                ON CREATE SET src.type = 'EventDatabase', src.trust_score = $trust

                MERGE (sig:Signal {uid: 'gdelt_' + event.GlobalEventID})
                ON CREATE SET
                    sig.timestamp = datetime(event.DATEADDED),
                    sig.url = event.SOURCEURL,
                    sig.created = true

                MERGE (src)-[:PUBLISHED]->(sig)

                WITH sig, event
                WHERE sig.created = true
                REMOVE sig.created
                RETURN event.GlobalEventID AS id
                """,
                events=events,
                trust=self.trust_score,
            )

            new_ids = {r["id"] for r in result}

        if not new_ids:
            logger.info("Nenhum evento GDELT novo.")
            return

        self.process_new_events(df[df["GlobalEventID"].isin(new_ids)])
        logger.info("Ingestão GDELT finalizada com sucesso.")
