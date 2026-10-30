"""Approved-template registry for crew texts (design package 1e, R12,
S3; audited spec 2.3 Content; package Appendix C).

A crew text is never free text. It is one of these templates, rendered
by the crew gate with slots it fills from structured records. A
template counts as approved only when the operator's approval record
carries the SHA-256 of its exact text; change one character and it
needs approval again.

Slots are typed, and there is no free-text type:
  {property}                      the property's short name, filled by
                                  the gate from the crew directory,
                                  never by the caller
  {date}                          an ISO date, rendered "Sun Nov 8"
  {time} {from_time} {to_time}    a 24-hour HH:MM, rendered "12:30 PM"
  {done_by} {start_time} {end_time}
A template with any other slot is rejected at load.

Content lint, on every template at load and on every rendered text:
no money words, no codes or access words, no long digit runs, nothing
about cameras (the worker notice excepted: it has to disclose them),
no gendered pronouns, no guest details or reservation codes, nothing
about discipline or performance, never signed as Gus, no em or en
dashes. The gate adds the directory's worker names.

The shipped drafts are text for the operator to approve (package Q7).
None is approved, so the shipped registry refuses every send. The
brand string is open (Q7: "Comfi Stays" or "Comfi-Stays"); changing it
changes the hash.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from .classes import CREW_ACTION_KEYS, ROLES

KINDS = ("job", "notice")

SLOT_TYPES = {
    "property": "property",
    "date": "date",
    "time": "time",
    "from_time": "time",
    "to_time": "time",
    "done_by": "time",
    "start_time": "time",
    "end_time": "time",
}

_SLOT_RE = re.compile(r"\{([^{}]*)\}")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LINT = (
    ("money", re.compile(
        r"\$|\b(?:pay|paid|payment|payout|rates?|fees?|prices?|costs?"
        r"|charges?|invoice|tips?|bonus|reimburs\w*|refund\w*|buy|bought"
        r"|purchase\w*|venmo|zelle|cash)\b", re.I), ()),
    ("access", re.compile(
        r"\b(?:codes?|locks?|unlock\w*|keypad|keys?|lockbox|passwords?|pin"
        r"|garage|gate)\b", re.I), ()),
    ("long digit run", re.compile(r"\d{4,}"), ()),
    ("camera", re.compile(
        r"\b(?:cameras?|video|footage|recordings?|recorded|monitor\w*)\b",
        re.I), ("notice",)),
    ("gendered pronoun", re.compile(
        r"\b(?:he|she|him|her|his|hers|himself|herself)\b", re.I), ()),
    ("guest detail", re.compile(
        r"\bguest'?s?\s+(?:name|number|phone|email)\b|\bHM[A-Z0-9]{8}\b"
        r"|\+?\d[\d\s().-]{8,}\d", re.I), ()),
    ("discipline or performance", re.compile(
        r"\b(?:no[\s-]?show|late\s+again|you(?:'re|\s+are|\s+were)\s+late"
        r"|warning|penalt\w*|discipline|performance|ratings?|reviews?"
        r"|strikes?|fired)\b", re.I), ()),
    ("signed as the operator", re.compile(
        r"\b(?:Gus|Gustavo)\b|^\s*-\s*G\s*$", re.I | re.M), ()),
    ("dash", re.compile("[–—]"), ()),
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lint(text: str, kind: str = "job", names=()) -> list[str]:
    """Content lint hits on a template or a rendered text. `names`:
    worker names that must never appear."""
    hits = [label for label, pattern, exempt in _LINT
            if kind not in exempt and pattern.search(text or "")]
    for name in names:
        name = (name or "").strip()
        if len(name) >= 2 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text or "", re.I):
            hits.append("worker name")
            break
    return hits


@dataclass(frozen=True)
class Template:
    template_id: str
    kind: str                      # job | notice
    action_key: str
    roles: tuple[str, ...]
    language: str
    text: str
    approved_sha256: str | None = None
    approved_by: str = ""
    approved_at: str = ""

    @property
    def sha256(self) -> str:
        return sha256(self.text)

    @property
    def approved(self) -> bool:
        return bool(self.approved_by) and self.approved_sha256 == self.sha256

    @property
    def slots(self) -> tuple[str, ...]:
        seen = []
        for name in _SLOT_RE.findall(self.text):
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    @property
    def caller_slots(self) -> frozenset[str]:
        """The slots a request supplies: every slot but {property}."""
        return frozenset(s for s in self.slots if SLOT_TYPES.get(s) != "property")


def template_problems(t: Template) -> list[str]:
    problems = []
    if t.kind not in KINDS:
        problems.append(f"unknown kind {t.kind!r}")
    if t.action_key not in CREW_ACTION_KEYS:
        problems.append(f"{t.action_key!r} is not a crew action key")
    if not t.roles or any(r not in ROLES for r in t.roles):
        problems.append(f"roles must be from {', '.join(ROLES)}")
    bad = [s for s in t.slots if s not in SLOT_TYPES]
    if bad:
        problems.append(f"slot(s) with no structured type: {', '.join(bad)}")
    stripped = _SLOT_RE.sub("", t.text)
    if "{" in stripped or "}" in stripped:
        problems.append("unbalanced braces")
    hits = lint(stripped, t.kind)
    if hits:
        problems.append(f"content lint: {', '.join(hits)}")
    return problems


def render_value(slot: str, value) -> str:
    """A slot value checked against its type and rendered. Raises
    ValueError on anything that isn't a valid date or time."""
    kind = SLOT_TYPES.get(slot)
    if not isinstance(value, str):
        raise ValueError(f"{slot}: a string is required")
    if kind == "date":
        if not _DATE_RE.match(value):
            raise ValueError(f"{slot}: not an ISO date (YYYY-MM-DD)")
        d = date.fromisoformat(value)
        return f"{d:%a %b} {d.day}"
    if kind == "time":
        m = _TIME_RE.match(value)
        if not m:
            raise ValueError(f"{slot}: not a 24-hour HH:MM time")
        t = datetime.strptime(value, "%H:%M")
        return f"{t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"
    raise ValueError(f"{slot}: not a slot the caller may fill")


