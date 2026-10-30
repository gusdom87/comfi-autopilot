"""Crew send gate (design package 1c, S4-minimal; audited spec 2.3;
package Appendix C).

The one door a future crew text must pass. Code only in this build:
nothing calls it from a play or a webhook, its transport is a mock, and
it refuses any transport that does not declare itself offline.

What a request can carry is the first gate. A CrewSendRequest names an
action key, a property, a role, a template, and slot values. It has no
body field. The worker would get the approved template rendered by the
gate, with slots that are dates and times only; the property's short
name comes from the directory. Guest words and model output have
nowhere to go (audited spec 2.7, safeguard 7).

Hard checks, in order. The first one that fails REFUSES: nothing
sends, and the log records why.
   1 operation   a crew action key; a deny-listed operation or any
                 other key is refused
   2 allowlist   one of the operator's own listings, by ID
   3 directory   the property is in the crew directory
   4 role        cleaner, handyman, plumber, or electrician
   5 template    in the registry, approved by the operator, its text
                 still hashing to what was approved, built for this
                 action key and role
   6 recipient   exactly one active directory entry for this role at
                 this property; if the run's record names a recipient,
                 it must be that entry
   7 language    the template's language is the recipient's
   8 slots       exactly the template's slots, each a valid date or
                 time
   9 content     lint on the rendered text, the directory's worker
                 names included; an optional voice lint hook
  10 consent     a job text needs a recorded YES to the approved
                 worker notice; nobody opted out
  11 duplicate   one attempt per run, recipient, and template, ever;
                 an attempt with an unclear result is never re-sent
  12 thread      the worker's thread re-read now; a failed read refuses

Then the checks that HOLD (every hard check passed; the prepared text
waits for the operator): cleaner recipient (a cleaner is verified only
by a live Turno assignee read, which this build doesn't have, so every
cleaner text waits for the operator to confirm the recipient, audited
spec 2.3), yield to humans (a manual business-side text in that thread
in the last 2 hours), loop guard (the last two messages are inbound
"other" texts), caps (3 per worker per local day, 3 per job, 10 per day
across all workers), and the per-action autonomy check
(classes.may_run_without_approval: Class C and D always wait; Class B
waits until its own key has an earned right). No worker key has a
graded record, so in practice every crew text is held.

Worker quiet hours (9 PM to 7 AM, property-local, DST-aware) DEFER a
text that would otherwise go, to 7 AM local.

Every evaluate() and submit() writes one full record (each check's
result, the rendered text and its hash, the template hash, the slot
values, the directory ID, the last four digits of the number, the
class and right, the transport result) to a private JSONL log, mode
600, outside any git work tree (audited spec 2.3 Logging).
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

from . import deny_list
from ._files import append_jsonl, check_private_path, read_jsonl
from .classes import CREW_ACTION_KEYS, ROLES, class_for, may_run_without_approval
from .templates import TemplateRegistry, lint, render
from .transport import E164_RE, UNKNOWN, CrewTransport, SendResult

REFUSED, HELD, DEFERRED, READY = "refused", "held", "deferred", "ready"
SENT, SEND_FAILED = "sent", "send_failed"
SENDING = "sending"

DEFAULT_TZ = "America/Chicago"
NOTICE_TEMPLATE_ID = "crew_notice_v1"

# Audited spec 2.3: the cleaner is the assignee of that exact Turno
# project, re-read at send time through a supported read. This build has
# no such read, so a cleaner text always waits for the operator to
# confirm the recipient. Vendors are verified by the directory alone
# (exactly one entry for the role at the property).
CLEANER_ASSIGNEE_READ = False


@dataclass(frozen=True)
class Limits:
    quiet_start: time = time(21, 0)
    quiet_end: time = time(7, 0)
    per_worker_day: int = 3
    per_job: int = 3
    all_workers_day: int = 10
    yield_window: timedelta = timedelta(hours=2)


@dataclass(frozen=True)
class Person:
    person_id: str
    number: str
    name: str = ""
    language: str = "en"
    notice_sha256: str = ""        # hash of the notice text they said YES to
    consent_yes_at: str = ""
    opted_out: bool = False
    active: bool = True


@dataclass(frozen=True)
class PropertyEntry:
    property_id: str
    short_name: str
    timezone: str
    roles: MappingProxyType        # role -> tuple of person IDs


class Directory:
    """The private crew directory (a config file kept outside the repo,
    maintained by the operator). Inconsistent entries fail at load."""

    def __init__(self, properties: dict[str, PropertyEntry], people: dict[str, Person]):
        numbers = {}
        for p in people.values():
            if not E164_RE.match(p.number):
                raise ValueError(f"directory: {p.person_id} has no valid E.164 number")
            if p.number in numbers:
                raise ValueError(f"directory: {p.person_id} and {numbers[p.number]} share a number")
            numbers[p.number] = p.person_id
        for prop in properties.values():
            ZoneInfo(prop.timezone)
            for role, ids in prop.roles.items():
                if role not in ROLES:
                    raise ValueError(f"directory: unknown role {role!r} at {prop.property_id}")
                missing = [i for i in ids if i not in people]
                if missing:
                    raise ValueError(f"directory: {prop.property_id} {role} names unknown "
                                     f"people: {', '.join(missing)}")
        self._properties = dict(properties)
        self._people = dict(people)

    @classmethod
    def from_dict(cls, data: dict) -> "Directory":
        if data.get("schema") != "crew-directory/v1":
            raise ValueError("not a crew-directory/v1 file")
        people = {}
        for pid, p in (data.get("people") or {}).items():
            people[pid] = Person(
                person_id=pid, number=str(p.get("number") or ""), name=str(p.get("name") or ""),
                language=str(p.get("language") or "en"),
                notice_sha256=str(p.get("notice_sha256") or ""),
                consent_yes_at=str(p.get("consent_yes_at") or ""),
                # Only an explicit false counts as opted in; a missing
                # flag reads as opted out.
                opted_out=p.get("opted_out") is not False,
                active=p.get("active") is not False)
        props = {}
        for prop_id, e in (data.get("properties") or {}).items():
            roles = {r: tuple(ids or ()) for r, ids in (e.get("roles") or {}).items()}
            props[prop_id] = PropertyEntry(
                property_id=prop_id, short_name=str(e.get("short_name") or ""),
                timezone=str(e.get("timezone") or DEFAULT_TZ), roles=MappingProxyType(roles))
        return cls(props, people)

    @classmethod
    def load(cls, path: str) -> "Directory":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def numbers(self) -> frozenset[str]:
        return frozenset(p.number for p in self._people.values())

    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._people.values() if p.name)

    def property(self, property_id: str) -> PropertyEntry | None:
        return self._properties.get(property_id)

    def resolve(self, property_id: str, role: str) -> tuple[Person | None, str]:
        """(the one verified person, "") or (None, why not)."""
        prop = self._properties.get(property_id)
        if prop is None:
            return None, "property is not in the crew directory"
        people = [self._people[i] for i in prop.roles.get(role, ()) if self._people[i].active]
        if not people:
            return None, f"no verified {role} on file for this property"
        if len(people) > 1:
            return None, (f"{len(people)} active {role} entries for this property; the "
                          "operator picks the recipient (Class C)")
        return people[0], ""


@dataclass(frozen=True)
class CrewSendRequest:
    """No body field, by design: the text is the approved template."""
    action_key: str
    property_id: str
    role: str
    template_id: str
    slots: dict
    run_id: str
    job_id: str
    recipient_id: str | None = None    # the directory ID the run's record names, if any


@dataclass(frozen=True)
class ThreadMessage:
    at: datetime
    direction: str                     # "in" (from the worker) | "out" (business side)
    automated: bool = False            # outbound sent through this gate
    kind: str = ""                     # inbound class when known ("other", ...)


class ThreadReader:
    """Re-reads one worker's Quo thread. Only a fake exists in this build."""

    def recent(self, number: str, since: datetime) -> list[ThreadMessage]:
        raise NotImplementedError


