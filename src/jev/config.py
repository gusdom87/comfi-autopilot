"""Runtime config from environment. No secrets in code, ever."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.meta.ai/v1"
    tier_fast: str = "muse-spark-1.3"
    tier_deep: str = "muse-wonder-1.0"
    daily_spend_cap_usd: float = 15.0
    # 75 s: the Oct 27 ablation showed Wonder's reasoning latency routinely
    # exceeds 20 s (27 of 83 calls timed out and fell back to Confirm).
    # The fail-safe makes a long ceiling safe: worst case is a Confirm.
    request_timeout_secs: float = 75.0
    # Muse Spark Open, served by Baseten (OpenAI-compatible). The hackathon
    # key works only with this exact model name; see docs/API-NOTES.md.
    baseten_api_key: str = ""
    baseten_base_url: str = "https://inference.baseten.co/v1"
    tier_open: str = "meta-models/muse-spark-open-1026"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("META_MODEL_API_KEY", ""),
            base_url=os.environ.get("META_MODEL_API_BASE_URL", "https://api.meta.ai/v1"),
            tier_fast=os.environ.get("META_MODEL_TIER_FAST", "muse-spark-1.3"),
            tier_deep=os.environ.get("META_MODEL_TIER_DEEP", "muse-wonder-1.0"),
            daily_spend_cap_usd=float(os.environ.get("JEV_DAILY_SPEND_CAP_USD", "15")),
            baseten_api_key=os.environ.get("BASETEN_API_KEY", ""),
            baseten_base_url=os.environ.get("BASETEN_API_BASE_URL",
                                            "https://inference.baseten.co/v1"),
            tier_open=os.environ.get("BASETEN_MODEL_OPEN", "meta-models/muse-spark-open-1026"),
        )