def render(t: Template, property_short_name: str, slots: dict) -> str:
    """The template with its slots filled. `slots` must be exactly the
    caller slots; {property} comes from the directory."""
    given = set(slots or {})
    missing = sorted(t.caller_slots - given)
    extra = sorted(given - t.caller_slots)
    if missing or extra:
        raise ValueError("slots must match the template exactly"
                         + (f"; missing: {', '.join(missing)}" if missing else "")
                         + (f"; not in the template: {', '.join(extra)}" if extra else ""))
    values = {s: render_value(s, slots[s]) for s in t.caller_slots}
    if "property" in t.slots:
        values["property"] = property_short_name
    return _SLOT_RE.sub(lambda m: values[m.group(1)], t.text)


# Drafts for the operator's approval (package 1e). Not approved.
DRAFT_TEMPLATES = (
    Template(
        template_id="crew_notice_v1",
        kind="notice",
        action_key="crew_notice",
        roles=ROLES,
        language="en",
        # Package R12: discloses automated texts and exterior cameras and
        # asks for YES. The opt-out is "just reply", not STOP, because
        # STOP on the shared line may block the operator's own texts too
        # (audited spec 2.6 A).
        text=("Hi! This is Comfi Stays. Some scheduling texts from this "
              "number, like turnover times and visit check-ins, will now "
              "come from our system automatically. A person still reads "
              "every reply. Our entrances also have exterior cameras that "
              "record arrivals. Reply YES to get these texts. If you'd "
              "rather not, just reply and let us know."),
    ),
    Template(
        template_id="cleaner_late_checkout_ask_v1",
        kind="job",
        action_key="message_cleaner",
        roles=("cleaner",),
        language="en",
        # S3's cleaner ask (package Appendix A step 4).
        text=("Hi! Checkout at {property} on {date} may move from "
              "{from_time} to {to_time}. Can you start at {to_time} and "
              "still be done by {done_by}? Reply YES or NO."),
    ),
)


class TemplateRegistry:
    def __init__(self, templates):
        self._by_id: dict[str, Template] = {}
        self.rejected: dict[str, list[str]] = {}
        for t in templates:
            if t.template_id in self._by_id or t.template_id in self.rejected:
                raise ValueError(f"duplicate template id {t.template_id!r}")
            problems = template_problems(t)
            if problems:
                self.rejected[t.template_id] = problems
            else:
                self._by_id[t.template_id] = t

    def get(self, template_id: str) -> Template | None:
        return self._by_id.get(template_id)

    def ids(self) -> list[str]:
        return sorted(self._by_id)


def draft_registry() -> TemplateRegistry:
    return TemplateRegistry(DRAFT_TEMPLATES)


def template_from_dict(d: dict) -> Template:
    return Template(
        template_id=str(d["template_id"]), kind=str(d["kind"]),
        action_key=str(d["action_key"]), roles=tuple(d.get("roles") or ()),
        language=str(d.get("language") or "en"), text=str(d["text"]),
        approved_sha256=d.get("approved_sha256") or None,
        approved_by=str(d.get("approved_by") or ""),
        approved_at=str(d.get("approved_at") or ""))


def load_registry(path: str) -> TemplateRegistry:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != "crew-templates/v1":
        raise ValueError("not a crew-templates/v1 file")
    return TemplateRegistry(template_from_dict(d) for d in data.get("templates") or [])
