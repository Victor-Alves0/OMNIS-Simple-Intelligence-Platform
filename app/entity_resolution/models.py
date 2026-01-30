from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

class EntityType(str, Enum):
    ACTOR = "ACTOR"
    LOCATION = "LOCATION"
    ORG = "ORG"

@dataclass(frozen=True)
class EntityMention:
    signal_uid: str
    raw_text: str
    normalized: str
    entity_type: EntityType
    confidence: float = 0.65
    source: Literal["NER", "COLLECTOR"] = "NER"
    needs_review: bool = False
    review_reason: Optional[str] = None
