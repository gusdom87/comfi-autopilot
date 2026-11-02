"""Action-key classes (design package 1b; audited spec 2.1; package
Appendix B).

Every action key carries one fixed class, set here in code. Only a
code change Gus reviews can move one.

  A  internal: reading, triage, drafting, holds, logging, and alerts
     that only describe and only go to the operator. Runs every time.
  B  routine outward: changes nobody's day, no money, no safety. Runs
     without a per-send approval only on its own earned right (the
     ladder, jev.autonomy.right_for, on that exact key).
  C  day-changing: prepared in full, then waits for one approval.
  D  never automatic: money, access, safety lines. Always waits for a
     person, whatever the ledger says.

A key's class is the ceiling of its uses. message_cleaner is B when it
changes no schedule and C when it announces one, so the key is C; a
narrower B use would need its own key. A key not in the map is D.

The second permission source in the audited spec (an operator-
authorized template, armed by Gus with a 12-hour expiry) is not built:
the package defers it (R4) and arms no worker template before Nov 3.
Here, Class B runs alone only on an earned right, and no worker key has
a graded record yet, so every crew text waits for the operator.
"""
from types import MappingProxyType

from ..autonomy import P2, P3, right_for

A, B, C, D = "A", "B", "C", "D"

ROLES = ("cleaner", "handyman", "plumber", "electrician")

# Package Appendix B, plus the existing charge_guest.
CLASS_D_KEYS = frozenset({
    "charge_guest", "refund", "discount", "price_change", "approve_spend",
    "vendor_payment", "purchase_or_reimburse", "deposit_or_damage_claim",
    "airbnb_case_or_resolution", "assign_clean", "share_access",
    "safety_guest_line",
})

ACTION_CLASSES = MappingProxyType({
    "auto_close": A,
    # Only describes, only to the operator (package R6; Q6 is the
    # operator's confirmation). Ungating it in the plays engine is
    # package item 2b, not built here.
    "notify_operator": A,
    # Ceiling of its uses: a stay-change grant or a "ready early" text.
    "message_guest": C,
    # Crew keys (audited spec 2.3).
    "message_cleaner": C,
    "ask_status": B,
    "dispatch_vendor": C,
    "shift_cleaning": C,
    # The one-time worker notice and opt-in ask (package R12): always
    # the operator's approval.
    "crew_notice": C,
    **{key: D for key in CLASS_D_KEYS},
})

# The keys a crew text may carry.
CREW_ACTION_KEYS = frozenset({"message_cleaner", "ask_status",
                              "dispatch_vendor", "crew_notice"})


def class_for(key: str) -> str:
    return ACTION_CLASSES.get(key, D)


def may_run_without_approval(key: str, ledger: dict | None) -> tuple[bool, str, str]:
    """(allowed, class, why). Class A always; Class B only on an earned
    right for this exact key; Class C and D never."""
    cls = class_for(key)
    if cls == A:
        return True, cls, "internal or operator-only"
    if cls == C:
        return False, cls, "day-changing: prepared, waits for the operator's approval"
    if cls == D:
        return False, cls, "never automatic: waits for a person, whatever the ledger says"
    right = right_for(key, ledger or {})
    if right in (P2, P3):
        return True, cls, f"earned right {right} on {key}"
    return False, cls, f"{key} has no earned right ({right}): waits for the operator's approval"
