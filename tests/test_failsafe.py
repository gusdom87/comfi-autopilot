import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev.config import Config
from jev.client import MetaModelClient
from jev.tiers import decide
from jev.types import Category


def test_no_key_means_no_decision():
    client = MetaModelClient(Config(api_key="", base_url=""))
    assert client.complete("muse-spark", "s", "u") is None


def test_default_policy_fails_safe_to_confirm(monkeypatch):
    # Pinned to no key: with a real key exported this would make a
    # live, paid call instead of testing the fail-safe.
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    d = decide("escalate", "The lock code is not working", {})
    assert d.category is Category.CONFIRM
