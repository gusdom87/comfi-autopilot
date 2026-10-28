"""Play: SPECIAL REQUESTS - late checkout (Play 5, late-checkout variant).

A departing guest asks to check out later than the standard time. This
is Play 2's timing logic run in reverse: Play 2 asks when the unit will
be ready after the guest before; this play asks how late this guest can
stay and still leave the turnover room before the guest after. It reads
the context for the property and the departure day (run.date is the
DEPARTURE day in this play) and decides from what is on record:

  hard limit   a same-day arrival or turnover. The clean (the
               turnover's booked duration, else the property's typical
               clean) plus the settle buffer has to fit before the next
               guest's check-in, so the latest workable checkout is
               next check-in - clean - buffer. An ask past it collides
               with the next guest.
  soft limits  the property's latest-checkout house rule, and any
               scheduled cleaner/vendor window later that day. Running
               past one of these is not a collision; the guest gets
               the latest time that works instead.

Outcomes:
  grant          the requested time fits every limit: a drafted yes
  grant_partial  the ask overshoots a soft limit only: a drafted yes to
                 the latest time that works, asking the guest to confirm
  deny_offer     the ask collides with a same-day arrival/turnover: a
                 drafted no that offers the latest time that works
  deny           nothing later than standard checkout works that day
  clarify        no usable time in the message: a drafted question
                 back, carrying the latest workable time - never a guess

Money: up to config.late_checkout_free_minutes past standard checkout
is free (the operator's standing rule: up to one hour). Past it, the
fee is a separate charge_guest step - highest right, held at P1, gated
exactly like the early check-in fee - and it is only ever drafted for
the time the guest asked for. A time the play offers but the guest has
not accepted (grant_partial, deny_offer) carries no charge step: the
guest's "yes, that works" arrives as a new request for that time, and
that run's grant carries the fee. The amount is the operator's to set
(config.late_checkout_fee_usd, from JEV_LATE_CHECKOUT_FEE_USD): with
none configured, the fee step is still drafted and held, worded as
"amount to be set", and no dollar figure appears anywhere. When a
grant moves a same-day turnover's start, the cleaner gets a heads-up
through the message_cleaner gate; when a grant lands on a same-day
arrival with no turnover on record, the operator is flagged through
the notify_operator gate to confirm who cleans and when.

Fail-safe, like every play: a message that is not a late-checkout
ask, an unknown property, a missing date, or no
stay on record departing that day holds the run for a human - nobody
promises a checkout time on a booking the context cannot see. A grant
writes nothing back to the calendar: the fixture world has no write
path, and on real rails the approval of the held steps is where the
calendar and the cleaner's schedule would change.
"""
import re
from dataclasses import dataclass

from ..types import Category, Decision
from .context import fmt_time, to_minutes
from .early_checkin import _effect
from .engine import Play, PlayRun, Step
from .in_stay_issue import classify_issue

_N = r"(an?|one|two|three|four|[1-9])"
_N_VALUE = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}

# A time counts only when the guest tied it to leaving. The cue is the
# leaving verb; the glue is the short list of words allowed between the
# cue and the time ("check out a bit later, like 1pm", "stay until
# about noon"). Any other word breaks the anchor, so "late checkout,
# our flight is at 7pm" reads no checkout time at all.
_CUE = (r"\b(?:check(?:ing)?[\s-]?out|leav(?:e|ing)|stay(?:ing)?"
        r"|be\s+out|head\s+out)\b")
_GLUE = (r"(?:[\s,]+(?:a\s+(?:bit|little)\s+)?later?)?"
         r"(?:[\s,]+(?:at|by|around|until|till|til|to|about|like|maybe"
         r"|say|of|be|time|closer|nearer|instead))*[\s,]+")
_CLOCK = (r"(?:(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)"
          r"(?![a-z])|\b(noon|midday)\b)")
