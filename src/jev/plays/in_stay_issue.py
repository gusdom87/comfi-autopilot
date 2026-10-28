"""Play: IN-STAY ISSUES with handyman dispatch (Play 4).

A guest who is already in the home reports something broken: the
door will not unlock, there is a leak, the water is out. Triage
calls it Act; this play is what Act turns into. It identifies the
issue from the message with plain pattern matching (no model call -
the triage Decision already spent the model's judgment getting
here), checks reality (who is actually staying in the home? which
roster vendor covers this?), drafts the guest reply in the
operator's voice, and composes the dispatch to the handyman. The
dispatch send is gated at dispatch_vendor, middle of the ladder:
held for a human at P1 with the artifact preserved, executed
through the simulated effector when the right is earned.

Severity branch: safety-class issues (a gas smell, smoke or a CO
alarm, active flooding, no heat) skip self-help entirely - the
guest gets safety guidance instead - and the run ALSO flags the
operator in the same run, through Play 7's notify_operator gate.
Alarms do not wait for the ladder to be the story.

Follow-ups ride the vendor's events, the way Play 2 rides the
cleaner's: a vendor_eta event ("help is on the way, arriving around
X") and a vendor_done event ("the fix is done - check it works")
each produce a guest update through the message_guest gate. If the
done report arrives without an ETA first, the ETA steps skip and
the done update still runs.

Fail-safe, like every play: no identifiable issue, no stay in
progress, or no vendor covering the problem holds the run for a
human and nothing sends. A booking change or any other Act that is
not an in-home problem never triggers this play.
"""
import re
from dataclasses import dataclass

from ..types import Category, Decision
from .early_checkin import _effect
from .engine import Play, PlayRun, Step


@dataclass
class Issue:
    type: str
    label: str
    safety: bool = False


# Issue patterns, safety classes first so a dangerous framing of a
# problem always wins over its milder cousin ("water pouring
# everywhere" is flooding, not a leak). Deterministic on purpose:
# extraction must be testable and must never invent an issue.
_ISSUE_PATTERNS = [
    ("gas_smell", "gas smell", True, re.compile(
        r"smell(?:s|ing|ed)?\s+(?:of\s+|like\s+)?gas"
        r"|gas\s+(?:smell|leak)", re.I)),
    ("smoke_co", "smoke / CO alarm", True, re.compile(
        r"smoke|carbon\s+monoxide|\bco\s+(?:alarm|detector)\b", re.I)),
    ("flooding", "active flooding", True, re.compile(
        r"flood(?:ing|ed)?|water\s+(?:is\s+)?(?:pouring|gushing|everywhere)"
        r"|burst\s+pipe|pipe\s+(?:burst|broke)", re.I)),
    ("no_heat", "no heat", True, re.compile(
        r"no\s+heat|heat(?:ing)?\s+(?:is\s+)?(?:not\s+working|out|broken|off)"
        r"|furnace\s+(?:is\s+)?(?:not\s+working|out|broken)"
        r"|house\s+is\s+freezing|freezing\s+inside", re.I)),
    ("lock", "door won't unlock", False, re.compile(
        r"locked\s+out|won'?t\s+unlock|not\s+unlocking"
        r"|can'?t\s+get\s+in|cannot\s+get\s+in"
        r"|(?:door\s+)?(?:code|keypad)\s+(?:is\s+)?not\s+working"
        r"|door\s+(?:code|lock)\b[^.]{0,40}?"
        r"(?:not\s+working|won'?t|doesn'?t|dead)", re.I)),
    ("no_hot_water", "no hot water", False, re.compile(
        r"no\s+hot\s+water|hot\s+water\s+(?:is\s+)?(?:out|gone|not\s+working)"
        r"|water\s+(?:won'?t|doesn'?t|isn'?t)\s+(?:get\s+)?(?:hot|warm)",
        re.I)),
    ("no_water", "no water", False, re.compile(
        r"no\s+water|no\s+running\s+water"
        r"|water\s+(?:is\s+)?(?:shut\s+off|not\s+running)", re.I)),
    ("leak", "leak", False, re.compile(
        r"leak(?:ing|ed|s)?\b|drip(?:ping|ped)?\b"
        r"|water\s+(?:under|behind|around)\b|puddle", re.I)),
    ("no_ac", "AC out", False, re.compile(
        r"no\s+(?:ac|a/c)|no\s+air\s+conditioning"
        r"|(?:ac|a/c|air\s+conditioning)\s+(?:is\s+)?"
        r"(?:not\s+working|out|broken)|won'?t\s+cool|not\s+cooling", re.I)),
]

_APPLIANCE_RE = re.compile(
    r"fridge|refrigerator|oven|stove|dishwasher|washer|dryer|microwave"
    r"|garbage\s+disposal", re.I)
_PROBLEM_RE = re.compile(
    r"broken|not\s+working|won'?t|doesn'?t\s+work|dead|stopped|quit", re.I)

