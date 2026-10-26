"""Meta Model API client. FAIL-SAFE BY DESIGN: any transport error, timeout,
or unparsable response returns None, and callers must treat None as
'no decision' and fall back to the human (Category.CONFIRM).

ADAPTER NOTE: endpoint path, auth header, and model ID spellings are pinned
in docs/API-NOTES.md from the official quickstart before the first live call.
"""
import json
import urllib.request
import urllib.error

from .config import Config


class MetaModelClient:
    def __init__(self, config: Config):
        self.config = config

    def complete(self, model: str, system: str, user: str) -> dict | None:
        """One completion call. Returns the parsed JSON body, or None on any failure."""
        if not self.config.api_key or not self.config.base_url:
            return None
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.request_timeout_secs) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None
