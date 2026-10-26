"""Runtime config from environment. No secrets in code, ever."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    api_key: str = ""
    base_url: str = ""
    tier_fast: str = "muse-spark"
    tier_deep: str = "muse-wonder"
    daily_spend_cap_usd: float = 15.0
    request_timeout_secs: float = 20.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("META_MODEL_API_KEY", ""),
            base_url=os.environ.get("META_MODEL_API_BASE_URL", ""),
            tier_fast=os.environ.get("META_MODEL_TIER_FAST", "muse-spark"),
            tier_deep=os.environ.get("META_MODEL_TIER_DEEP", "muse-wonder"),
            daily_spend_cap_usd=float(os.environ.get("JEV_DAILY_SPEND_CAP_USD", "15")),
        )
