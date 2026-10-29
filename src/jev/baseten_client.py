"""Baseten client for Muse Spark Open. Same contract as MetaModelClient:
complete() returns a body, or None on any failure, and callers treat None
as 'no decision' and fall back to the human (Category.CONFIRM).

Baseten speaks OpenAI Chat Completions; the rest of the pipeline reads the
Responses API shape. complete() translates, so parse.extract_text and
parse.usage_cost work unchanged. Pinned in docs/API-NOTES.md.
"""
import http.client
import json
import urllib.request
import urllib.error

from .config import Config

# The model always reasons before it answers, and reasoning shares the
# completion budget, so both limits sit well above the Meta client's. At
# 2000 tokens, 12 of 101 eval messages came back with empty content (every
# token spent reasoning, no answer); 6000 leaves the answer room after it.
MAX_TOKENS = 6000
TIMEOUT_SECS = 120.0


class BasetenClient:
    def __init__(self, config: Config):
        self.config = config

    def complete(self, model: str, system: str, user: str) -> dict | None:
        """One completion call. Returns a Responses-shaped body, or None on any failure."""
        if not self.config.baseten_api_key or not self.config.baseten_base_url:
            return None
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": MAX_TOKENS,
        }
        req = urllib.request.Request(
            self.config.baseten_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.baseten_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                if resp.status != 200:
                    return None
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                ValueError, OSError):
            return None
        return to_responses_shape(body)


def to_responses_shape(body) -> dict | None:
    """Chat Completions body -> {"output_text", "usage"?} as parse.py reads it.
    None if the body has no assistant message where one should be."""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if content is None:
        # Every token went to reasoning. The call was still billed, so the
        # body comes back (the spend guard records it) and parses to no
        # decision downstream.
        content = ""
    if not isinstance(content, str):
        return None
    out = {"output_text": content}
    usage = body.get("usage")
    if isinstance(usage, dict):
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(tin, int) and isinstance(tout, int):
            out["usage"] = {"input_tokens": tin, "output_tokens": tout}
    return out
