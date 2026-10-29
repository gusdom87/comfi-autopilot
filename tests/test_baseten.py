import io, json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-test.json")

import pytest

from jev.baseten_client import BasetenClient, MAX_TOKENS, TIMEOUT_SECS
from jev.client import MetaModelClient
from jev.config import Config
from jev.parse import extract_text, usage_cost
from jev.tiers import TierClients, decide
from jev.types import Category, FAIL_SAFE

OPEN = "meta-models/muse-spark-open-1026"
CONFIG = Config(baseten_api_key="test-key", baseten_base_url="https://baseten.test/v1")


class FakeResp:
    def __init__(self, body, status=200):
        self.status = status
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self):
        return self._raw
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def stub_transport(monkeypatch, reply):
    """Replace urlopen. reply is a FakeResp, an exception to raise, or a
    callable(payload) -> FakeResp. Returns the list of captured requests."""
    seen = []
    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data)
        seen.append({"url": req.full_url, "headers": dict(req.header_items()),
                     "payload": payload, "timeout": timeout})
        if isinstance(reply, Exception):
            raise reply
        return reply(payload) if callable(reply) else reply
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def chat_body(content, usage=True):
    body = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 412, "completion_tokens": 655, "total_tokens": 1067}
    return body


def verdict(cat, conf):
    return json.dumps({"category": cat, "confidence": conf, "route": "host", "reason": "r"})


# --- BasetenClient: request, translation, failure ---------------------------

def test_request_shape_and_translation(monkeypatch):
    seen = stub_transport(monkeypatch, FakeResp(chat_body(verdict("Act", 0.9))))
    resp = BasetenClient(CONFIG).complete(OPEN, "sys", "usr")
    assert resp == {"output_text": verdict("Act", 0.9),
                    "usage": {"input_tokens": 412, "output_tokens": 655}}
    (req,) = seen
    assert req["url"] == "https://baseten.test/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["payload"]["model"] == OPEN
    assert req["payload"]["messages"] == [{"role": "system", "content": "sys"},
                                          {"role": "user", "content": "usr"}]
    assert req["payload"]["max_tokens"] == MAX_TOKENS == 6000
    assert req["timeout"] == TIMEOUT_SECS == 120
    # parse.py reads the translated body unchanged, priced at par with Spark.
    assert extract_text(resp) == verdict("Act", 0.9)
    assert usage_cost(resp, OPEN) == pytest.approx(usage_cost(resp, "muse-spark-1.3"))
    assert usage_cost(resp, OPEN) == pytest.approx((412 * 1.25 + 655 * 4.25) / 1e6)


def test_missing_usage_is_omitted_and_cost_estimated(monkeypatch):
    stub_transport(monkeypatch, FakeResp(chat_body("hello", usage=False)))
    resp = BasetenClient(CONFIG).complete(OPEN, "s", "u")
    assert resp == {"output_text": "hello"}
    assert usage_cost(resp, OPEN, fallback_text_len=400) > 0


def test_null_content_is_empty_text_but_billed(monkeypatch):
    # All tokens went to reasoning: no text, but the usage is kept so the
    # spend guard records what the call cost.
    stub_transport(monkeypatch, FakeResp(chat_body(None)))
    resp = BasetenClient(CONFIG).complete(OPEN, "s", "u")
    assert resp["output_text"] == "" and resp["usage"]["output_tokens"] == 655


def test_no_key_returns_none_without_network(monkeypatch):
    seen = stub_transport(monkeypatch, FakeResp(chat_body("x")))
    assert BasetenClient(Config(baseten_api_key="")).complete(OPEN, "s", "u") is None
    assert seen == []


@pytest.mark.parametrize("reply", [
    urllib.error.HTTPError("https://baseten.test", 401, "Unauthorized", {}, io.BytesIO(b"")),
    urllib.error.URLError("connection refused"),
    TimeoutError("timed out"),
    ConnectionResetError("reset"),
    FakeResp(chat_body("x"), status=202),
    FakeResp(b"not json"),
    FakeResp(b"\xff\xfe"),
    FakeResp({"error": "bad request"}),
    FakeResp({"choices": []}),
    FakeResp({"choices": [{"message": {}}]}),
    FakeResp({"choices": [{"message": {"content": [{"type": "text"}]}}]}),
    FakeResp([1, 2, 3]),
    FakeResp("just a string"),
], ids=["http-401", "url-error", "timeout", "conn-reset", "status-202", "bad-json",
        "bad-utf8", "no-choices", "empty-choices", "no-content", "content-not-str",
        "list-body", "string-body"])