_CLOCK_RE = re.compile(_CLOCK, re.I)
_ANCHORED_RE = re.compile(_CUE + _GLUE + _CLOCK, re.I)
# A time that belongs to the trip is never the checkout ask. The clause
# after a transport word is blanked ("our flight is leaving at 7pm"),
# and so is a leaving clause that starts somewhere else ("we're leaving
# at 1pm from the airport").
_TRANSPORT_RE = re.compile(
    r"\b(?:flight|plane|train|bus|ride|cab|uber|taxi)\b[^.!?,;]*"
    r"|[^.!?,;]*\bfrom\s+the\s+(?:airport|station)\b", re.I)
_RELATIVE_RES = (
    # "check out an hour later", "stay 2 hours longer"
    re.compile(_CUE + r"[\s,]+" + _N + r"\s+hours?\s+(?:later|longer|late)\b",
               re.I),
    # "stay an extra hour", "check out two more hours"
    re.compile(_CUE + r"[\s,]+" + _N + r"\s+(?:extra|more)\s+hours?\b", re.I),
    # "an extra hour at checkout", "one more hour on check-out day"
    re.compile(r"\b" + _N + r"\s+(?:extra|more)\s+hours?\s+"
               r"(?:at|for|on|before|of)\s+check[\s-]?out", re.I),
)
_LATE_CUE_RE = re.compile(
    r"\blater?\s+check[\s-]?out\b"
    r"|\bcheck(?:ing)?[\s-]?out\s+(?:a\s+(?:bit|little)\s+)?later?\b"
    r"|\b(?:leave|stay)\s+(?:a\s+(?:bit|little)\s+)?(?:later|longer)\b",
    re.I)
# Not this play: an early check-in ask (Play 2's), or a booking
# extension - another night is a calendar and money change a human
# makes, not a later checkout.
_CHECKIN_RE = re.compile(r"\bcheck[\s-]?in\b", re.I)
_EXTENSION_RE = re.compile(
    r"\b(?:another|extra|additional|\w+\s+more|more|few\s+more)\s+"
    r"(?:night|day)s?\b"
    r"|\bextend\s+(?:our|the|my)\s+(?:stay|booking|reservation|trip)\b"
    r"|\b(?:through|until|till)\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day\b",
    re.I)


def _clock_minutes(m: re.Match) -> int | None:
    if m.group(4):
        return 12 * 60
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None          # "25pm", "0am", "1:75pm": not a time
    hour %= 12
    if m.group(3).lower().startswith("p"):
        hour += 12
    return hour * 60 + minute


def extract_requested_checkout(message: str,
                               standard_checkout: str) -> int | None:
    """The checkout time the guest is asking for, minutes since
    midnight. Reads only times tied to leaving ('check out at 1pm',
    'stay until noon', 'check out an hour later', 'an extra hour at
    checkout'), only times later than standard checkout, and only when
    the guest names exactly one. None - no time, nothing later than
    standard, a nonsense time, two different times, or only a trip's
    time ('our flight is leaving at 7pm') - means the play asks
    instead of guessing."""
    text = _TRANSPORT_RE.sub(" ", message or "")
    standard = to_minutes(standard_checkout)
    clock = set()
    for m in _ANCHORED_RE.finditer(text):
        minutes = _clock_minutes(m)
        if minutes is not None and minutes > standard:
            clock.add(minutes)
    if clock:
        if len(clock) > 1:
            return None
        ask = clock.pop()
        # Any other later time in the message ("1pm or 2pm", "actually
        # 2pm would be better") means the guest has not settled on one.
        later = {t for t in map(_clock_minutes, _CLOCK_RE.finditer(text))
                 if t is not None and t > standard}
        return ask if later <= {ask} else None
    hours = set()
    for pattern in _RELATIVE_RES:
        for m in pattern.finditer(text):
            word = m.group(1).lower()
            hours.add(_N_VALUE.get(word) or int(word))
    if len(hours) == 1:
        return standard + 60 * hours.pop()
    return None


