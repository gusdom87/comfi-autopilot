"""Config for the plays engine. Kept separate from jev.config (model
settings) so play knobs never touch the triage pipeline's config."""
import os
from dataclasses import dataclass


@dataclass
class PlaysConfig:
    # Early check-in fee charged to the guest when an early arrival is
    # granted (play: early_checkin). Charging is the highest-stakes action
    # in the ladder: always held for a human until the right is earned.
    early_checkin_fee_usd: float = 25.0
    # If the unit's expected ready time misses the guest's requested time
    # by no more than this many minutes, the play goes conditional (ask
    # the cleaner, answer when they report done) instead of denying.
    conditional_window_minutes: int = 90

    @classmethod
    def from_env(cls) -> "PlaysConfig":
        return cls(
            early_checkin_fee_usd=float(os.environ.get("JEV_EARLY_CHECKIN_FEE_USD", "25")),
        )