def test_failures_return_none_never_raise(monkeypatch, reply):
    stub_transport(monkeypatch, reply)
    assert BasetenClient(CONFIG).complete(OPEN, "s", "u") is None


# --- Routing: open model -> Baseten, Meta models -> Meta --------------------

class RecordingClient:
    """Canned Responses-API bodies in order; records which models it served."""
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.models = []
    def complete(self, model, system, user):
        self.models.append(model)
        text = self.bodies.pop(0) if self.bodies else None
        if text is None:
            return None
        return {"output_text": text, "usage": {"input_tokens": 100, "output_tokens": 20}}


def test_default_clients_route_by_model():
    config = Config()
    clients = TierClients(config)
    assert isinstance(clients.for_model(config.tier_open), BasetenClient)
    assert isinstance(clients.for_model(config.tier_fast), MetaModelClient)
    assert isinstance(clients.for_model(config.tier_deep), MetaModelClient)


def test_injected_client_serves_meta_tiers_only():
    config, meta = Config(), RecordingClient([])
    clients = TierClients(config, meta)
    assert clients.for_model(config.tier_fast) is meta
    assert clients.for_model(config.tier_deep) is meta
    assert isinstance(clients.for_model(config.tier_open), BasetenClient)
    override = RecordingClient([])
    assert TierClients(config, meta, override).for_model(config.tier_open) is override


def test_open_escalate_through_baseten_transport(monkeypatch):
    # End to end: with only a Meta client injected, the open first pass
    # still goes out over Baseten's chat-completions wire format.
    monkeypatch.setenv("BASETEN_API_KEY", "test-key")
    monkeypatch.setenv("BASETEN_API_BASE_URL", "https://baseten.test/v1")
    seen = stub_transport(monkeypatch, FakeResp(chat_body(verdict("Confirm", 0.9))))
    meta = RecordingClient([])
    d = decide("open_escalate", "Can we check in early?", {}, client=meta)
    assert d.category is Category.CONFIRM and d.tier_path == Config.from_env().tier_open
    assert [r["url"] for r in seen] == ["https://baseten.test/v1/chat/completions"]
    assert meta.models == []


def test_open_escalate_high_confidence_stands_and_open_tier_drafts():
    config = Config.from_env()
    meta = RecordingClient([])
    open_ = RecordingClient([verdict("Act", 0.9), "On it, sending someone now."])
    d = decide("open_escalate", "Water is leaking", {}, client=meta, open_client=open_)
    assert d.category is Category.ACT and d.tier_path == config.tier_open
    assert d.draft.startswith("On it")
    assert open_.models == [config.tier_open, config.tier_open] and meta.models == []


def test_open_escalate_low_confidence_escalates_to_wonder():
    config = Config.from_env()
    meta = RecordingClient([verdict("Act", 0.88), "On it, cleaner is headed over."])
    open_ = RecordingClient([verdict("Confirm", 0.4)])
    d = decide("open_escalate", "Water is leaking", {}, client=meta, open_client=open_)
    assert d.category is Category.ACT
    assert d.tier_path == config.tier_open + " -> " + config.tier_deep
    assert open_.models == [config.tier_open]
    assert meta.models == [config.tier_deep, config.tier_deep]   # re-decide, then draft
    assert d.draft.startswith("On it") and d.cost_usd > 0


def test_open_escalate_escalation_failure_fails_safe():
    config = Config.from_env()
    d = decide("open_escalate", "ok thanks", {},
               client=RecordingClient([None]), open_client=RecordingClient([verdict("Ignore", 0.3)]))
    assert d.category is Category.CONFIRM and d.confidence == 0.0
    assert d.reason == FAIL_SAFE.reason
    assert d.tier_path == config.tier_open + " (escalation failed; fail-safe)"
    assert d.cost_usd > 0          # the open call's spend is still reported


def test_open_escalate_dead_first_pass_fails_safe_without_escalating():
    meta = RecordingClient([verdict("Act", 0.9)])
    d = decide("open_escalate", "Hello?", {}, client=meta, open_client=RecordingClient([None]))
    assert d is FAIL_SAFE and meta.models == []


@pytest.mark.parametrize("policy", ["spark_only", "wonder_only", "escalate"])
def test_existing_policies_never_reach_the_open_tier(policy):
    meta = RecordingClient([verdict("Confirm", 0.4), verdict("Confirm", 0.5)])
    open_ = RecordingClient([])
    decide(policy, "Can we bring a second car?", {}, client=meta, open_client=open_)
    assert open_.models == [] and meta.models
