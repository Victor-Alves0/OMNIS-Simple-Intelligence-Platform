from __future__ import annotations

import re
from typing import Dict, List, Tuple

from app.config.settings import CONFIG


class SeverityCalculator:
    def __init__(self):
        rules = CONFIG.get("rules", {})
        self.concepts: Dict[str, Dict] = rules.get("concepts", {})
        self._compiled: Dict[str, List[Tuple[str, re.Pattern]]] = {}
        for name, data in self.concepts.items():
            patterns: List[Tuple[str, re.Pattern]] = []
            for kw_list in data.get("keywords", {}).values():
                for kw in kw_list:
                    if not kw:
                        continue
                    lowered = kw.lower()
                    boundary_pattern = r"(?<!\w)" + re.escape(lowered) + r"(?!\w)"
                    patterns.append((lowered, re.compile(boundary_pattern)))
            self._compiled[name] = patterns

    def score(self, text: str, trust: float) -> float:
        if not text:
            return max(0.0, min(trust * 0.4, 1.0))
        base = max(0.0, min(trust * 0.45, 1.0))
        lowered = text.lower()
        concept_boost = 0.0
        for name, patterns in self._compiled.items():
            if not patterns:
                continue
            if any(regex.search(lowered) for _, regex in patterns):
                concept_boost = max(concept_boost, self.concepts.get(name, {}).get("severity", 0.1))
        final = base + concept_boost * 0.5
        return max(0.0, min(final, 1.0))

    def extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        lowered = text.lower()
        matches: List[str] = []
        for patterns in self._compiled.values():
            for kw, regex in patterns:
                if regex.search(lowered):
                    matches.append(kw)
        seen = set()
        deduped = []
        for kw in matches:
            if kw in seen:
                continue
            seen.add(kw)
            deduped.append(kw)
        return deduped


severity_calculator = SeverityCalculator()