_URGENCY_HINTS = ("right now", "urgent", "asap", "emergency", "baby",
                  "kids", "elderly", "freezing", "getting worse",
                  "all night", "can't sleep", "cannot sleep")

# How the reply opens, per issue - plain acknowledgment, operator's
# voice, no minimizing.
_ACK = {
    "lock": "I'm really sorry you're stuck at the door",
    "leak": "I'm really sorry about the leak",
    "no_water": "I'm really sorry - no water is not okay",
    "no_hot_water": "I'm really sorry - no hot water is not okay",
    "no_ac": "I'm really sorry the AC is out",
    "appliance": "I'm really sorry about that",
}

# One immediate thing the guest can safely try while help is coming,
# per issue. Generic guidance only - never a real code, never
# anything electrical or structural. Safety issues have none, ever.
_SELF_HELP = {
    "lock": "While I get him moving, please try the code once more, "
            "slowly, and give the door a firm push as the last digit "
            "goes in - the keypad sometimes misses a press.",
    "leak": "If it's easy to reach, please put a towel or a bowl "
            "under the drip - only what's right there, nothing heavy.",
    "no_ac": "While I get him moving, please check the thermostat is "
             "set to Cool, a few degrees below the room temperature - "
             "if it kicks on, tell me here and I'll call him off.",
}

# Safety guidance replaces self-help for the safety classes: what
# the guest should do for THEMSELVES, before anything about the fix.
_SAFETY_LINE = {
    "gas_smell": "Please head outside right now, and once you're "
                 "out, call 911 or the gas company - don't switch "
                 "anything on or off on your way out.",
    "smoke_co": "Please head outside right now, and once you're "
                "out, call 911.",
    "flooding": "Please stay clear of any cords or outlets near the "
                "water, and don't try to fix anything yourself.",
    "no_heat": "Please layer up with any spare blankets you can "
               "find in the house for now - nobody should sit in a "
               "cold house tonight.",
}

_WATER_ISSUES = {"leak", "flooding", "no_water", "no_hot_water"}


def classify_issue(message: str) -> Issue | None:
    """The in-stay issue a message reports, or None. Pattern table
    in severity order, then the appliance fallback (an appliance
    noun plus a problem word). None means: not an in-home problem
    this play recognizes - including every non-issue Act."""
    text = message or ""
    for issue_type, label, safety, pattern in _ISSUE_PATTERNS:
        if pattern.search(text):
            return Issue(issue_type, label, safety)
    m = _APPLIANCE_RE.search(text)
    if m and _PROBLEM_RE.search(text):
        return Issue("appliance", f"{m.group(0).lower()} not working")
    return None


def _urgency_hints(message: str) -> list[str]:
    low = (message or "").lower()
    return [h for h in _URGENCY_HINTS if h in low]


def _has_event(run: PlayRun, event_type: str) -> bool:
    return f"event:{event_type}" in run.data


