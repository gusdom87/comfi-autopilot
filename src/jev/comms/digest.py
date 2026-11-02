"""Operator digest (design package S1, floor item 1d; audited spec 2.1
Class A).

Reads the run logs and writes one plain-language digest for the
operator: what is waiting on him (held items with their drafts), new
decisions since the last digest, sends the actuator made, auto-disarm
events, and guard trips. Composed deterministically from the logs; no
model runs.

Sources, opened for reading only:
  - the live shadow's ledger (shadow-live/ledger.jsonl);
  - the actuator's logs when they exist (autopilot-live/:
    decisions.jsonl, send-log.jsonl, confirm-queue.jsonl,
    events.jsonl). Until the actuator runs there are none, and the
    digest says so.

Own listings by default. A row counts only when its property ID is in
the config's own_property_ids, matched by ID, never by name. Rows on
other listings are counted in one line and never mixed in. With
include_client_properties they get their own section at the end and
stay out of every own-listing count.

Per run, in a private directory outside any git work tree (mode 700,
files 600): digest-<UTC stamp>-<id>.txt (the digest) and
digest-<UTC stamp>-<id>.payload.json (the send-ready payload: a short
SMS version, its hash, a segment estimate, and the gates that block
sending it). digest-log.jsonl records every run.

Sending. The default builds only; nothing is sent. The only live send
this component can make is one text to the operator's own phone
through Quo, and only when every gate holds (send_blockers):
  - --send on the command line;
  - a2p_confirmed is true in the config. It ships false: the line's
    A2P 10DLC registration is unverified (package L1, Q4);
  - operator_number and from_number are valid E.164 numbers, not the
    same number;
  - a crew directory is configured and readable, and the operator
    number is not in it;
  - the Quo CLI path exists;
  - this digest was never sent or attempted before.
A refusal is logged with every reason and exits 3. The window does not
move, so the next digest still covers it. An attempt with an unclear
result is never retried.

"Since the last digest": the window runs from the end of the last
digest (state.json) to now; a first run looks back 24 hours. A
build-only run advances the window (the file is the digest of record)
unless --no-advance. A send run advances it only on a confirmed send.
Items waiting on the operator are not windowed: a held item stays under
"Waiting on you" until a host reply is recorded or 24 hours pass, and
older unanswered items are counted, never dropped silently. The shadow's
no_reply_24h grade is not a reply. A host reply the shadow recorded also
clears the actuator's queue copy of the same message.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ._files import (append_jsonl, check_private_path, ensure_private_dir,
                     read_jsonl, write_private)
from .crew_gate import Directory
from .transport import OperatorTransport, QuoCliOperatorTransport, SendResult, is_e164

CONFIG_SCHEMA = "operator-digest/config/v1"
PAYLOAD_SCHEMA = "operator-digest/payload/v1"
STATE_SCHEMA = "operator-digest/state/v1"
DEFAULT_TZ = "America/Chicago"
HELD_WINDOW = timedelta(hours=24)
FIRST_RUN_LOOKBACK = timedelta(hours=24)
SMS_MAX_CHARS = 700
EXIT_OK, EXIT_CONFIG, EXIT_REFUSED, EXIT_SEND_FAILED = 0, 2, 3, 4

ACTUATOR_FILES = {"decisions": "decisions.jsonl", "sends": "send-log.jsonl",
                  "queue": "confirm-queue.jsonl", "events": "events.jsonl"}

# Actuator holds that are guard trips (content safety), as opposed to
# holds on timing, rate, or thread position.
ACTUATOR_GUARDS = {
    "deny_list": "deny list hit on an Act draft (the actuator disarms on this)",
    "inbound_topic": "the guest's words raise money, damage, safety, or a stay change",
    "lint": "voice lint hard hit on the draft",
    "redaction_token": "a redaction token in the draft",
}
LANE_EVENTS = ("arm", "disarm", "stop", "allowlist_confirmed", "allowlist_missing",
               "interrupted_sends", "loop_exit")
_GUARD_RE = re.compile(r"^Guard \(([^)]*)\)")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DigestConfig:
    own_property_ids: frozenset
    shadow_ledger: str
    out_dir: str
    actuator_dir: str | None = None
    timezone: str = DEFAULT_TZ
    include_client_properties: bool = False
    operator_number: str | None = None
    from_number: str | None = None
    a2p_confirmed: bool = False
    quo_cli: str | None = None
    quo_pythonpath: str | None = None
    crew_directory: str | None = None


def config_from_dict(d: dict) -> DigestConfig:
    if not isinstance(d, dict) or d.get("schema") != CONFIG_SCHEMA:
        raise ConfigError(f"not an {CONFIG_SCHEMA} config")
    own = d.get("own_property_ids")
    if not isinstance(own, list) or not own or \
            not all(isinstance(x, str) and x.strip() for x in own):
        raise ConfigError("own_property_ids must list at least one property ID: the "
                          "digest covers the operator's own listings, by ID")
    for key in ("shadow_ledger", "out_dir"):
        if not isinstance(d.get(key), str) or not d[key]:
            raise ConfigError(f"{key} is required")
    for key in ("operator_number", "from_number", "quo_cli", "quo_pythonpath", "crew_directory"):
        if d.get(key) is not None and not isinstance(d[key], str):
            raise ConfigError(f"{key} must be a string or null, not {type(d[key]).__name__}")
    tz = d.get("timezone") or DEFAULT_TZ
    try:
        ZoneInfo(tz)
    except Exception as e:
        raise ConfigError(f"timezone {tz!r} is not an IANA zone") from e

    def path(key):
        v = d.get(key)
        return os.path.expanduser(v) if isinstance(v, str) and v else None

    return DigestConfig(
        own_property_ids=frozenset(x.strip() for x in own),
        shadow_ledger=path("shadow_ledger"),
        out_dir=check_private_path(path("out_dir")),
        actuator_dir=path("actuator_dir"),
        timezone=tz,
        include_client_properties=d.get("include_client_properties") is True,
        operator_number=d.get("operator_number") or None,
        from_number=d.get("from_number") or None,
        # Only the JSON value true confirms; "true", 1, or "yes" do not.
        a2p_confirmed=d.get("a2p_confirmed") is True,
        quo_cli=path("quo_cli"),
        quo_pythonpath=d.get("quo_pythonpath") or None,
        crew_directory=path("crew_directory"),
    )


def load_config(path: str) -> DigestConfig:
    with open(path, "r", encoding="utf-8") as fh:
        return config_from_dict(json.load(fh))


# --- sources ---

@dataclass
class Sources:
    shadow: list
    decisions: list
    sends: list
    queue: list
    events: list
    shadow_found: bool
    actuator_found: bool
    unreadable: int = 0


def read_sources(cfg: DigestConfig) -> Sources:
    shadow, bad = read_jsonl(cfg.shadow_ledger)
    logs, found = {}, False
    for name, filename in ACTUATOR_FILES.items():
        path = os.path.join(cfg.actuator_dir, filename) if cfg.actuator_dir else ""
        if path and os.path.exists(path):
            found = True
        rows, b = read_jsonl(path) if path else ([], 0)
        logs[name] = rows
        bad += b
    return Sources(shadow=shadow, shadow_found=os.path.exists(cfg.shadow_ledger),
                   actuator_found=found, unreadable=bad, **logs)


def _ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Item:
    source: str                    # shadow | actuator
    at: datetime                   # decided (or queued) at
    property_id: str
    property: str
    reservation_id: str
    message_at: str
    message_text: str
    category: str
    confidence: float | None
    draft: str
    reason: str
    failsafe: bool
    outcome: str = ""              # actuator outcome
    detail: str = ""
    triaged: bool = True
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        """One guest message, whichever log it came from."""
        at = _ts(self.message_at)
        return (self.reservation_id, at.astimezone(timezone.utc).isoformat() if at else self.message_at)


def _shadow_item(r: dict) -> Item | None:
    at = _ts(r.get("decided_at"))
    if at is None:
        return None
    return Item(source="shadow", at=at, property_id=str(r.get("property_id") or ""),
                property=str(r.get("property") or r.get("listing") or ""),
                reservation_id=str(r.get("reservation_id") or ""),
                message_at=str(r.get("message_at") or ""),
                message_text=str(r.get("message_text") or ""),
                category=str(r.get("engine_category") or ""),
                confidence=r.get("confidence"), draft=str(r.get("draft") or ""),
                reason=str(r.get("reason") or ""), failsafe=bool(r.get("failsafe")), raw=r)


def _actuator_item(r: dict) -> Item | None:
    at = _ts(r.get("decided_at"))
    if at is None:
        return None
    eng = r.get("engine") if isinstance(r.get("engine"), dict) else None
    return Item(source="actuator", at=at, property_id=str(r.get("property_id") or ""),
                property=str(r.get("property") or ""),
                reservation_id=str(r.get("reservation_id") or ""),
                message_at=str(r.get("message_at") or ""),
                message_text=str(r.get("message_text") or ""),
                category=str((eng or {}).get("category") or ""),
                confidence=(eng or {}).get("confidence"),
                draft=str((eng or {}).get("draft") or ""),
                reason=str((eng or {}).get("reason") or ""),
                failsafe=bool((eng or {}).get("failsafe")),
                outcome=str(r.get("outcome") or ""), detail=str(r.get("detail") or ""),
                triaged=eng is not None, raw=r)


def _queue_item(r: dict) -> Item | None:
    at = _ts(r.get("queued_at"))
    if at is None:
        return None
    return Item(source="actuator", at=at, property_id=str(r.get("property_id") or ""),
                property=str(r.get("property") or ""),
                reservation_id=str(r.get("reservation_id") or ""),
                message_at=str(r.get("message_at") or ""),
                message_text=str(r.get("message_text") or ""),
                category=str(r.get("engine_category") or ""),
                confidence=r.get("confidence"), draft=str(r.get("draft") or ""),
                reason=str(r.get("engine_reason") or ""), failsafe=bool(r.get("failsafe")),
                outcome=str(r.get("hold_reason") or ""), detail=str(r.get("detail") or ""),
                raw=r)


def guard_trips(item: Item) -> list[str]:
    """Plain-language guard trips on one decision."""
    out = []
    m = _GUARD_RE.match(item.reason)
    if m:
        out.append(f"Phase 0 guard held it ({m.group(1)})")
    if item.source == "shadow" and item.raw.get("never_graduate"):
        topics = ", ".join(item.raw.get("never_graduate_topics") or []) or "unnamed"
        out.append(f"never-graduate topic: {topics}")
    held_for = item.raw.get("reason") if item.source == "actuator" else None
    if held_for in ACTUATOR_GUARDS:
        label = ACTUATOR_GUARDS[held_for]
        out.append(f"{label}: {item.detail}" if item.detail else label)
    if item.failsafe:
        out.append("fail-safe: the model was unavailable or unparsable, so it held for you")
    return out


def _answered(item: Item) -> bool:
    """A host reply is on record for this shadow row. The live shadow
    grades host_outcome as host_replied or no_reply_24h, and only the
    first one is an answer."""
    return item.raw.get("answered_by_human") is True or \
        item.raw.get("host_outcome") == "host_replied"


def _shadow_waiting(item: Item, now: datetime) -> str:
    """'waiting', 'old' (past the held window, no answer recorded), or ''."""
    if item.category not in ("Act", "Confirm") or item.at > now:
        return ""
    if _answered(item):
        return ""
    return "waiting" if now - item.at <= HELD_WINDOW else "old"


# --- compose ---

@dataclass
class Digest:
    built_at: datetime
    window_from: datetime
    window_to: datetime
    tz: str
    own_listing_count: int
    new: list
    skipped_before_triage: Counter
    waiting: list
    waiting_old: int
    sends: list
    disarms: list
    lane_events: list
    trips: list                    # (item, label)
    client_new: list
    client_waiting: list
    left_out_client: int
    left_out_no_id: int
    shadow_found: bool
    actuator_found: bool
    unreadable: int
    include_client: bool
    text: str = ""
    sms: str = ""

    @property
    def digest_id(self) -> str:
        seed = f"{self.window_from.isoformat()}|{self.window_to.isoformat()}|{self.text}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def compose(cfg: DigestConfig, src: Sources, state: dict, now: datetime) -> Digest:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = _ts(state.get("watermark_utc")) or now - FIRST_RUN_LOOKBACK
    own = cfg.own_property_ids

    def in_window(t):
        return start < t <= now

    shadow = [i for i in map(_shadow_item, src.shadow) if i]
    decisions = [i for i in map(_actuator_item, src.decisions) if i]
    queue = [i for i in map(_queue_item, src.queue) if i]

    windowed = [i for i in shadow + decisions if in_window(i.at)]
    new = sorted((i for i in windowed if i.property_id in own and i.triaged), key=lambda i: i.at)
    skipped = Counter(str(i.raw.get("reason") or i.outcome or "held")
                      for i in windowed if i.property_id in own and not i.triaged)
    client_rows = [i for i in windowed if i.property_id and i.property_id not in own]
    no_id = sum(1 for i in windowed if not i.property_id)

    waiting, client_waiting, old = {}, {}, 0
    # Messages the shadow has seen answered. The actuator does not track
    # replies, so its queue copy of one of these is no longer waiting.
    answered = {i.key for i in shadow if i.message_at and _answered(i)}
    for i in shadow:
        st = _shadow_waiting(i, now)
        if not st:
            continue
        if i.property_id in own:
            if st == "waiting":
                waiting[i.key] = i
            else:
                old += 1
        elif i.property_id and st == "waiting":
            client_waiting[i.key] = i
    for i in queue:
        if i.at > now or i.key in answered:
            continue
        if now - i.at > HELD_WINDOW:
            old += 1 if i.property_id in own else 0
            continue
        # The actuator is the live lane: its copy of an unanswered
        # message wins over the shadow's.
        (waiting if i.property_id in own else client_waiting)[i.key] = i
    rank = {"Act": 0, "Confirm": 1}
    waiting_list = sorted(waiting.values(), key=lambda i: (rank.get(i.category, 2), i.at))

    sends = []
    for r in src.sends:
        at = _ts(r.get("attempted_at") or r.get("decided_at"))
        if at and in_window(at) and str(r.get("property_id") or "") in own:
            sends.append((at, r))
    sends.sort(key=lambda p: p[0])

    disarms, lane = [], []
    for e in src.events:
        at = _ts(e.get("at"))
        if not at or not in_window(at):
            continue
        if e.get("event") == "auto_disarm":
            disarms.append((at, e))
        elif e.get("event") in LANE_EVENTS:
            lane.append((at, e))

    trips = [(i, label) for i in new for label in guard_trips(i)]

    d = Digest(
        built_at=now, window_from=start, window_to=now, tz=cfg.timezone,
        own_listing_count=len(own), new=new, skipped_before_triage=skipped,
        waiting=waiting_list, waiting_old=old, sends=sends, disarms=sorted(disarms, key=lambda p: p[0]),
        lane_events=sorted(lane, key=lambda p: p[0]), trips=trips,
        client_new=sorted(client_rows, key=lambda i: i.at) if cfg.include_client_properties else [],
        client_waiting=sorted(client_waiting.values(), key=lambda i: i.at)
        if cfg.include_client_properties else [],
        left_out_client=0 if cfg.include_client_properties else len(client_rows),
        left_out_no_id=no_id, shadow_found=src.shadow_found, actuator_found=src.actuator_found,
        unreadable=src.unreadable, include_client=cfg.include_client_properties)
    d.text = render_text(d)
    d.sms = render_sms(d)
    return d


# --- render ---

def fmt_time(dt: datetime, tz: str) -> str:
    d = dt.astimezone(ZoneInfo(tz))
    return f"{d:%a %b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'} {d.tzname()}"


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _breakdown(items) -> str:
    c = Counter(i.category or "?" for i in items)
    parts = [f"{k} {c[k]}" for k in ("Act", "Confirm", "Ignore") if c[k]]
    parts += [f"{k} {v}" for k, v in sorted(c.items()) if k not in ("Act", "Confirm", "Ignore")]
    fs = sum(1 for i in items if i.failsafe)
    return ", ".join(parts) + (f"; {fs} fail-safe" if fs else "")


def _label(i: Item) -> str:
    if i.source == "actuator" and i.outcome:
        return f"{i.category or 'held'}, held by the actuator ({i.outcome})"
    return f"{i.category}, held" if i.category else "held"


def _send_status(r: dict) -> str:
    st = r.get("post_status")
    if st == "sent":
        return f"Sent (message ID {r.get('message_id_returned') or 'not returned'})"
    if st == "unknown":
        return "Status unknown; never retried. Re-read the thread before anyone replies"
    return f"Did not send ({st or 'failed'}); never retried"


def _waiting_block(i: Item, n: int, tz: str) -> list[str]:
    conf = f" {i.confidence:.2f}" if isinstance(i.confidence, (int, float)) else ""
    out = [f"{n}. {i.property or '(no listing name)'}: {_label(i)}{conf}. "
           f"Decided {fmt_time(i.at, tz)} ({i.source})."]
    if i.message_text:
        out.append(f"   Guest wrote: \"{_clip(i.message_text, 400)}\"")
    out.append(f"   Draft: \"{_clip(i.draft, 400)}\"" if i.draft else "   Draft: none written")
    if i.reason:
        out.append(f"   Why: {_clip(i.reason, 300)}")
    if i.source == "actuator":
        out.append("   (The actuator does not track replies; check the thread.)")
    return out


def _decision_line(i: Item, tz: str) -> str:
    conf = f" {i.confidence:.2f}" if isinstance(i.confidence, (int, float)) else ""
    outcome = ""
    if i.source == "actuator" and i.outcome:
        why = str(i.raw.get("reason") or "")
        outcome = f", {i.outcome}" + (f" ({why})" if why and why != i.outcome else "")
    return (f"- {fmt_time(i.at, tz)}. {i.property or '(no listing name)'}. "
            f"{i.category}{conf}{outcome}. {_clip(i.reason, 140)} ({i.source})")


def render_text(d: Digest) -> str:
    tz = d.tz
    L = ["COMFI AUTOPILOT: OPERATOR DIGEST",
         f"Built {fmt_time(d.built_at, tz)}. Covers {fmt_time(d.window_from, tz)} "
         f"to {fmt_time(d.window_to, tz)}.",
         (f"Own listings only ({d.own_listing_count}, matched by property ID)."
          if not d.include_client else
          f"Own listings ({d.own_listing_count}, matched by property ID). Client listings "
          "are shown separately at the end and are in none of the counts."),
         "Sources: shadow ledger " + ("read" if d.shadow_found else "not found")
         + "; actuator logs " + ("read" if d.actuator_found else "not found (it has not run)") + ".",
         "",
         "AT A GLANCE",
         f"- Waiting on you: {len(d.waiting)}",
         f"- New decisions: {len(d.new)}" + (f" ({_breakdown(d.new)})" if d.new else ""),
         f"- Sends: {len(d.sends)}" + (
             f" ({sum(1 for _, r in d.sends if r.get('post_status') == 'sent')} confirmed)"
             if d.sends else ""),
         f"- Auto-disarms: {len(d.disarms)}",
         f"- Guard trips: {len(d.trips)}",
         "",
         "WAITING ON YOU (held for your approval; Act first, then oldest first)"]
    if d.waiting:
        for n, i in enumerate(d.waiting, 1):
            L += _waiting_block(i, n, tz)
    else:
        L.append("Nothing is waiting.")
    if d.waiting_old:
        L.append(f"Also: {_plural(d.waiting_old, 'held item')} older than 24 hours with no "
                 "recorded answer. Check the inbox.")

    L += ["", f"NEW DECISIONS SINCE THE LAST DIGEST ({len(d.new)})"]
    L += [_decision_line(i, tz) for i in d.new] or ["None."]
    if d.skipped_before_triage:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(d.skipped_before_triage.items()))
        L.append(f"Actuator messages held before triage: {parts}.")

    L += ["", f"SENDS ({len(d.sends)})"]
    for at, r in d.sends:
        L.append(f"- {fmt_time(at, tz)}. {r.get('property') or '(no listing name)'}. "
                 f"{_send_status(r)}: \"{_clip(r.get('body_sent') or '', 300)}\"")
    if not d.sends:
        L.append("None." + ("" if d.actuator_found else " The actuator has no logs yet."))

    L += ["", f"AUTO-DISARMS ({len(d.disarms)})"]
    for at, e in d.disarms:
        L.append(f"- {fmt_time(at, tz)}. {_clip(e.get('reason') or 'no reason logged', 200)} "
                 f"(trigger: {e.get('trigger') or 'unknown'}).")
    if not d.disarms:
        L.append("None.")
    if d.lane_events:
        L.append("Lane changes:")
        for at, e in d.lane_events:
            why = e.get("reason") or e.get("note") or ""
            L.append(f"- {fmt_time(at, tz)}. {e.get('event')}" + (f": {_clip(str(why), 160)}" if why else "") + ".")

    L += ["", f"GUARD TRIPS ({len(d.trips)})"]
    for i, label in d.trips:
        L.append(f"- {fmt_time(i.at, tz)}. {i.property or '(no listing name)'}. {label} ({i.source})")
    if not d.trips:
        L.append("None.")

    L += ["", "LEFT OUT OF THIS DIGEST"]
    if not d.include_client:
        L.append(f"- {_plural(d.left_out_client, 'decision')} on client listings "
                 "(own listings only, by config).")
    L.append(f"- {_plural(d.left_out_no_id, 'decision')} with no property ID.")
    L.append(f"- {_plural(d.unreadable, 'unreadable log line')}.")

    if d.include_client:
        L += ["", "CLIENT LISTINGS (shown by config; observe-only, nothing is ever sent on these)",
              f"New decisions ({len(d.client_new)}):"]
        L += [_decision_line(i, tz) for i in d.client_new] or ["None."]
        L.append(f"Waiting ({len(d.client_waiting)}):")
        for n, i in enumerate(d.client_waiting, 1):
            L += _waiting_block(i, n, tz)
        if not d.client_waiting:
            L.append("Nothing is waiting.")
    return "\n".join(L) + "\n"


_SMS_REPLACE = {"‘": "'", "’": "'", "“": '"', "”": '"',
                "–": "-", "—": "-", "…": "...", " ": " "}
_GSM_EXT = set("[]{}\\^~|")


def sms_ascii(text: str) -> str:
    for a, b in _SMS_REPLACE.items():
        text = text.replace(a, b)
    return text.encode("ascii", "replace").decode("ascii")


def sms_segments(text: str) -> int:
    """GSM-7 segment estimate for ASCII text (extension characters count
    twice). An estimate; Quo's billing is the authority."""
    units = len(text) + sum(1 for c in text if c in _GSM_EXT)
    return 1 if units <= 160 else math.ceil(units / 153)


