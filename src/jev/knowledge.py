"""Operator knowledge base: the per-property fact layer (built Oct 31, 2026).

Mirrors the live autopilot's fact sheets (autopilot-live/build_fact_sheets.py)
for this repo's staged fixture world, in plain deterministic stdlib code.

Each fact carries a status, and only one status may answer a guest:
  known       an operator-authored source (the property record, the
              operator's own messages) or an operator confirmation.
              answer() returns its value.
  unverified  e.g. a Knowledge Hub item (AI-detected or bot-edited). Shown
              to the operator in report() for confirmation, never answers.
  unknown     no operator source. Nothing is inferred; answer() is None.

Ingest safety, same rule as the live builder: door, lock, gate, and
laundry-room codes are never extracted, from any source. scrub_text()
splits text into sentences and drops any sentence that carries an entry
code (a code word next to an access word), a keypad or code word used with
digits, a run of 4+ digits, or dial-format digits (9-6-9-2). A sentence
that is clearly a wifi password fact keeps its digits, but never an entry
code riding along in the same sentence. KnowledgeBase scrubs every text
value at construction (wifi credentials excepted), and promote() refuses a
value the scrubber would cut.

The learning loop: promote() records an operator's confirmation as one
JSON line in a promotions file and flips the fact to known.
load_knowledge() rebuilds from the base sheet and replays that file as an
overlay (promotions win), so a confirmed fact survives rebuilds.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

KNOWN, UNVERIFIED, UNKNOWN = "known", "unverified", "unknown"
STATUSES = (KNOWN, UNVERIFIED, UNKNOWN)
OPERATOR_CONFIRMED = "operator-confirmed"

# Fact keys, in report order (the live builder's FACT_KEYS as of Oct 31, 2026).
FACT_KEYS = [
    "checkin_time", "checkout_time", "entry_method", "checkin_message_timing", "wifi_network", "wifi_password",
    "parking_instructions", "checkout_tasks", "trash_location", "laundry_details",
    "climate_notes", "thermostat_instructions", "tv_details", "coffee_details",
    "kitchen_details", "towels_toiletries", "extra_bedding", "noise_policy",
    "quiet_hours", "smoking_policy", "guidebook_url",
]
# Credentials are the fact itself, digits and all; never scrubbed.
_CREDENTIAL_KEYS = {"wifi_network", "wifi_password"}


@dataclass(frozen=True)
class Fact:
    value: object
    status: str
    source: str = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"fact status must be one of {STATUSES}, got {self.status!r}")


# ---------- ingest safety ----------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_ACCESS_RE = re.compile(r"\b(?:doors?|locks?|lock\s?box(?:es)?|gates?|garage|laundry|entry|smart\s?lock|deadbolt"
                        r"|keypad)\b", re.I)
_CODE_WORD_RE = re.compile(r"\b(?:codes?|pass\s?codes?|pin|combination|combo)\b", re.I)
_KEYPAD_RE = re.compile(r"\bkeypad\b", re.I)
_DIGIT_RE = re.compile(r"\d")
_DIGIT_RUN_RE = re.compile(r"\d{4,}")
_DIAL_RE = re.compile(r"\d(?:\s*[-\s]\s*\d){2,}")
_WIFI_RE = re.compile(r"\bwi[\s-]?fi\b", re.I)
_PASSWORD_RE = re.compile(r"\bpassword\b", re.I)


def _carries_code(sentence):
    has_digit = bool(_DIGIT_RE.search(sentence))
    code_word = bool(_CODE_WORD_RE.search(sentence))
    # An entry code, shown or described: never exempt, not even in a wifi sentence.
    if code_word and _ACCESS_RE.search(sentence):
        return True
    if has_digit and _KEYPAD_RE.search(sentence):
        return True
    if _WIFI_RE.search(sentence) and _PASSWORD_RE.search(sentence):
        return False
    if has_digit and code_word:
        return True
    return bool(_DIGIT_RUN_RE.search(sentence) or _DIAL_RE.search(sentence))


def _split(text):
    """(kept, dropped) sentences."""
    kept, dropped = [], []
    for s in _SENTENCE_RE.split(text or ""):
        s = s.strip()
        if s:
            (dropped if _carries_code(s) else kept).append(s)
    return kept, dropped


def scrub_text(text):
    """The text with every code-bearing sentence dropped."""
    return " ".join(_split(text)[0])


# ---------- the knowledge base ----------

def _ingest(key, row):
    row = row or {}
    value, source = row.get("value"), row.get("source")
    status = str(row.get("status") or UNKNOWN).lower()
    if isinstance(value, str) and key not in _CREDENTIAL_KEYS:
        value = scrub_text(value) or None
    if value is None:
        status = UNKNOWN
    return Fact(value, status, source)


class KnowledgeBase:
    """Per-property facts from a fact-sheet dict:
    {"properties": {property_id: {"name": ..., "facts": {fact_key: {"value", "status", "source"}}}}}
    The dict is read, never mutated."""

    def __init__(self, sheet):
        self._props = {}
        for pid, prop in (sheet.get("properties") or {}).items():
            facts = {key: _ingest(key, row) for key, row in (prop.get("facts") or {}).items()}
            self._props[pid] = {"name": prop.get("name"), "facts": facts}

    def property_ids(self):
        return list(self._props)

    def fact(self, property_id, fact_key):
        """The Fact on file, or an unknown Fact when there is none."""
        prop = self._props.get(property_id)
        return (prop and prop["facts"].get(fact_key)) or Fact(None, UNKNOWN)

    def answer(self, property_id, fact_key):
        """The fact's value when it is known; otherwise None. Unverified never answers."""
        f = self.fact(property_id, fact_key)
        return f.value if f.status == KNOWN else None

    def report(self, property_id):
        """Every fact key with its status, for operator review. Keys with no
        fact on file are listed as unknown."""
        if property_id not in self._props:
            raise KeyError(f"no such property: {property_id!r}")
        prop = self._props[property_id]
        keys = FACT_KEYS + [k for k in prop["facts"] if k not in FACT_KEYS]
        out = {"property_id": property_id, "name": prop["name"], "facts": [],
               KNOWN: [], UNVERIFIED: [], UNKNOWN: []}
        for key in keys:
            f = self.fact(property_id, key)
            out["facts"].append({"key": key, "status": f.status, "value": f.value, "source": f.source})
            out[f.status].append(key)
        return out

    def _check(self, property_id, fact_key, value, confirmed_by):
        if property_id not in self._props:
            raise KeyError(f"no such property: {property_id!r}")
        if fact_key not in FACT_KEYS and fact_key not in self._props[property_id]["facts"]:
            raise ValueError(f"unknown fact key: {fact_key!r}")
        if value is None or value == "":
            raise ValueError("a promoted fact needs a value")
        if not confirmed_by:
            raise ValueError("a promotion must name who confirmed it")
        if isinstance(value, str) and fact_key not in _CREDENTIAL_KEYS and _split(value)[1]:
            raise ValueError(f"refused: {fact_key} value carries a code; codes are never stored")

    def _set_confirmed(self, property_id, fact_key, value):
        self._props[property_id]["facts"][fact_key] = Fact(value, KNOWN, OPERATOR_CONFIRMED)

    def promote(self, property_id, fact_key, value, confirmed_by, promotions_path, now=None):
        """Record an operator's confirmation: append one JSON line to
        promotions_path, then mark the fact known (source operator-confirmed).
        The line is written first, so a fact is never known in memory without
        its record on disk. Returns the line written."""
        self._check(property_id, fact_key, value, confirmed_by)
        when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        line = {"property_id": property_id, "fact_key": fact_key, "value": value,
                "confirmed_by": confirmed_by, "promoted_at": when.isoformat(timespec="seconds")}
        with open(promotions_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        self._set_confirmed(property_id, fact_key, value)
        return line


def load_knowledge(base_sheet, promotions_path=None):
    """A KnowledgeBase from the base sheet with every recorded promotion
    replayed on top, in file order. A missing file means no overlay.
    Promotions for a property no longer in the base sheet are skipped."""
    kb = KnowledgeBase(base_sheet)
    if not promotions_path or not os.path.exists(promotions_path):
        return kb
    with open(promotions_path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                p = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{promotions_path}:{n}: not a JSON line ({e})") from None
            if p.get("property_id") not in kb._props:
                continue
            kb._check(p["property_id"], p.get("fact_key"), p.get("value"), p.get("confirmed_by"))
            kb._set_confirmed(p["property_id"], p["fact_key"], p["value"])
    return kb
