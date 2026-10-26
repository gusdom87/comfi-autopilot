"""Judge sandbox server (stdlib only). Serves the page and POST /triage.
All model calls happen server-side; the API key never reaches the browser.
Run: JEV_SANDBOX_PORT=8791 python3 sandbox/server.py"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev.autonomy import load_ledger, right_for  # noqa: E402
from jev.tiers import decide as _decide  # noqa: E402
from jev.types import FAIL_SAFE  # noqa: E402

HERE = os.path.dirname(__file__)


def handle_triage(payload: dict, decide_fn=_decide, ledger_path: str = "") -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        return {"error": "empty message"}
    context = {"property": str(payload.get("property", ""))}
    d = decide_fn("escalate", message, context)
    ledger = load_ledger(ledger_path or os.environ.get("JEV_LEDGER", ""))
    return {
        "category": d.category.value,
        "confidence": d.confidence,
        "draft": d.draft,
        "route": d.route,
        "reason": d.reason,
        "tier_path": d.tier_path,
        "cost_usd": round(d.cost_usd, 6),
        "autonomy_right": right_for(d.category.value, ledger),
        "failsafe": d.reason == FAIL_SAFE.reason,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with open(os.path.join(HERE, "index.html"), "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/triage":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        result = handle_triage(payload)
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("JEV_SANDBOX_PORT", "8791"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