def render_sms(d: Digest, cap: int = SMS_MAX_CHARS) -> str:
    sent = sum(1 for _, r in d.sends if r.get("post_status") == "sent")
    lines = [f"Comfi Autopilot digest, {fmt_time(d.built_at, d.tz)}",
             f"Waiting on you: {len(d.waiting)}. New: {len(d.new)}. "
             f"Sends: {len(d.sends)}" + (f" ({len(d.sends) - sent} unconfirmed)" if len(d.sends) > sent else "")
             + f". Auto-disarms: {len(d.disarms)}. Guard trips: {len(d.trips)}."]
    for at, e in d.disarms[:2]:
        lines.append(f"Auto-disarm {fmt_time(at, d.tz)}: {_clip(e.get('reason') or '', 90)}")
    if len(d.disarms) > 2:
        lines.append(f"+{len(d.disarms) - 2} more auto-disarms in the digest.")
    if d.waiting:
        lines.append("Waiting:")
    shown = 0
    for n, i in enumerate(d.waiting, 1):
        what = f'draft "{_clip(i.draft, 70)}"' if i.draft else _clip(i.reason, 70) or "no draft"
        line = f"{n}. {i.property}, {i.category or 'held'}: {what}"
        after = len(d.waiting) - n
        tail = [f"+{after} more waiting; the full list is in the digest."] if after else []
        if len(sms_ascii("\n".join(lines + [line] + tail))) > cap:
            break
        lines.append(line)
        shown += 1
    if shown < len(d.waiting):
        lines.append(f"+{len(d.waiting) - shown} more waiting; the full list is in the digest.")
    return sms_ascii("\n".join(lines))[:cap]