@dataclass
class GateResult:
    outcome: str
    reasons: list[str]
    checks: list[dict] = field(default_factory=list)
    body: str = ""
    body_sha256: str = ""
    template_sha256: str = ""
    recipient_id: str = ""
    action_class: str = ""
    right: str = ""
    idempotency_key: str = ""
    deferred_until: str = ""
    transport: dict | None = None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _ts(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_quiet_hours(local: datetime, limits: Limits) -> bool:
    t = local.timetz().replace(tzinfo=None)
    return t >= limits.quiet_start or t < limits.quiet_end


def quiet_hours_end(local: datetime, limits: Limits) -> datetime:
    """The next quiet-hours end (7:00 AM local) at or after `local`."""
    day = local.date() if local.timetz().replace(tzinfo=None) < limits.quiet_end \
        else local.date() + timedelta(days=1)
    return datetime.combine(day, limits.quiet_end, tzinfo=local.tzinfo)


class CrewSendGate:
    def __init__(self, *, directory: Directory, registry: TemplateRegistry,
                 own_property_ids, log_path: str, transport: CrewTransport,
                 thread_reader: ThreadReader, ledger: dict | None = None,
                 limits: Limits | None = None, voice_lint=None,
                 notice_template_id: str = NOTICE_TEMPLATE_ID):
        if not isinstance(transport, CrewTransport):
            raise TypeError("the crew gate takes a CrewTransport only")
        if getattr(transport, "OFFLINE", False) is not True:
            raise ValueError("this build has no live crew transport: only an offline "
                             "(mock) CrewTransport is accepted")
        self._directory = directory
        self._registry = registry
        self._own = frozenset(own_property_ids)
        self._log_path = check_private_path(log_path)
        self._transport = transport
        self._reader = thread_reader
        self._ledger = ledger or {}
        self._limits = limits or Limits()
        self._voice_lint = voice_lint
        self._notice_id = notice_template_id

    # --- public surface: evaluate and submit, nothing else ---

    def evaluate(self, request: CrewSendRequest, now: datetime) -> GateResult:
        """Every check; nothing is sent. Logged."""
        result, person = self._decide(request, now)
        self._log("evaluate", request, result, person, now)
        return result

    def submit(self, request: CrewSendRequest, now: datetime) -> GateResult:
        """Every check, then one text through the (offline) transport
        only when the outcome is READY. Logged."""
        result, person = self._decide(request, now)
        if result.outcome == READY:
            self._log("intent", request, GateResult(SENDING, ["attempt in flight"],
                                                    idempotency_key=result.idempotency_key,
                                                    recipient_id=result.recipient_id), person, now)
            try:
                sent = self._transport.send_text(person.number, result.body)
            except Exception as e:  # timeout, bug: the status is unknown
                sent = SendResult(UNKNOWN, None, f"{type(e).__name__}: {e}"[:300])
            result.transport = {"status": sent.status, "message_id": sent.message_id,
                                "detail": sent.detail}
            result.outcome = {"sent": SENT, "failed": SEND_FAILED}.get(sent.status, UNKNOWN)
            if result.outcome != SENT:
                result.reasons.append(f"transport: {sent.status}; never re-sent")
        self._log("submit", request, result, person, now)
        return result

    # --- internals ---

    def _decide(self, req: CrewSendRequest, now: datetime) -> tuple[GateResult, Person | None]:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        checks: list[dict] = []
        res = GateResult(outcome=REFUSED, reasons=[], checks=checks)
        person = None

        def ok(name, detail=""):
            checks.append({"check": name, "result": "pass", "detail": detail})

        def refuse(name, detail):
            checks.append({"check": name, "result": "fail", "detail": detail})
            res.reasons.append(f"{name}: {detail}")
            return res, person

        # 1 operation
        key = str(req.action_key or "")
        if deny_list.is_denied(key):
            return refuse("operation", f"{key} is a deny-listed operation; not callable from comms")
        if key not in CREW_ACTION_KEYS:
            cls = class_for(key)
            return refuse("operation", f"{key!r} is not a crew text key (class {cls})")
        ok("operation", key)
        res.action_class = class_for(key)

        # 2 allowlist, 3 directory, 4 role
        if req.property_id not in self._own:
            return refuse("allowlist", "not one of the operator's own listings (by ID); "
                          "client properties get no automated text")
        ok("allowlist")
        prop = self._directory.property(req.property_id)
        if prop is None:
            return refuse("directory", "property is not in the crew directory")
        ok("directory")
        if req.role not in ROLES:
            return refuse("role", f"{req.role!r} is not one of {', '.join(ROLES)}")
        ok("role", req.role)

        # 5 template
        t = self._registry.get(req.template_id)
        if t is None:
            why = "not in the approved-template registry"
            if req.template_id in getattr(self._registry, "rejected", {}):
                why = "rejected at load: " + "; ".join(self._registry.rejected[req.template_id])
            return refuse("template", f"{req.template_id!r} {why}")
        if not t.approved:
            why = ("its text changed after approval" if t.approved_sha256
                   else "not approved by the operator")
            return refuse("template", f"{t.template_id}: {why}")
        if t.action_key != key:
            return refuse("template", f"{t.template_id} is for {t.action_key}, not {key}")
        if req.role not in t.roles:
            return refuse("template", f"{t.template_id} is not for the {req.role} role")
        res.template_sha256 = t.sha256
        ok("template", t.template_id)

        # 6 recipient
        person, why = self._directory.resolve(req.property_id, req.role)
        if person is None:
            return refuse("recipient", why)
        if req.recipient_id is not None and req.recipient_id != person.person_id:
            return refuse("recipient", f"the run's record names {req.recipient_id!r}, which is "
                          f"not the verified {req.role} for this property")
        res.recipient_id = person.person_id
        ok("recipient", person.person_id)

        # 7 language
        if t.language != person.language:
            return refuse("language", f"no approved {person.language} template; "
                          "translation at send time is never done")
        ok("language", t.language)

        # 8 slots and render
        try:
            body = render(t, prop.short_name, dict(req.slots or {}))
        except ValueError as e:
            return refuse("slots", str(e))
        res.body, res.body_sha256 = body, hashlib.sha256(body.encode("utf-8")).hexdigest()
        ok("slots", json.dumps(req.slots, sort_keys=True))

        # 9 content
        hits = lint(body, t.kind, names=self._directory.names())
        if self._voice_lint is not None:
            hits += [f"voice: {h}" for h in self._voice_lint(body)]
        if hits:
            return refuse("content", ", ".join(hits))
        ok("content")

        # 10 consent
        if person.opted_out:
            return refuse("consent", "the recipient opted out of automated texts")
        if t.kind == "job":
            notice = self._registry.get(self._notice_id)
            if notice is None or not notice.approved:
                return refuse("consent", "the worker notice is not approved by the operator; "
                              "no automated text before it")
            if not person.consent_yes_at or person.notice_sha256 != notice.sha256:
                return refuse("consent", "no recorded YES to the approved worker notice")
        ok("consent")

        # 11 duplicate
        res.idempotency_key = hashlib.sha256(
            f"{req.run_id}|{person.person_id}|{t.template_id}".encode("utf-8")).hexdigest()[:20]
        history = self._attempts()
        if res.idempotency_key in history:
            prior = history[res.idempotency_key]
            return refuse("duplicate", f"already attempted ({prior['outcome']} at "
                          f"{prior['at']}); never re-sent")
        ok("duplicate")

        # 12 thread re-read
        try:
            thread = sorted(self._reader.recent(person.number, now - timedelta(days=1)),
                            key=lambda m: m.at)
        except Exception as e:
            return refuse("thread", f"could not re-read the worker's thread "
                          f"({type(e).__name__}); held for the operator")
        ok("thread", f"{len(thread)} message(s) in the last 24 hours")

        holds = []
        if req.role == "cleaner" and not CLEANER_ASSIGNEE_READ:
            holds.append(("recipient_check", "no live Turno assignee read in this build; the "
                          "operator confirms the cleaner (Class C)"))
        manual = [m for m in thread if m.direction == "out" and not m.automated
                  and now - m.at <= self._limits.yield_window]
        if manual:
            holds.append(("yield", "a manual business-side text landed in this thread "
                          f"{(now - manual[-1].at).total_seconds() / 60:.0f} min ago"))
        last_two = thread[-2:]
        if len(last_two) == 2 and all(m.direction == "in" and m.kind == "other" for m in last_two):
            holds.append(("loop_guard", "the last two texts from this worker were 'other'; "
                          "automation paused on this thread"))

        tz = ZoneInfo(prop.timezone)
        local_now = now.astimezone(tz)
        holds += self._cap_holds(history, person, req.job_id, local_now, tz)

        allowed, cls, why = may_run_without_approval(key, self._ledger)
        res.right = why
        if not allowed:
            holds.append(("autonomy", f"class {cls}: {why}"))

        for name, detail in holds:
            checks.append({"check": name, "result": "hold", "detail": detail})
            res.reasons.append(f"{name}: {detail}")
        if holds:
            res.outcome = HELD
            return res, person

        if in_quiet_hours(local_now, self._limits):
            until = quiet_hours_end(local_now, self._limits)
            res.deferred_until = until.isoformat()
            checks.append({"check": "quiet_hours", "result": "defer",
                           "detail": f"local {local_now:%H:%M %Z}; deferred to {until:%H:%M %Z}"})
            res.reasons.append(f"quiet_hours: worker quiet hours until {until:%I:%M %p %Z}")
            res.outcome = DEFERRED
            return res, person
        ok("quiet_hours", f"local {local_now:%H:%M %Z}")
        res.outcome = READY
        return res, person

    def _attempts(self) -> dict:
        """Idempotency key -> its latest attempt (sending, sent,
        send_failed, unknown), from the log."""
        rows, _ = read_jsonl(self._log_path)
        out = {}
        for r in rows:
            if r.get("phase") in ("intent", "submit") and \
                    r.get("outcome") in (SENDING, SENT, SEND_FAILED, UNKNOWN):
                out[r.get("idempotency_key")] = r
        return out

    def _cap_holds(self, history, person, job_id, local_now, tz) -> list[tuple[str, str]]:
        today = local_now.date()
        counted = [r for r in history.values() if r.get("outcome") in (SENDING, SENT, UNKNOWN)]
        day = [r for r in counted if (at := _ts(r.get("at"))) and at.astimezone(tz).date() == today]
        lim = self._limits
        holds = []
        if sum(1 for r in day if r.get("recipient_id") == person.person_id) >= lim.per_worker_day:
            holds.append(("cap", f"{lim.per_worker_day} automated texts to this worker today"))
        if sum(1 for r in counted if r.get("job_id") == job_id) >= lim.per_job:
            holds.append(("cap", f"{lim.per_job} automated texts on this job"))
        if len(day) >= lim.all_workers_day:
            holds.append(("cap", f"{lim.all_workers_day} automated texts across all workers today"))
        return holds

    def _log(self, phase, req, res, person, now):
        row = {
            "at": _iso(now), "phase": phase, "outcome": res.outcome, "reasons": res.reasons,
            "checks": res.checks, "request": asdict(req), "job_id": req.job_id,
            "run_id": req.run_id, "recipient_id": res.recipient_id,
            "recipient_number_last4": person.number[-4:] if person else "",
            "template_id": req.template_id, "template_sha256": res.template_sha256,
            "body": res.body, "body_sha256": res.body_sha256,
            "action_class": res.action_class, "right": res.right,
            "idempotency_key": res.idempotency_key, "deferred_until": res.deferred_until,
            "transport": res.transport,
        }
        append_jsonl(self._log_path, row)
