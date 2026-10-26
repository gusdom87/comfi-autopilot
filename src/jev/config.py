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
    request_timeout_secs: float = 20.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("META_MODEL_API_KEY", ""),
            base_url=os.environ.get("META_MODEL_API_BASE_URL", "https://api.meta.ai/v1"),
            tier_fast=os.environ.get("META_MODEL_TIER_FAST", "muse-spark-1.3"),
            tier_deep=os.environ.get("META_MODEL_TIER_DEEP", "muse-wonder-1.0"),
            daily_spend_cap_usd=float(os.environ.get("JEV_DAILY_SPEND_CAP_USD", "15")),
        )
