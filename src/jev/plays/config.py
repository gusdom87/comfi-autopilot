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
    # Late checkout (play: late_checkout). Up to this many minutes past
    # standard checkout is free - the operator's standing rule is "up to
    # one hour free". Past it, the fee below applies as its own
    # charge_guest step: highest right, held for a human until earned,
    # exactly like the early check-in fee. The amount has no default:
    # None (JEV_LATE_CHECKOUT_FEE_USD unset) means the operator sets it,
    # and the step is drafted with the amount to be set.
    late_checkout_free_minutes: int = 60
    late_checkout_fee_usd: float | None = None

    @classmethod
    def from_env(cls) -> "PlaysConfig":
        late_fee = os.environ.get("JEV_LATE_CHECKOUT_FEE_USD", "").strip()
        return cls(
            early_checkin_fee_usd=float(os.environ.get("JEV_EARLY_CHECKIN_FEE_USD", "25")),
            late_checkout_fee_usd=float(late_fee) if late_fee else None,
        )
