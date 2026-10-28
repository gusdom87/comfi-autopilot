"""First-live-call check, run once the API key exists. Sends ONE staged
message to each tier and reports category, latency, and cost. This is the
check that settles whether muse-wonder-1.0 answers on this account.
Usage: python3 scripts/first_call_check.py"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev.client import MetaModelClient
from jev.config import Config
from jev.parse import extract_text, parse_decision, usage_cost
from jev.prompts import TRIAGE_SYSTEM, build_triage_user

STAGED = "Hi, we just got back and the door code is not working. We are standing outside with our bags."


def main() -> int:
    config = Config.from_env()
    if not config.api_key:
        print("No META_MODEL_API_KEY in env. Stop: the key comes through the Secure Vault flow.")
        return 2
    client = MetaModelClient(config)
    user = build_triage_user(STAGED, {"property": "Cedar Hollow Studio 2A"}, [])
    for model in (config.tier_fast, config.tier_deep):
        start = time.time()
        resp = client.complete(model, TRIAGE_SYSTEM, user)
        latency = time.time() - start
        if resp is None:
            print(f"{model}: NO RESPONSE (transport error or timeout)")
            continue
        d = parse_decision(extract_text(resp), tier_path=model,
                           cost_usd=usage_cost(resp, model, fallback_text_len=len(user)))
        if d is None:
            print(f"{model}: responded in {latency:.1f}s but output did not parse")
        else:
            print(f"{model}: {d.category.value} @ {d.confidence:.2f} in {latency:.1f}s, "
                  f"cost ${d.cost_usd:.4f} - {d.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
