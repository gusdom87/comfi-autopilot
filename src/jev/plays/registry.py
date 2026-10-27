"""The play registry: which plays exist, and which one a triage
Decision fires. Plays register themselves on import of this package."""
from ..types import Decision
from .engine import Play

REGISTRY: dict[str, Play] = {}


def register(play: Play) -> Play:
    REGISTRY[play.name] = play
    return play


def get(name: str) -> Play | None:
    return REGISTRY.get(name)


def all_plays() -> list[Play]:
    return list(REGISTRY.values())


def match(decision: Decision, message: str, context: dict | None = None) -> Play | None:
    """First registered play whose trigger fires for this decision."""
    context = context or {}
    for play in REGISTRY.values():
        if play.trigger(decision, message, context):
            return play
    return None