class InStayIssue(Play):
    name = "in_stay_issue"
    description = "A guest mid-stay reports something broken: reply, " \
                  "dispatch the handyman, keep the guest updated. " \
                  "Safety issues also flag the operator."

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Act is the gate: Confirm means a human is already looking,
        # and an Act with no in-home problem in it (a booking change,
        # an early-arrival ask) belongs to another play or to no play.
        if decision is None or decision.category is not Category.ACT:
            return False
        return classify_issue(message) is not None

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("extract_issue", "internal", run=self._extract),
            Step("reality_check", "internal", run=self._reality),
            Step("guest_reply", "action", run=self._guest_reply,
                 action_key="message_guest"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: not r.data.get("safety")),
            Step("dispatch_vendor", "action", run=self._dispatch,
                 action_key="dispatch_vendor"),
            Step("wait_vendor_eta", "wait", event="vendor_eta",
                 skip_if=lambda r: _has_event(r, "vendor_done")),
            Step("guest_eta_update", "action", run=self._eta_update,
                 action_key="message_guest",
                 skip_if=lambda r: not _has_event(r, "vendor_eta")),
            Step("wait_vendor_done", "wait", event="vendor_done"),
            Step("guest_done_update", "action", run=self._done_update,
                 action_key="message_guest"),
        ]

    # -- steps ---------------------------------------------------------
    def _extract(self, engine, run: PlayRun, step: Step) -> None:
        issue = classify_issue(run.message)
        if issue is None:
            step.status = "failed"
            step.result = {"error": "no in-stay issue identified"}
            run.hold_reason = ("could not identify an in-stay issue "
                               "in the message")
            return
        run.data.update({"issue": issue, "safety": issue.safety})
        step.result = {"issue": issue.label, "issue_type": issue.type,
                       "safety": issue.safety,
                       "urgency_hints": _urgency_hints(run.message)}

    def _reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "issue reported for an unknown property"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        if stay is None:
            # The play dispatches a stranger into a home only on a
            # verified stay. No stay on record, a human confirms who
            # is in the home before anyone is sent anywhere.
            step.status = "failed"
            step.result = {"property": prop.name,
                           "stay_in_progress": False}
            run.hold_reason = ("no stay in progress at this property "
                               "today - a human confirms who is in the "
                               "home before anyone is dispatched")
            return
        issue: Issue = run.data["issue"]
        vendor = ctx.get_vendor(issue.type, run.property_id)
        if vendor is None:
            step.status = "failed"
            step.result = {"property": prop.name,
                           "stay_in_progress": True,
                           "issue": issue.label}
            run.hold_reason = (f"no roster vendor covers a "
                               f"{issue.label} at this property")
            return
        notes = []
        if prop.lock_note:
            notes.append(prop.lock_note)
        if issue.type in _WATER_ISSUES and prop.water_note:
            notes.append(prop.water_note)
        run.data.update({
            "stay": stay, "vendor": vendor,
            "property_name": prop.name,
            "access_notes": " ".join(notes),
            "outcome": f"safety_{issue.type}" if issue.safety
                       else issue.type,
        })
        step.result = {
            "property": prop.name, "stay_in_progress": True,
            "guest": stay.guest_name, "issue": issue.label,
            "safety": issue.safety, "vendor": vendor.name,
            "window": vendor.window_note,
        }

    def _guest_reply(self, engine, run: PlayRun, step: Step) -> None:
        issue: Issue = run.data["issue"]
        guest = run.data["stay"].guest_name
        if issue.safety:
            text = (f"Hi {guest}, thank you for telling me right away "
                    f"- this one I treat as urgent. "
                    f"{_SAFETY_LINE[issue.type]} "
                    f"I'm flagging the operator and getting our "
                    f"handyman dispatched now. Message me here once "
                    f"you're set.")
        else:
            bits = [f"Hi {guest}, {_ACK.get(issue.type, _ACK['appliance'])}."]
            help_line = _SELF_HELP.get(issue.type)
            if help_line:
                bits.append(help_line)
            bits.append("I'm getting our handyman out to you today - "
                        "I'll message you here the moment I have his "
                        "arrival time.")
            text = " ".join(bits)
        step.result = {"draft": text, "issue": issue.label,
                       "safety": issue.safety, **_effect(step)}

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        issue: Issue = run.data["issue"]
        stay = run.data["stay"]
        vendor = run.data["vendor"]
        summary = (
            f"SAFETY - {run.data['property_name']}: guest "
            f"{stay.guest_name} reports {issue.label} (mid-stay, "
            f"arrived {stay.arrival_date}, leaves "
            f"{stay.departure_date}). The guest was given safety "
            f"guidance and told not to touch anything. A dispatch to "
            f"{vendor.name} is in this same run. This needs a human's "
            f"eyes now."
        )
        step.result = {"summary": summary, "issue": issue.label,
                       "safety": True,
                       "reason": "safety-class issue", **_effect(step)}

    def _dispatch(self, engine, run: PlayRun, step: Step) -> None:
        issue: Issue = run.data["issue"]
        stay = run.data["stay"]
        vendor = run.data["vendor"]
        safety_tag = " SAFETY - treat as first call." if issue.safety \
            else ""
        access = run.data["access_notes"] or "No special access notes."
        text = (
            f"Hi {vendor.name}, dispatch from the operator - "
            f"{run.data['property_name']}, {run.date}. Issue: "
            f"{issue.label}.{safety_tag} Guest in residence: "
            f"{stay.guest_name} (arrived {stay.arrival_date}, leaves "
            f"{stay.departure_date}, party of {stay.guest_count}). "
            f"Access notes: {access} Please reply with your ETA so "
            f"we can update the guest. {vendor.window_note} "
            f"{vendor.rates_note}"
        )
        step.result = {"draft": text, "vendor": vendor.name,
                       "issue": issue.label, "safety": issue.safety,
                       "access_notes": run.data["access_notes"],
                       **_effect(step)}

    def _eta_update(self, engine, run: PlayRun, step: Step) -> None:
        payload = run.data.get("event:vendor_eta", {})
        eta = str(payload.get("eta", "")).strip()
        guest = run.data["stay"].guest_name
        when = f"arriving around {eta}" if eta else "on his way now"
        text = (f"Quick update, {guest} - help is on the way, "
                f"{when}. I'll message you again here when the work "
                f"is done.")
        step.result = {"draft": text, "eta": eta, **_effect(step)}

    def _done_update(self, engine, run: PlayRun, step: Step) -> None:
        issue: Issue = run.data["issue"]
        guest = run.data["stay"].guest_name
        text = (f"Hi {guest}, our handyman reports the {issue.label} "
                f"is fixed. Please try it once and tell me here if "
                f"everything works now - and I'm sorry again for the "
                f"trouble.")
        step.result = {"draft": text, "issue": issue.label,
                       **_effect(step)}
