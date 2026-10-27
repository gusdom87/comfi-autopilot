"""Play: CLEANER-DONE -> GUEST-READY (Play 3).

Play 2 parks a run inside an early check-in and wakes it when the
cleaner reports done. This play stands alone: ANY cleaner-done event
for a unit with a guest arriving today starts it — no guest message
needed. The cleaner's finish is the trigger.

Rails are shared, not rebuilt: the run starts on the same wait step
kind Play 2 uses, parked for the "cleaner_done" event, and
handle_cleaner_done_event() below is the entry point that creates the
run and delivers the event to it. From there the ordinary engine
machinery — steps, gates, run log — takes over.

Reality is verified, never taken on the event's word:
  - the event must match a guest arriving at that property today —
    no arrival today, the run logs and closes, nothing sends;
  - the clean must be marked complete in the context — an event that
    claims done with no completed clean on record fails safe to a
    human hold;
  - a clean completed WITH a problem flag (damage, a missing item,
    anything the cleaner noted) is never messaged to the guest as
    "ready": the run holds for a human with the flag summarized.

The ready message is composed from fixture property data (name,
standard check-in, door/parking instruction lines) in the operator's
voice, and the send is gated by the message_guest right, exactly like
every other guest send in the plays.
"""
from ..types import Decision
from .context import fmt_time, to_minutes
from .early_checkin import _effect
from .engine import Play, PlayRun, Step


class CleanerDone(Play):
    name = "cleaner_done"
    description = "A cleaner finishes a same-day turnover: verify the " \
                  "clean, then tell the arriving guest the place is ready."

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered, not message-triggered: a triage Decision
        # never starts this play. Entry point is the cleaner-done
        # event (see handle_cleaner_done_event). Returning False keeps
        # decision matching exactly as it was before this play existed.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        quiet = lambda r: r.data.get("outcome") == "no_arrival"
        return [
            Step("wait_cleaner_done", "wait", event="cleaner_done"),
            Step("match_arrival", "internal", run=self._match_arrival),
            Step("verify_clean", "internal", run=self._verify_clean,
                 skip_if=quiet),
            Step("send_guest_ready", "action", run=self._send_ready,
                 action_key="message_guest", skip_if=quiet),
        ]

    # -- steps ---------------------------------------------------------
    def _match_arrival(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "cleaner-done event for an unknown property"
            return
        arrival = ctx.get_arrival(run.property_id, run.date)
        step.result = {
            "property": prop.name,
            "arrival_today": arrival is not None,
            "guest": arrival.guest_name if arrival else "",
        }
        if arrival is None:
            # Not an error: a clean with nobody arriving (checkout-only
            # day, a mid-stay refresh). Log it and close; no message.
            run.data["outcome"] = "no_arrival"
            return
        run.data.update({"arrival": arrival, "property_name": prop.name})

    def _verify_clean(self, engine, run: PlayRun, step: Step) -> None:
        report = engine.context.get_clean_report(run.property_id, run.date)
        if report is None or report.status != "complete":
            # The event claimed done; the record does not confirm it.
            # Fail safe: a human checks before any guest hears "ready".
            step.status = "failed"
            step.result = {"error": "no completed clean on record",
                           "on_record": report.status if report else ""}
            run.hold_reason = ("cleaner reported done, but no completed "
                               "clean is on record for this property")
            return
        step.result = {
            "clean_status": report.status,
            "finished_at": report.finished_at,
            "problem_flag": report.problem_flag,
        }
        if report.problem_flag:
            # The unit may be clean, but something is wrong in it. The
            # guest must not hear "ready" until a human has seen this.
            run.data["outcome"] = "problem_flag"
            run.hold_reason = ("cleaner flagged a problem: "
                               + report.problem_flag)
            return
        run.data.update({"outcome": "ready", "report": report})

    def _send_ready(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        arrival = run.data["arrival"]
        report = run.data["report"]
        standard = fmt_time(to_minutes(prop.standard_checkin))
        bits = [
            f"Hi {arrival.guest_name}, good news - {prop.name} is clean "
            f"and ready for you now, ahead of the {standard} check-in.",
        ]
        if report.finished_at:
            bits.append(f"Our cleaner finished at {report.finished_at}.")
        if prop.door_note:
            bits.append(prop.door_note)
        if prop.parking_note:
            bits.append(prop.parking_note)
        bits.append("If you need anything during your stay, just message "
                    "me here.")
        step.result = {"draft": " ".join(bits),
                       "guest": arrival.guest_name, **_effect(step)}


def handle_cleaner_done_event(engine, payload: dict) -> PlayRun:
    """Entry point: a cleaner-done event arrived (Turno-style webhook
    shape: property_id, date, cleaner_id, finished_at). Creates the
    Play 3 run on the shared rails — the run parks on its wait step —
    then delivers the event, which wakes it and runs the play out.
    Malformed events (no property or no date) are a caller error and
    raise; everything after that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    if not property_id or not date:
        raise ValueError("cleaner_done event needs property_id and date")
    run = engine.start(CleanerDone(), None,
                       f"[event] cleaner_done {property_id} {date}",
                       property_id, date)
    engine.handle_event(run, "cleaner_done", payload)
    return run
