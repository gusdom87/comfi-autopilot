"""Core types for the triage pipeline."""
from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    CONFIRM = "Confirm"   # host should eyeball and approve before anything sends
    ACT = "Act"           # time-sensitive: surface immediately with a draft
    IGNORE = "Ignore"     # noise: receipts, duplicates, FYI with no ask


@dataclass
class Decision:
    category: Category
    confidence: float            # 0.0 to 1.0
    draft: str = ""              # suggested reply in the operator's voice
    route: str = ""              # who/what acts next (host, cleaner, no one)
    reason: str = ""             # one-line justification
    tier_path: str = ""          # which model tier(s) produced this decision
    cost_usd: float = 0.0
    raw: dict = field(default_factory=dict)


FAIL_SAFE = Decision(
    category=Category.CONFIRM,
    confidence=0.0,
    reason="Fail-safe: model unavailable or output unparsable; a human decides.",
)
