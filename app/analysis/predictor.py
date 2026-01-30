from __future__ import annotations

from typing import Dict, List

from app.utils.translation import openrouter_client
from app.utils.omnis_logger import logger

SYSTEM_PROMPT = (
    "You are OMNIS Predictive Analyst, similar to NSA/Palantir forecasting systems. "
    "Use provided context (themes + signals) to estimate likelihoods, explain drivers, and suggest what-if considerations."
)


class PredictionEngine:
    def __init__(self, driver):
        self.driver = driver
        self.client = openrouter_client

    @property
    def available(self) -> bool:
        return self.client is not None

    def _fetch_context(self, hours: int, limit: int) -> Dict[str, List[Dict]]:
        with self.driver.session() as session:
            signals = session.run(
                """
                MATCH (s:Signal)<-[:PUBLISHED]-(src:Source)
                WHERE s.timestamp > datetime() - duration({hours:$hours})
                RETURN s.timestamp AS ts,
                       s.severity AS severity,
                       s.category AS category,
                       src.name AS source,
                       s.source_trust AS trust,
                       s.keywords AS keywords,
                       coalesce(s.translated_content, s.content) AS content
                ORDER BY s.severity DESC
                LIMIT $limit
                """,
                {"hours": hours, "limit": limit},
            ).data()

            themes = session.run(
                """
                MATCH (t:Theme)<-[:MATCHES_THEME]-(s:Signal)
                WHERE s.timestamp > datetime() - duration({hours:$hours})
                RETURN t.id AS theme,
                       count(*) AS volume,
                       round(avg(s.severity),2) AS severity,
                       max(s.timestamp) AS last_ts
                ORDER BY volume DESC
                LIMIT 8
                """,
                {"hours": hours},
            ).data()
        return {"signals": signals, "themes": themes}

    def _build_context_text(self, data: Dict[str, List[Dict]]) -> str:
        lines = ["Themes:"]
        if data["themes"]:
            for t in data["themes"]:
                lines.append(
                    f"- {t['theme']}: volume {t['volume']} | severity {t['severity']} | last {t['last_ts']}"
                )
        else:
            lines.append("- (no theme activity)")

        lines.append("\nSignals:")
        if data["signals"]:
            for s in data["signals"]:
                snippet = (s.get("content") or "").strip().replace("\n", " ")
                snippet = snippet[:220] + ("…" if len(snippet) > 220 else "")
                sev = s.get("severity")
                trust = s.get("trust")
                sev_val = 0.0 if sev is None else float(sev)
                trust_val = 0.0 if trust is None else float(trust)
                lines.append(
                    f"- [{s['ts']}] {s['source']} | cat={s['category']} | sev={sev_val:.2f} | trust={trust_val:.2f} -> {snippet}"
                )
        else:
            lines.append("- (no signals)")
        return "\n".join(lines)

    def ask(self, question: str, hours: int = 72, limit: int = 25) -> Dict[str, str]:
        if not self.available:
            raise RuntimeError("Prediction engine requires OPENROUTER_API_KEY configured.")
        context = self._fetch_context(hours, limit)
        context_text = self._build_context_text(context)
        user_prompt = (
            f"Question: {question}\n"
            f"Time window analyzed: last {hours} hours.\n"
            f"Context data (themes + signals):\n{context_text}\n"
            "Provide: 1) Probability estimate (0-100%) with justification; "
            "2) Key drivers and leading indicators; 3) Recommended 'what-if' actions or monitoring steps."
        )
        try:
            response = self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                temperature=0.2,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            logger.error("PredictionEngine | LLM call failed: %s", exc)
            raise

        answer = response.choices[0].message.content.strip()
        return {"answer": answer, "context": context_text}