def is_late_checkout_request(message: str) -> bool:
    """A later-checkout ask, and nothing this play should not answer:
    no check-in ask, no booking extension, no in-home problem (Play 4
    owns those, and a broken thing outranks a checkout time)."""
    text = message or ""
    if _CHECKIN_RE.search(text) or _EXTENSION_RE.search(text):
        return False
    if classify_issue(text) is not None:
        return False
    return bool(_LATE_CUE_RE.search(text)) or \
        extract_requested_checkout(text, "11:00") is not None


@dataclass
class _Limit:
    minutes: int     # latest checkout this limit allows
    kind: str        # same_day_arrival | house_rule | scheduled_work
    why: str         # operator-facing, for the run log
    reason: str      # guest-facing sentence for the reply


def _usd(amount: float) -> str:
    return f"${amount:.0f}" if amount == int(amount) else f"${amount:.2f}"


def _fee(config, for_that: str = "", start: bool = False) -> str:
    """The fee as a reply names it: 'the late checkout fee is $25'.
    With no amount configured the operator sets it, so the reply says
    so and carries no dollar figure at all."""
    if config.late_checkout_fee_usd is None:
        text = f"there's a late checkout fee{for_that} (amount to be set)"
    else:
        text = (f"the late checkout fee{for_that} is "
                f"{_usd(config.late_checkout_fee_usd)}")
    return text[0].upper() + text[1:] if start else text


def _fee_line(checkout: int, standard: int, config, accepted: bool) -> str:
    """What the reply says about money for one checkout time. Accepted
    = the guest asked for exactly this time; otherwise it is an offer
    and the fee waits for their yes."""
    if checkout - standard <= config.late_checkout_free_minutes:
        return "There's no charge for that."
    if accepted:
        return (f"{_fee(config, start=True)}, and it'll come "
                f"through as a separate request you can accept.")
    return (f"{_fee(config, ' for that', start=True)}, and it would "
            f"come through as a separate request once you confirm.")


def _price_line(latest: int, standard: int, config) -> str:
    """The money line for a clarifying reply, before any time is set."""
    free = config.late_checkout_free_minutes
    if latest - standard <= free:
        return "There's no charge for checking out by then."
    if free > 0:
        return (f"Checking out by {fmt_time(standard + free)} is free; "
                f"after that {_fee(config)}.")
    return f"{_fee(config, start=True)}."


