"""The plays engine: one state machine every play runs on.

A Play turns a triage Decision into work: an ordered plan of steps.
Step kinds:
  internal - extract facts, check reality. Never gated, sends nothing.
  draft    - produce an artifact in the operator's voice. Drafting is
             never gated; only sending is.
  action   - would touch the outside world (send a message, charge a
             guest, dispatch a human). ALWAYS gated by the graduated
             autonomy ladder before it may execute.
  wait     - park the run until an event arrives (e.g. cleaner_done),
             then resume with the follow-up steps.

Gating reuses jev.autonomy.right_for; when a run carries a
triage decision, that decision's confidence goes through the ladder's
per-decision floor. The ladder's ledger carries per-key stats under
"categories"; triage categories live there today, and play action keys
live there the same way. Stakes:

  auto_close       low      closing noise, logging
  message_guest    middle   sending to a guest
  message_cleaner  middle   sending to a cleaner
  dispatch_vendor  middle   dispatching a handyman/vendor
  notify_operator  middle   flagging the operator (never a guest)
  charge_guest     highest  moving a guest's money

At P1 (approve_first, the default) an action step is HELD: its artifact
is still produced and logged, and nothing executes. At P2/P3 it
executes through the effector — in this build the effector is
SIMULATED (no real rails are wired; every simulated effect is labeled
"simulated" in the log). Fail-safe everywhere: any error, missing
reality, or unparseable step input holds the run for a human.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..autonomy import P2, P3, right_for
from ..ledger_log import append_decision
from ..types import Decision
from .config import PlaysConfig
from .context import RealityContext

STAKES = {
    "auto_close": "low",
    "message_guest": "middle",
    "message_cleaner": "middle",
    "dispatch_vendor": "middle",
    # Added for Play 7 (camera check): the notification goes to the
    # operator, never to a guest — words to a real person, so middle,
    # the same as messaging a cleaner or dispatching a vendor.
    "notify_operator": "middle",
    "charge_guest": "highest",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Step:
    name: str
    kind: str                                    # internal|draft|action|wait
    run: Callable | None = None                  # fn(engine, run, step)
    action_key: str = ""                         # for kind == "action"
    event: str = ""                              # for kind == "wait"
    skip_if: Callable | None = None              # fn(run) -> bool
    status: str = "pending"                      # done|held|waiting|skipped|failed
    result: dict = field(default_factory=dict)
    required_right: str = ""                     # ladder right consulted
    disposition: str = ""                        # execute|hold (actions)

    @property
    def stake(self) -> str:
        return STAKES.get(self.action_key, "")


@dataclass
class PlayRun:
    run_id: str
    play: str
    message: str
    property_id: str
    date: str                                    # arrival day, ISO
    decision: Decision | None = None
    status: str = "running"                      # waiting|completed|held_for_human
    steps: list[Step] = field(default_factory=list)
    cursor: int = 0
    data: dict = field(default_factory=dict)     # scratch space for steps
    events: list[dict] = field(default_factory=list)
    hold_reason: str = ""

    @property
    def held_steps(self) -> list[str]:
        return [s.name for s in self.steps if s.status == "held"]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "play": self.play,
            "status": self.status,
            "property_id": self.property_id,
            "date": self.date,
            "message": self.message,
            "decision_category": self.decision.category.value if self.decision else "",
            "decision_confidence": self.decision.confidence if self.decision else 0.0,
            "outcome": self.data.get("outcome", ""),
            "awaiting_approval": self.held_steps,
            "hold_reason": self.hold_reason,
            "steps": [{
                "name": s.name, "kind": s.kind, "status": s.status,
                "action_key": s.action_key, "stake": s.stake,
                "required_right": s.required_right,
                "disposition": s.disposition, "result": s.result,
            } for s in self.steps],
        }


class Play:
    """A play: trigger + plan builder. Subclasses set name/description,
    implement trigger() and build()."""
    name = ""
    description = ""

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        raise NotImplementedError

    def build(self, run: PlayRun) -> list[Step]:
        raise NotImplementedError


class PlayEngine:
    def __init__(self, context: RealityContext, ledger: dict | None = None,
                 log_path: str | None = None,
                 config: PlaysConfig | None = None):
        self.context = context
        self.ledger = ledger or {}
        self.log_path = log_path
        self.config = config or PlaysConfig.from_env()

    # -- logging ------------------------------------------------------
    def _log(self, run: PlayRun, event: str, **fields) -> None:
        if not self.log_path:
            return
        record = {"ts": _now(), "run_id": run.run_id, "play": run.play,
                  "event": event}
        record.update(fields)
        append_decision(self.log_path, record)

    # -- lifecycle ----------------------------------------------------
    def start(self, play: Play, decision: Decision | None, message: str,
              property_id: str, date: str) -> PlayRun:
        run = PlayRun(run_id=uuid.uuid4().hex[:12], play=play.name,
                      message=message, property_id=property_id, date=date,
                      decision=decision)
        self._log(run, "run_started", property_id=property_id, date=date,
                  decision_category=decision.category.value if decision else "",
                  decision_confidence=decision.confidence if decision else 0.0)
        try:
            run.steps = play.build(run)
        except Exception as exc:                 # fail safe: hold for human
            run.status = "held_for_human"
            run.hold_reason = f"play build failed: {exc}"
            self._log(run, "run_held", reason=run.hold_reason)
            return run
        self._advance(run)
        return run

    def handle_event(self, run: PlayRun, event_type: str,
                     payload: dict | None = None) -> PlayRun:
        payload = payload or {}
        run.events.append({"type": event_type, "payload": payload})
        run.data[f"event:{event_type}"] = payload
        self._log(run, "event_received", event_type=event_type, payload=payload)
        if run.status == "waiting":
            run.status = "running"
            self._advance(run)
        return run

    # -- the state machine ---------------------------------------------
    def _advance(self, run: PlayRun) -> None:
        while run.cursor < len(run.steps):
            step = run.steps[run.cursor]
            if step.skip_if is not None and step.skip_if(run):
                step.status = "skipped"
                self._log(run, "step", step=step.name, kind=step.kind,
                          status="skipped")
                run.cursor += 1
                continue
            if step.kind == "wait":
                got = self._event_for(run, step.event)
                if got is None:
                    step.status = "waiting"
                    run.status = "waiting"
                    self._log(run, "run_waiting", step=step.name,
                              waiting_for=step.event)
                    return
                step.status = "done"
                step.result = {"event": step.event, "payload": got}
                self._log(run, "step", step=step.name, kind="wait",
                          status="done", result=step.result)
                run.cursor += 1
                continue
            if step.kind == "action":
                # A run started from a triage decision carries that
                # decision's confidence through the per-decision floor.
                confidence = run.decision.confidence \
                    if run.decision is not None else None
                step.required_right = right_for(step.action_key, self.ledger,
                                                confidence)
                step.disposition = "execute" if step.required_right in (P2, P3) \
                    else "hold"
                self._log(run, "gate", step=step.name,
                          action_key=step.action_key, stake=step.stake,
                          required_right=step.required_right,
                          disposition=step.disposition)
            try:
                if step.run is not None:
                    step.run(self, run, step)
            except Exception as exc:             # fail safe: hold for human
                step.status = "failed"
                step.result = {"error": str(exc)}
            if step.status == "pending":
                step.status = "done"
            if step.kind == "action" and step.status == "done" \
                    and step.disposition == "hold":
                step.status = "held"
            self._log(run, "step", step=step.name, kind=step.kind,
                      status=step.status, action_key=step.action_key,
                      required_right=step.required_right,
                      disposition=step.disposition, result=step.result)
            run.cursor += 1
            if step.status == "failed" or run.hold_reason:
                run.status = "held_for_human"
                if not run.hold_reason:
                    run.hold_reason = f"step {step.name} failed"
                self._log(run, "run_held", reason=run.hold_reason)
                return
        run.status = "completed"
        self._log(run, "run_completed", outcome=run.data.get("outcome", ""),
                  awaiting_approval=run.held_steps)

    @staticmethod
    def _event_for(run: PlayRun, event_type: str) -> dict | None:
        for ev in run.events:
            if ev["type"] == event_type:
                return ev["payload"]
        return None
