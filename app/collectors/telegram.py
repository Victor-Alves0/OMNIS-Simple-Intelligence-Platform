import re
import asyncio
from pathlib import Path
from datetime import timezone
from typing import Dict

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from neo4j import GraphDatabase

from app.config.settings import (
    CONFIG,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_SESSION_NAME,
    TELEGRAM_SESSION_STRING,
    TELEGRAM_SESSIONS_DIR,
)
from app.utils.omnis_logger import logger
from app.utils.translation import translate_to_pt_br
from app.utils.normalizer import clean_text, normalize_timestamp
from app.utils.vectorizer import vectorizer
from app.utils.severity_calculator import severity_calculator
from app.utils.metrics import global_metrics, collector_health

class TelegramHumintCollector:
    def __init__(self, driver: GraphDatabase.driver):
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            raise ValueError("Credenciais do Telegram ausentes")

        session_str = TELEGRAM_SESSION_STRING
        if session_str:
            session = StringSession(session_str)
        else:
            sessions_dir = Path(TELEGRAM_SESSIONS_DIR)
            sessions_dir.mkdir(parents=True, exist_ok=True)
            session = str(sessions_dir / TELEGRAM_SESSION_NAME)

        self.client = TelegramClient(
            session,
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH
        )

        self.driver = driver

        self.target_channels_config = CONFIG.get("telegram", {}).get("channels", [])
        self.channel_trust_map: Dict[int, float] = {}
        self.sem = asyncio.Semaphore(5)

    async def process_new_signal(
        self,
        uid: str,
        channel_name: str,
        raw_text: str,
        timestamp,
        trust_score: float,
    ):
        async with self.sem:
            try:
                if re.search(r"[а-яА-Я]", raw_text):
                    lang = "ru"
                elif re.search(r"[\u0600-\u06FF]", raw_text):
                    lang = "ar"
                elif re.search(r"[一-龥]", raw_text):
                    lang = "zh"
                else:
                    lang = "en"

                translated = translate_to_pt_br(raw_text, source_lang=lang)
                if not translated:
                    return

                cleaned = clean_text(translated)

                embedding = vectorizer.embed(cleaned)
                if not embedding:
                    return

                severity = severity_calculator.score(cleaned, trust_score)
                raw_severity = severity
                keywords = severity_calculator.extract_keywords(cleaned)

                if severity >= 0.7:
                    category = "HIGH_RISK"
                elif severity >= 0.4:
                    category = "MEDIUM_RISK"
                else:
                    category = "LOW_RISK"

                await asyncio.to_thread(
                    self._persist_processed_signal,
                    uid,
                    cleaned[:3000],
                    severity,
                    raw_severity,
                    trust_score,
                    category,
                    lang,
                    embedding,
                    keywords,
                )

                log = logger.info if category != "LOW_RISK" else logger.debug
                log(
                    f"Telegram | {category} | trust={trust_score:.2f} | @{channel_name}"
                )

            except Exception:
                logger.error(f"Erro ao processar sinal Telegram {uid}", exc_info=True)

    async def ingest_signal(self, chat_id: int, channel_name: str, msg, trust: float):
        raw_text = msg.message
        if not raw_text or len(raw_text) < 20:
            return

        uid = f"tg_{chat_id}_{msg.id}"
        timestamp = normalize_timestamp(msg.date.astimezone(timezone.utc))

        with self.driver.session() as session:
            result = session.run(
                """
                MERGE (src:Source {name: $channel, type: 'Telegram'})
                SET src.trust_score = $trust

                MERGE (sig:Signal {uid: $uid})
                ON CREATE SET
                    sig.original_content = $content,
                    sig.timestamp = datetime($ts),
                    sig.created = true

                MERGE (src)-[:PUBLISHED]->(sig)

                WITH sig
                WHERE sig.created = true
                REMOVE sig.created
                RETURN true AS is_new
                """,
                {
                    "uid": uid,
                    "channel": f"@{channel_name}",
                    "content": raw_text[:3000],
                    "ts": timestamp,
                    "trust": trust,
                },
            ).single()

        if result and result["is_new"]:
            asyncio.create_task(
                self.process_new_signal(
                    uid, channel_name, raw_text, timestamp, trust
                )
            )
    async def run(self):
        logger.info("Iniciando Telegram Collector...")
        await self.client.start()

        valid_entities = []
        resolved_names = {}

        for ch in self.target_channels_config:
            name = ch.get("name")
            trust = ch.get("trust_score", 0.5)

            try:
                entity = await self.client.get_input_entity(name)
                chat = await self.client.get_entity(entity)

                chat_id = (
                    getattr(entity, "channel_id", None)
                    or getattr(entity, "user_id", None)
                    or chat.id
                )
                chat_name = chat.username or chat.title or name

                valid_entities.append(entity)
                resolved_names[chat_id] = chat_name
                self.channel_trust_map[chat_id] = trust

                logger.info(f"Canal OK: @{chat_name} (trust={trust})")

            except Exception as e:
                logger.warning(f"Falha ao resolver {name}: {e}")

        if not valid_entities:
            logger.error("Nenhum canal válido configurado.")
            collector_health.mark_error("Telegram sem canais válidos")
            return

        @self.client.on(events.NewMessage(chats=valid_entities))
        async def handler(event):
            try:
                peer = event.message.peer_id
                chat_id = getattr(peer, "channel_id", None) or getattr(
                    peer, "user_id", None
                )

                await self.ingest_signal(
                    chat_id,
                    resolved_names.get(chat_id, "unknown"),
                    event.message,
                    self.channel_trust_map.get(chat_id, 0.5),
                )
            except Exception:
                logger.error("Erro no handler Telegram", exc_info=True)

        logger.info(f"Escutando {len(valid_entities)} canais Telegram...")
        global_metrics.inc("telegram_sessions")
        collector_health.mark_success()
        await self.client.run_until_disconnected()

    def _persist_processed_signal(
        self,
        uid: str,
        content: str,
        severity: float,
        raw_severity: float,
        trust_score: float,
        category: str,
        lang: str,
        embedding,
        keywords,
    ):
        query = """
        MATCH (sig:Signal {uid: $uid})
        SET
            sig.translated_content = $content,
            sig.severity = $severity,
            sig.raw_severity = $raw_severity,
            sig.source_trust = $trust,
            sig.category = $category,
            sig.language = $lang,
            sig.embedding = $embedding,
            sig.keywords = $keywords,
            sig:Processed
        """
        with self.driver.session() as session:
            session.run(
                query,
                {
                    "uid": uid,
                    "content": content,
                    "severity": severity,
                    "raw_severity": raw_severity,
                    "trust": trust_score,
                    "category": category,
                    "lang": lang,
                    "embedding": embedding,
                    "keywords": keywords,
                },
            )

