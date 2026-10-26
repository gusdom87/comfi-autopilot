"""Tiering policies (the ablation arms). Each policy maps a message to a
Decision using zero, one, or two model calls:
  - spark_only:   fast tier only
  - wonder_only:  deep tier only
  - escalate:     fast tier first; low-confidence results escalate to deep tier
Prompt construction lives in prompts.py (added with the core port build).
"""
from .types import Decision, FAIL_SAFE


def decide(policy: str, message: str, context: dict) -> Decision:
    # Placeholder until the core port lands; the harness already exercises
    # this entry point, and the fail-safe is the honest default.
    return FAIL_SAFE
