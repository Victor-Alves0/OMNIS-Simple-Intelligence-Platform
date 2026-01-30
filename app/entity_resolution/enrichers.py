from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional
import json
import urllib.error
import urllib.request
import requests

import pycountry

from app.utils.omnis_logger import logger


@dataclass
class GeoMetadata:
    iso3: str
    country: str
    region: str
    subregion: Optional[str] = None
    capital: Optional[str] = None
    population: Optional[int] = None


class GeoEnricher:
    """Enriquece entidades de localização com dados básicos."""

    def __init__(self):
        self._rest_cache: Dict[str, GeoMetadata] = {}

    def lookup_restcountries(self, iso3: str) -> Optional[GeoMetadata]:
        if iso3 in self._rest_cache:
            return self._rest_cache[iso3]
        url = f"https://restcountries.com/v3.1/alpha/{iso3.lower()}"
        for attempt in range(2):
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    return None
                country = data[0]
                meta = GeoMetadata(
                    iso3=iso3,
                    country=country.get("name", {}).get("common", iso3),
                    region=country.get("region") or "Unknown",
                    subregion=country.get("subregion"),
                    capital=", ".join(country.get("capital", [])) or None,
                    population=country.get("population"),
                )
                self._rest_cache[iso3] = meta
                return meta
            except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError):
                continue
        return None

    def lookup(self, iso3: str) -> Optional[GeoMetadata]:
        try:
            country = pycountry.countries.get(alpha_3=iso3)
        except KeyError:
            country = None
        if country:
            region = getattr(country, "region", None) or "Unknown"
            subregion = getattr(country, "subregion", None)
            return GeoMetadata(
                iso3=iso3,
                country=country.name,
                region=region,
                subregion=subregion,
            )
        return self.lookup_restcountries(iso3)

    def enrich(self, driver, entity_ids: Iterable[str]) -> None:
        iso_set = [eid.split("::", 1)[1] for eid in entity_ids if eid.startswith("location::")]
        if not iso_set:
            return
        updates = []
        for iso3 in iso_set:
            meta = self.lookup(iso3)
            if meta:
                updates.append(meta)
        if not updates:
            return

        query = """
        UNWIND $batch AS row
        MATCH (e:Entity {entity_id: row.entity_id})
        SET e.geo_country = row.country,
            e.geo_region = row.region,
            e.geo_subregion = row.subregion,
            e.geo_capital = row.capital,
            e.geo_population = row.population,
            e.geo_enriched_at = datetime()
        """
        payload = [
            {
                "entity_id": f"location::{meta.iso3}",
                "country": meta.country,
                "region": meta.region,
                "subregion": meta.subregion,
                "capital": meta.capital,
                "population": meta.population,
            }
            for meta in updates
        ]
        with driver.session() as session:
            session.run(query, batch=payload)
        logger.info("GeoEnricher | Atualizou %d entidades", len(payload))


class OrgEnricher:
    """Heurísticas simples para tipo/setor de organizações/atores."""

    KEYWORD_MAP: Dict[str, str] = {
        "BRIGADE": "Military",
        "MINISTRY": "Government",
        "CORP": "Private",
        "PRESS": "Media",
    }

    def infer_sector(self, canonical_name: str) -> Optional[str]:
        upper = canonical_name.upper()
        for keyword, sector in self.KEYWORD_MAP.items():
            if keyword in upper:
                return sector
        return None

    def enrich(self, driver, entity_ids: Iterable[str]) -> None:
        targets = [eid for eid in entity_ids if eid.startswith("actor::") or eid.startswith("org::")]
        if not targets:
            return

        query = """
        MATCH (e:Entity)
        WHERE e.entity_id IN $ids
        RETURN e.entity_id AS entity_id, e.canonical_name AS name
        """
        with driver.session() as session:
            rows = session.run(query, ids=targets)
            updates = []
            for row in rows:
                sector = self.infer_sector(row["name"] or "")
                if sector:
                    updates.append({"entity_id": row["entity_id"], "sector": sector})
        if not updates:
            return

        write = """
        UNWIND $batch AS row
        MATCH (e:Entity {entity_id: row.entity_id})
        SET e.sector = row.sector,
            e.org_enriched_at = datetime()
        """
        with driver.session() as session:
            session.run(write, batch=updates)
        logger.info("OrgEnricher | Atualizou %d entidades", len(updates))
