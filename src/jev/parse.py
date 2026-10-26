"""Strict parsing of model output. Anything unexpected returns None and the
caller falls back to the fail-safe decision."""
import json

from .types import Category, Decision, FAIL_SAFE

_ROUTES = {"host", "cleaner", "none"}


def parse_decision(text: str, tier_path: str, cost_usd: float = 0.0) -> Decision | None:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        category = Category(obj["category"])
        confidence = float(obj["confidence"])
        if not 0.0 <= confidence <= 1.0:
            return None
        route = str(obj.get("route", "host")).lower()
        if route not in _ROUTES:
            route = "host"
        return Decision(
            category=category,
            confidence=confidence,
            draft=str(obj.get("draft", "")),
            route=route,
            reason=str(obj.get("reason", "")),
            tier_path=tier_path,
            cost_usd=cost_usd,
            raw=obj,
        )
    except (ValueError, KeyError, TypeError):
        return None


def extract_text(resp: dict | None) -> str:
    """Pull the assistant text out of a Responses API body; '' if absent."""
    if not resp:
        return ""
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    chunks = []
    for item in resp.get("output", []) or []:
        for part in item.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                chunks.append(part.get("text", ""))
    return "".join(chunks)


def usage_cost(resp: dict | None, model: str, fallback_text_len: int = 0) -> float:
    """Cost in USD from the response usage block when present, else a rough
    character-based estimate. Prices pinned in docs/API-NOTES.md."""
    prices = {  # (input, output) per 1M tokens
        "muse-spark-1.3": (1.25, 4.25),
        "muse-wonder-1.0": (4.00, 20.00),
    }
    pin, pout = prices.get(model, (1.25, 4.25))
    usage = (resp or {}).get("usage") or {}
    tin = usage.get("input_tokens")
    tout = usage.get("output_tokens")
    if tin is None or tout is None:
        est = max(fallback_text_len // 4, 1)
        tin, tout = est, est // 2
    return (tin * pin + tout * pout) / 1_000_000