class LateCheckout(Play):
    name = "late_checkout"
    description = "Guest asks to check out late: check the next arrival " \
                  "and the turnover, answer, hold the fee, tell the cleaner."

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        if decision is None or decision.category is Category.IGNORE:
            return False
        return is_late_checkout_request(message)

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("extract_request", "internal", run=self._extract),
            Step("reality_check", "internal", run=self._reality),
            Step("guest_reply", "action", run=self._guest_reply,
                 action_key="message_guest"),
            Step("late_checkout_charge", "action", run=self._charge,
                 action_key="charge_guest",
                 skip_if=lambda r: not r.data.get("fee_due")),
            Step("cleaner_heads_up", "action", run=self._cleaner_heads_up,
                 action_key="message_cleaner",
                 skip_if=lambda r: not r.data.get("moves_turnover")),
            # A grant on a same-day arrival with no turnover on record:
            # no cleaner to tell, so the operator hears about it.
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: not (
                     r.data.get("outcome") == "grant"
                     and r.data.get("hard") is not None
                     and r.data.get("turnover") is None)),
        ]

    # -- steps ---------------------------------------------------------
    def _extract(self, engine, run: PlayRun, step: Step) -> None:
        if not is_late_checkout_request(run.message):
            step.status = "failed"
            step.result = {"error": "not a late-checkout ask"}
            run.hold_reason = "message is not a late-checkout ask"
            return
        prop = engine.context.get_property(run.property_id)
        standard = prop.standard_checkout if prop else "11:00"
        req = extract_requested_checkout(run.message, standard)
        # No usable time is not a failure: the reply asks for one.
        run.data["requested_minutes"] = req
        step.result = {
            "requested_checkout": fmt_time(req) if req is not None else "",
            "standard_checkout": fmt_time(to_minutes(standard)),
        }

    def _reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "late checkout asked for an unknown property"
            return
        if not run.date:
            step.status = "failed"
            step.result = {"error": "no departure date given"}
            run.hold_reason = "late checkout asked with no departure date"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        if stay is None or stay.departure_date != run.date:
            step.status = "failed"
            step.result = {"property": prop.name, "departing_stay": False}
            run.hold_reason = (f"no stay on record departing this property "
                               f"on {run.date} - a human confirms the "
                               f"booking before any checkout time is "
                               f"promised")
            return

        standard = to_minutes(prop.standard_checkout)
        arrival = ctx.get_arrival(run.property_id, run.date)
        turn = ctx.get_turnover(run.property_id, run.date)
        limits: list[_Limit] = []
        hard = None
        next_in = None
        if arrival is not None or turn is not None:
            # A turnover is the clean before an arriving guest, so
            # either record means someone checks in today: at the
            # arrival's own time, else the house standard. The clean is
            # the turnover's booked duration, else the property typical.
            next_in = to_minutes(arrival.checkin_time) \
                if arrival is not None and arrival.checkin_time \
                else to_minutes(prop.standard_checkin)
            clean = turn.duration_minutes \
                if turn is not None and turn.duration_minutes is not None \
                else prop.typical_clean_minutes
            who = arrival.guest_name if arrival is not None \
                else "The next guest"
            hard = _Limit(
                next_in - clean - prop.buffer_minutes, "same_day_arrival",
                f"{who} checks in at {fmt_time(next_in)}; the "
                f"{clean}-minute turnover plus the {prop.buffer_minutes}-"
                f"minute buffer has to fit first",
                "We have guests arriving after you, and our cleaner needs "
                "the time in between to get the place ready.")
            limits.append(hard)
        cap = to_minutes(prop.latest_checkout)
        limits.append(_Limit(
            cap, "house_rule",
            f"house rule: checkout no later than {fmt_time(cap)}",
            f"{fmt_time(cap)} is the latest checkout this place allows."))
        for window in ctx.get_work_windows(run.property_id, run.date):
            if to_minutes(window.end) <= standard:
                continue                 # over before checkout; no bearing
            start = to_minutes(window.start)
            visit = "a cleaning" if window.kind == "cleaner" \
                else "a service visit"
            reason = f"We have {visit} at the place right after checkout." \
                if start <= standard else \
                f"We have {visit} booked at the place at {fmt_time(start)}."
            limits.append(_Limit(
                max(start, standard), "scheduled_work",
                f"{window.label} scheduled from {fmt_time(start)}",
                reason))
        # The binding limit is the earliest. On a tie the hard limit
        # (listed first) is the one reported.
        limit = min(limits, key=lambda lim: lim.minutes)
        latest = limit.minutes

        req = run.data.get("requested_minutes")
        if req is None:
            outcome = "clarify" if latest > standard else "deny"
        elif req <= latest:
            outcome = "grant"
        elif latest <= standard:
            outcome = "deny"
        elif hard is not None and req > hard.minutes:
            outcome = "deny_offer"
        else:
            outcome = "grant_partial"
        fee_due = outcome == "grant" and \
            req - standard > engine.config.late_checkout_free_minutes
        moves_turnover = outcome == "grant" and turn is not None and \
            req > to_minutes(turn.checkout_time)

        run.data.update({
            "outcome": outcome, "stay": stay, "turnover": turn,
            "property_name": prop.name, "standard_minutes": standard,
            "latest_minutes": latest, "limit": limit, "hard": hard,
            "next_checkin_minutes": next_in, "fee_due": fee_due,
            "moves_turnover": moves_turnover,
        })
        step.result = {
            "outcome": outcome,
            "guest": stay.guest_name,
            "standard_checkout": fmt_time(standard),
            "latest_checkout": fmt_time(latest),
            "limit": limit.kind,
            "limit_note": limit.why,
            "same_day_arrival": hard is not None,
            "next_guest": arrival.guest_name if arrival is not None else "",
            "next_checkin": fmt_time(next_in) if next_in is not None else "",
            "same_day_turnover": turn is not None,
            "cleaner": turn.cleaner_name if turn is not None else "",
            "fee_due": fee_due,
        }

    def _guest_reply(self, engine, run: PlayRun, step: Step) -> None:
        d = run.data
        cfg = engine.config
        outcome = d["outcome"]
        guest = d["stay"].guest_name
        standard = d["standard_minutes"]
        latest_min = d["latest_minutes"]
        latest = fmt_time(latest_min)
        req_min = d["requested_minutes"]
        req = fmt_time(req_min) if req_min is not None else ""
        if outcome == "grant":
            article = "an" if req.startswith(("8", "11")) else "a"
            text = (f"Hi {guest}, good news - {article} {req} checkout "
                    f"works! The place is yours until then. "
                    + _fee_line(req_min, standard, cfg, accepted=True))
        elif outcome == "grant_partial":
            text = (f"Hi {guest}, good news - I can stretch checkout to "
                    f"{latest}, though not quite to {req}. "
                    f"{d['limit'].reason} If {latest} works for you, just "
                    f"reply here and I'll set it up. "
                    + _fee_line(latest_min, standard, cfg, accepted=False))
        elif outcome == "deny_offer":
            text = (f"Hi {guest}, I'm sorry - {req} won't work that day. "
                    f"{d['hard'].reason} The latest I can do is {latest}. "
                    f"If that works, just reply here and I'll set it up. "
                    + _fee_line(latest_min, standard, cfg, accepted=False))
        elif outcome == "deny":
            text = (f"Hi {guest}, I'm sorry - I can't do a late checkout "
                    f"that day. {d['limit'].reason} Checkout stays at "
                    f"{fmt_time(standard)} this time.")
        else:                                    # clarify
            text = (f"Hi {guest}, a later checkout should work - what time "
                    f"were you hoping to leave? The latest I can do that "
                    f"day is {latest}. "
                    + _price_line(latest_min, standard, cfg))
        step.result = {"draft": text, "guest": guest, **_effect(step)}

    def _charge(self, engine, run: PlayRun, step: Step) -> None:
        # No configured amount: the step is still drafted and held, with
        # the amount left for the operator to set - never an invented one.
        amount = engine.config.late_checkout_fee_usd
        step.result = {
            "charge": {"amount_usd": amount,
                       "description": "Late checkout fee"
                       if amount is not None
                       else "Late checkout fee (amount to be set)",
                       "property_id": run.property_id,
                       "date": run.date,
                       "checkout_time":
                           fmt_time(run.data["requested_minutes"])},
            **_effect(step),
        }

    def _cleaner_heads_up(self, engine, run: PlayRun, step: Step) -> None:
        turn = run.data["turnover"]
        new_start = fmt_time(run.data["requested_minutes"])
        old_start = fmt_time(to_minutes(turn.checkout_time))
        next_in = fmt_time(run.data["next_checkin_minutes"])
        # The operator's rule for cleaner texts: never the cleaner's
        # name in the message. The name rides along for the operator.
        text = (f"Hi, a schedule change for {run.data['property_name']} on "
                f"{run.date}: the departing guest has a late checkout at "
                f"{new_start}, so the turnover starts then instead of "
                f"{old_start}. The next guest still checks in at "
                f"{next_in}. Thanks!")
        step.result = {"draft": text, "cleaner": turn.cleaner_name,
                       "turnover_start": new_start,
                       "was": old_start, **_effect(step)}

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        arrival = engine.context.get_arrival(run.property_id, run.date)
        who = arrival.guest_name if arrival is not None else "the next guest"
        summary = (
            f"{run.data['property_name']} on {run.date}: late checkout "
            f"granted on a same-day arrival with no turnover on record; "
            f"confirm who cleans and when. {run.data['stay'].guest_name} "
            f"checks out at {fmt_time(run.data['requested_minutes'])} and "
            f"{who} checks in at "
            f"{fmt_time(run.data['next_checkin_minutes'])}, so the clean "
            f"isn't on anyone's list yet.")
        step.result = {"summary": summary,
                       "reason": "same-day arrival, no turnover on record",
                       **_effect(step)}