# --- state, gates, run ---

def load_state(out_dir: str) -> dict:
    path = os.path.join(out_dir, "state.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        state = {}
    state.setdefault("schema", STATE_SCHEMA)
    state.setdefault("watermark_utc", None)
    state.setdefault("sent", {})
    return state


def save_state(out_dir: str, state: dict) -> None:
    write_private(os.path.join(out_dir, "state.json"), json.dumps(state, indent=1) + "\n")


def send_blockers(cfg: DigestConfig, digest_id: str, content: str, state: dict) -> list[str]:
    """Every reason the operator text can't go out. Empty only when all
    gates hold. The --send flag itself is checked by the caller."""
    out = []
    if cfg.a2p_confirmed is not True:
        out.append("A2P not confirmed: a2p_confirmed is false in the config. The business "
                   "line's A2P 10DLC registration is unverified, so nothing is texted, not even "
                   "to the operator.")
    op, frm = cfg.operator_number, cfg.from_number
    if not op:
        out.append("No operator number: operator_number is not set in the config.")
    elif not is_e164(op):
        out.append("operator_number is not a valid E.164 number.")
    if not frm:
        out.append("No sending line: from_number is not set in the config.")
    elif not is_e164(frm):
        out.append("from_number is not a valid E.164 number.")
    if op and frm and op == frm:
        out.append("operator_number is the sending line itself.")
    if not cfg.crew_directory:
        out.append("No crew directory configured: the operator number can't be checked "
                   "against worker numbers.")
    elif op:
        try:
            crew = Directory.load(cfg.crew_directory).numbers()
        except (OSError, ValueError) as e:
            out.append(f"The crew directory could not be read ({type(e).__name__}), so the "
                       "operator number can't be checked against worker numbers.")
        else:
            if op in crew:
                out.append("operator_number is a crew directory number; the digest goes only to "
                           "the operator's own phone.")
    if not cfg.quo_cli or not os.path.isfile(cfg.quo_cli):
        out.append("The Quo CLI path (quo_cli) is not set or does not exist.")
    if digest_id in state.get("sent", {}):
        prior = state["sent"][digest_id]
        out.append(f"This digest was already {prior.get('outcome')} at {prior.get('at')}; "
                   "a digest is never sent twice.")
    if not content:
        out.append("The SMS content is empty.")
    elif len(content) > SMS_MAX_CHARS:
        out.append(f"The SMS content is over {SMS_MAX_CHARS} characters.")
    return out


def default_transport_factory(cfg: DigestConfig) -> OperatorTransport:
    return QuoCliOperatorTransport(cfg.quo_cli, cfg.from_number, cfg.operator_number,
                                   pythonpath=cfg.quo_pythonpath)


@dataclass
class RunResult:
    exit_code: int
    digest_id: str
    digest_path: str
    payload_path: str
    outcome: str                   # built | refused | sent | send_failed | unknown
    reasons: list
    digest: Digest | None = None
    message_id: str | None = None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def run(cfg: DigestConfig, now: datetime, *, send: bool = False, advance: bool = True,
        transport_factory=default_transport_factory, err=None) -> RunResult:
    err = err or (lambda msg: print(msg, file=sys.stderr))
    ensure_private_dir(cfg.out_dir)
    lock_fd = os.open(os.path.join(cfg.out_dir, ".run.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            err("Another digest run holds the lock; nothing done.")
            return RunResult(EXIT_CONFIG, "", "", "", "locked", ["another run holds the lock"])
        return _run_locked(cfg, now, send=send, advance=advance,
                           transport_factory=transport_factory, err=err)
    finally:
        os.close(lock_fd)


def _run_locked(cfg, now, *, send, advance, transport_factory, err) -> RunResult:
    state = load_state(cfg.out_dir)
    d = compose(cfg, read_sources(cfg), state, now)
    did = d.digest_id
    base = os.path.join(cfg.out_dir, f"digest-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{did[:8]}")
    digest_path, payload_path = base + ".txt", base + ".payload.json"
    blockers = send_blockers(cfg, did, d.sms, state)
    payload = {
        "schema": PAYLOAD_SCHEMA, "digest_id": did, "built_at": _iso(now),
        "window": {"from": _iso(d.window_from), "to": _iso(d.window_to)},
        "channel": "quo_sms", "from": cfg.from_number, "to": cfg.operator_number,
        "content": d.sms, "content_sha256": hashlib.sha256(d.sms.encode("utf-8")).hexdigest(),
        "chars": len(d.sms), "segments_estimate": sms_segments(d.sms),
        "digest_file": digest_path,
        "counts": {"waiting": len(d.waiting), "new": len(d.new), "sends": len(d.sends),
                   "auto_disarms": len(d.disarms), "guard_trips": len(d.trips),
                   "left_out_client": d.left_out_client},
        "send_blockers": blockers,
        "sendable_with_send_flag": not blockers,
    }
    write_private(digest_path, d.text)
    write_private(payload_path, json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    log = os.path.join(cfg.out_dir, "digest-log.jsonl")
    row = {"at": _iso(now), "digest_id": did, "digest_file": digest_path,
           "window": payload["window"], "counts": payload["counts"]}

    def result(code, outcome, reasons, mid=None):
        return RunResult(code, did, digest_path, payload_path, outcome, reasons, d, mid)

    if not send:
        if advance:
            state["watermark_utc"], state["last_digest_id"] = _iso(d.window_to), did
            save_state(cfg.out_dir, state)
        append_jsonl(log, {**row, "mode": "build", "outcome": "built", "advanced": advance,
                           "send_blockers": blockers})
        return result(EXIT_OK, "built", [])

    if blockers:
        append_jsonl(log, {**row, "mode": "send", "outcome": "refused", "reasons": blockers})
        for b in blockers:
            err(f"REFUSED: {b}")
        return result(EXIT_REFUSED, "refused", blockers)

    transport = transport_factory(cfg)
    if not isinstance(transport, OperatorTransport) or \
            transport.operator_number != cfg.operator_number:
        why = ["The transport is not bound to the configured operator number."]
        append_jsonl(log, {**row, "mode": "send", "outcome": "refused", "reasons": why})
        err(f"REFUSED: {why[0]}")
        return result(EXIT_REFUSED, "refused", why)
    # Intent first: once this is on disk the digest can never send again.
    state["sent"][did] = {"at": _iso(now), "outcome": "sending"}
    save_state(cfg.out_dir, state)
    append_jsonl(log, {**row, "mode": "send", "outcome": "sending"})
    try:
        res = transport.send(d.sms)
    except Exception as e:
        res = SendResult("unknown", None, f"{type(e).__name__}: {e}"[:300])
    outcome = {"sent": "sent", "failed": "send_failed"}.get(res.status, "unknown")
    state["sent"][did] = {"at": _iso(now), "outcome": outcome, "message_id": res.message_id}
    if outcome == "sent" and advance:
        state["watermark_utc"], state["last_digest_id"] = _iso(d.window_to), did
    save_state(cfg.out_dir, state)
    append_jsonl(log, {**row, "mode": "send", "outcome": outcome, "message_id": res.message_id,
                       "detail": res.detail})
    if outcome != "sent":
        err(f"Send {outcome}: {res.detail}. Never retried.")
        return result(EXIT_SEND_FAILED, outcome, [res.detail])
    return result(EXIT_OK, "sent", [], res.message_id)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="operator_digest",
        description="Build the operator digest from the run logs. Builds only by default; "
                    "--send texts it to the operator's own phone, and only when every gate holds.")
    ap.add_argument("--config", required=True, help="path to the private digest config (JSON)")
    ap.add_argument("--send", action="store_true",
                    help="text the digest to the operator's own number (refused unless every gate holds)")
    ap.add_argument("--no-advance", action="store_true",
                    help="leave the 'since the last digest' window where it is (a preview)")
    ap.add_argument("--now", help="ISO time with an offset, for replays (default: now)")
    ap.add_argument("--print", dest="print_digest", action="store_true",
                    help="print the digest text to stdout")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("--now must be an ISO time", file=sys.stderr)
        return EXIT_CONFIG
    res = run(cfg, now, send=args.send, advance=not args.no_advance)
    if args.print_digest and res.digest:
        print(res.digest.text)
    print(f"digest {res.digest_id}: {res.outcome}")
    if res.digest_path:
        print(f"  digest:  {res.digest_path}")
        print(f"  payload: {res.payload_path}")
    if res.outcome == "built":
        print("  not sent (build only)")
    return res.exit_code
