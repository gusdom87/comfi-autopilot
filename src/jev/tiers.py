"""Tiering policies (the ablation arms):
  spark_only  - fast tier decides, fast tier drafts
  wonder_only - deep tier decides and drafts
  escalate    - fast tier decides; below the confidence threshold the deep
                tier re-decides and drafts
Every path fails safe: no usable model output -> FAIL_SAFE (Confirm).
"""
import os
from dataclasses import replace

from .client import MetaModelClient
from .config import Config
from .parse import extract_text, parse_decision, usage_cost
from .prompts import DRAFT_SYSTEM, TRIAGE_SYSTEM, build_draft_user, build_triage_user
from .spend import SpendGuard
from .types import Category, Decision, FAIL_SAFE

ESCALATION_THRESHOLD = 0.75


def _guard(config: Config) -> SpendGuard:
    path = os.environ.get("JEV_SPEND_STATE", ".decision-log/spend.json")
    return SpendGuard(path, config.daily_spend_cap_usd)


def _call(client, guard, model, system, user):
    """One guarded model call. Returns (Decision|None, cost)."""
    if not guard.allowed():
        return None, 0.0
    resp = client.complete(model, system, user)
    if resp is None:
        return None, 0.0
    cost = usage_cost(resp, model, fallback_text_len=len(user))
    guard.record(cost)
    decision = parse_decision(extract_text(resp), tier_path=model, cost_usd=cost)
    return decision, cost


def _draft(client, guard, model, decision, message, context, spent):
    if decision.category is not Category.ACT:
        return decision
    if not guard.allowed():
        return decision
    resp = client.complete(model, DRAFT_SYSTEM, build_draft_user(message, context))
    if resp is None:
        return decision
    cost = usage_cost(resp, model, fallback_text_len=len(message))
    guard.record(cost)
    text = extract_text(resp).strip()
    if text:
        decision.draft = text
        decision.cost_usd = spent + cost
    return decision


def decide(policy: str, message: str, context: dict, client=None, examples=None) -> Decision:
    config = Config.from_env()
    client = client or MetaModelClient(config)
    guard = _guard(config)
    examples = examples or []
    user = build_triage_user(message, context, examples)

    if policy == "spark_only":
        d, spent = _call(client, guard, config.tier_fast, TRIAGE_SYSTEM, user)
        if d is None:
            return FAIL_SAFE
        return _draft(client, guard, config.tier_fast, d, message, context, spent)

    if policy == "wonder_only":
        d, spent = _call(client, guard, config.tier_deep, TRIAGE_SYSTEM, user)
        if d is None:
            return FAIL_SAFE
        return _draft(client, guard, config.tier_deep, d, message, context, spent)

    if policy == "escalate":
        d, spent = _call(client, guard, config.tier_fast, TRIAGE_SYSTEM, user)
        if d is None:
            return FAIL_SAFE
        if d.confidence >= ESCALATION_THRESHOLD:
            d.tier_path = config.tier_fast
            return _draft(client, guard, config.tier_fast, d, message, context, spent)
        d2, spent2 = _call(client, guard, config.tier_deep, TRIAGE_SYSTEM, user)
        if d2 is None:
            # The fast verdict was already judged too uncertain to stand
            # alone, so it is not handed back as the decision: fail safe,
            # keeping the honest tier path and what the fast call cost.
            return replace(FAIL_SAFE,
                           tier_path=config.tier_fast + " (escalation failed; fail-safe)",
                           cost_usd=spent)
        d2.tier_path = config.tier_fast + " -> " + config.tier_deep
        d2.cost_usd = spent + spent2
        return _draft(client, guard, config.tier_deep, d2, message, context, spent + spent2)

    return FAIL_SAFE
