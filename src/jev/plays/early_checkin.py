"""Play: EARLY CHECK-IN.

A guest asks to arrive before standard check-in. The brain does not
just answer - it checks reality first (is there a same-day turnover?
who cleans? when will the unit actually be ready?), then runs the
play: reply to the guest, charge the early check-in fee (highest
right: held for a human until earned), and when the answer depends on
the cleaner, ping the cleaner and let the guest know the moment the
cleaner reports done.

Outcomes:
  grant       unit ready by the requested time
  conditional expected ready time misses the request by <= the
              conditional window: ping the cleaner, wait for their
              done-event, then tell the guest the place is ready
  deny        the unit cannot be ready in time; honest reply, no
              charge, no cleaner ping
"""
import re

from ..types import Category, Decision
from .context import fmt_time, to_minutes
from .engine import Play, PlayRun, Step

_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
_REL_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*hours?\s+(?:early|earlier)", re.I)
_ONE_HOUR_RE = re.compile(r"\ban?\s+hour\s+(?:early|earlier)", re.I)
_CUE_RE = re.compile(
    r"(check[\s-]?in|arriv|get in|get there|come by|drop (?:our|my) bags)",
    re.I)


def extract_requested_minutes(message: str, standard_checkin: str) -> int | None:
    """The arrival time the guest is asking for, minutes since midnight.
    Handles 'check in at 1pm', 'arrive by 2:30 PM', 'an hour early',
    '2 hours earlier'. None when the guest named no usable time."""
    m = _TIME_RE.search(message)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower().startswith("p"):
            hour += 12
        return hour * 60 + int(m.group(2) or 0)
    m = _REL_HOURS_RE.search(message)
    if m:
        return to_minutes(standard_checkin) - int(float(m.group(1)) * 60)
    if _ONE_HOUR_RE.search(message):
        return to_minutes(standard_checkin) - 60
    return None


def _effect(step: Step) -> dict:
    """What an action step's disposition means, stated honestly: at
    P2/P3 the step executes through a SIMULATED effector (no real rails
    wired in this build); at P1 nothing executes, the artifact waits."""
    if step.disposition == "execute":
        return {"executed": True, "channel": "simulated"}
    return {"executed": False, "held_for_approval": True}


class EarlyCheckIn(Play):
    name = "early_checkin"
    description = "Guest asks for early check-in: check reality, reply, " \
                  "charge the fee, coordinate the cleaner."

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        if decision is None or decision.category is Category.IGNORE:
            return False
        return bool(_CUE_RE.search(message)) and (
            extract_requested_minutes(message, "16:00") is not None
            or "early" in message.lower())

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("extract_request", "internal", run=self._extract),
            Step("reality_check", "internal", run=self._reality),
            Step("guest_reply", "action", run=self._guest_reply,
                 action_key="message_guest"),
            Step("early_checkin_charge", "action", run=self._charge,
                 action_key="charge_guest",
                 skip_if=lambda r: r.data.get("outcome") == "deny"),
            Step("cleaner_ping", "action", run=self._cleaner_ping,
                 action_key="message_cleaner",
                 skip_if=lambda r: r.data.get("outcome") != "conditional"),
            Step("wait_cleaner_done", "wait", event="cleaner_done",
                 skip_if=lambda r: r.data.get("outcome") != "conditional"),
            Step("guest_ready_update", "action", run=self._ready_update,
                 action_key="message_guest",
                 skip_if=lambda r: r.data.get("outcome") != "conditional"),
        ]

    # -- steps ---------------------------------------------------------
    def _extract(self, engine, run: PlayRun, step: Step) -> None:
        prop = engine.context.get_property(run.property_id)
        standard = prop.standard_checkin if prop else "16:00"
        req = extract_requested_minutes(run.message, standard)
        if req is None:
            step.status = "failed"
            step.result = {"error": "no usable arrival time in message"}
            run.hold_reason = "could not tell what time the guest wants"
            return
        run.data["requested_minutes"] = req
        step.result = {"requested": fmt_time(req)}

    def _reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        ready = ctx.ready_time_minutes(run.property_id, run.date)
        if prop is None or ready is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "no calendar/cleaner data for this property"
            return
        req = run.data["requested_minutes"]
        turn = ctx.get_turnover(run.property_id, run.date)
        if req >= ready:
            outcome = "grant"
        elif ready - req <= engine.config.conditional_window_minutes:
            outcome = "conditional"
        else:
            outcome = "deny"
        run.data.update({"outcome": outcome, "ready_minutes": ready,
                         "turnover": turn})
        step.result = {
            "outcome": outcome,
            "ready_at": fmt_time(ready),
            "same_day_turnover": turn is not None,
            "cleaner": turn.cleaner_name if turn else "",
        }

    def _guest_reply(self, engine, run: PlayRun, step: Step) -> None:
        req = fmt_time(run.data["requested_minutes"])
        ready = fmt_time(run.data["ready_minutes"])
        outcome = run.data["outcome"]
        fee = engine.config.early_checkin_fee_usd
        if outcome == "grant":
            text = (f"Hi, good news - {req} works for check-in. The place "
                    f"will be ready for you then. The early check-in fee "
                    f"is ${fee:.0f}, and it will show as a separate request.")
        elif outcome == "conditional":
            text = (f"Hi, that day depends on our cleaner - there is a "
                    f"turnover right before you, and the place is expected "
                    f"ready around {ready}. I have asked her to confirm "
                    f"the moment she finishes, and I will message you right "
                    f"away. If she is done by {req}, you are in then; "
                    f"either way it is yours from 4:00 PM.")
        else:
            text = (f"Hi, I am sorry - that day there is a turnover right "
                    f"before you and the place will not be ready by {req}. "
                    f"Check-in from 4:00 PM is certain, and I will let you "
                    f"know the moment it is ready if that ends up sooner.")
        step.result = {"draft": text, **_effect(step)}

    def _charge(self, engine, run: PlayRun, step: Step) -> None:
        fee = engine.config.early_checkin_fee_usd
        step.result = {
            "charge": {"amount_usd": fee,
                       "description": "Early check-in fee",
                       "property_id": run.property_id,
                       "date": run.date},
            **_effect(step),
        }

    def _cleaner_ping(self, engine, run: PlayRun, step: Step) -> None:
        turn = run.data["turnover"]
        req = fmt_time(run.data["requested_minutes"])
        prop = engine.context.get_property(run.property_id)
        # The operator's rule for cleaner texts: never the cleaner's
        # name in the message. The name rides along for the operator.
        text = (f"Hi, quick one on {prop.name} for "
                f"{run.date}: the arriving guest is hoping to get in at "
                f"{req}. No rush and no pressure - just reply done here "
                f"the moment you finish, and I will let them know. Thanks.")
        step.result = {"draft": text, "cleaner": turn.cleaner_name,
                       **_effect(step)}

    def _ready_update(self, engine, run: PlayRun, step: Step) -> None:
        payload = run.data.get("event:cleaner_done", {})
        finished = payload.get("finished_at", "")
        when = f" (she finished at {finished})" if finished else ""
        text = (f"Good news - the cleaner just finished{when}, so the "
                f"place is ready for you now. Your door code is in your "
                f"check-in message. See you soon.")
        step.result = {"draft": text, **_effect(step)}
