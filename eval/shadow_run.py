"""Shadow run: put live guest messages through the shipped triage path,
log-only. Spec: ~/workspace/hackathon/shadow-run-spec.md.

Input records (JSONL): {"message": str, "property": str}, the eval dataset
shape ("message_preview" and "property_id" are accepted as fallbacks).
Every message is redacted BEFORE any model call, then triaged with
tiers.decide under the escalate policy exactly as the sandbox and run_once
call it (no few-shot examples). One row per message goes to --out.

Redaction, in order: (1) the --redact-map JSON (string -> token, matched
case-insensitively, longest key first, one pass), (2) built-in patterns
for emails, URLs, phone numbers, and long confirmation-code-like tokens.
Property ids are mapped to a token; ids missing from the map get a
run-local token.

What this tool does NOT do, by construction: send or draft anything to a
guest, write to Hospitable, Turno, or any PMS, or make any network call
other than Meta Model API calls through the client handed to decide().
Raw message text lives only inside prepare(); it never reaches the client
and never reaches --out, whose rows carry tokens, categories, numbers,
and model names only. Live data, the map, and results must sit outside
this repo; the tool refuses paths inside it.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.client import MetaModelClient
from jev.config import Config
from jev.tiers import _guard, decide
from jev.types import Category, FAIL_SAFE

POLICY = "escalate"
DEFAULT_LIMIT = 60  # spec: the most recent 14 days, capped at 60 messages
REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
TOKEN_SHAPE = re.compile(r"[A-Z][A-Z0-9]*_[A-Z0-9]*\d+")  # PROP_03, PROP_L1, GUEST_019

# Applied in this order after the map. Email before URL (an address holds a
# domain); URL before phone and code (paths and query strings carry digits).
# The lookbehinds let a match start only at the beginning of a run (or just
# after a stray dot, as in "thanks...airbnb.com"), so a long separator-free
# run is scanned once, not once per position.
PATTERNS = [
    ("email", "[EMAIL]", re.compile(
        r"(?<![A-Za-z0-9._%+-])"
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("url", "[URL]", re.compile(
        r"(?:https?://|www\.)[^\s<>\"']+"
        r"|(?<![\w-])(?<![\w-]\.)"
        r"(?:[\w-]+\.)+(?:com|net|org|info|io|co|me|app|ly|us)\b(?:/[^\s<>\"']*)?",
        re.IGNORECASE)),
    ("phone", "[PHONE]", re.compile(
        r"(?<![\w+])(?:"
        r"\+\d(?:[ ().-]{0,2}\d){7,14}"                             # +CC international
        r"|(?:1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}"  # North American
        r")(?!\w)")),
    ("code", "[CODE]", re.compile(
        r"(?<![\w-])(?:"
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"     # UUID
        r"|\d{8,}"                                                  # long numeric ref
        r"|HM[A-Z0-9]{8}"                                           # Airbnb, even all-letter
        r"|(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{6,}[A-Z0-9]"  # caps+digits, 8+
        r"|(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}"        # any case, 12+
        r")(?![\w-])")),
]


class ShadowInputError(Exception):
    """A clean, content-free error about the inputs. Messages name files and
    line numbers only, never the text on the line."""


class Redactor:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping
        self._tokens = set(mapping.values())
        keys = sorted(mapping, key=len, reverse=True)
        # One alternation, longest key first: "Jordan Avery" wins over
        # "Jordan", and a token is never re-replaced by a later key. Case-
        # insensitive, so "jordan" and "JORDAN" are caught; the token comes
        # from the group that matched, never from re-casing the matched text.
        self._map_re = (re.compile("|".join(f"({re.escape(k)})" for k in keys), re.IGNORECASE)
                        if keys else None)
        self._key_tokens = [mapping[k] for k in keys]
        self.counts = {"map": 0, **{name: 0 for name, _, _ in PATTERNS}}
        self.unmapped: dict[str, str] = {}

    def __call__(self, text: str) -> str:
        if self._map_re:
            text, n = self._map_re.subn(lambda m: self._key_tokens[m.lastindex - 1], text)
            self.counts["map"] += n
        for name, token, pattern in PATTERNS:
            text, n = pattern.subn(token, text)
            self.counts[name] += n
        return text

    def property_token(self, raw: str) -> str:
        if not raw:
            return ""
        if raw in self.mapping:
            return self.mapping[raw]
        if raw in self._tokens or TOKEN_SHAPE.fullmatch(raw):
            return raw  # already tokenized upstream
        return self.unmapped.setdefault(raw, f"PROP_UNMAPPED_{len(self.unmapped) + 1}")


def load_redact_map(path: str) -> dict[str, str]:
    """Flat {raw: token}, or one level of grouping ({"guests": {raw: token}})
    like the eval dataset's private mapping."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ShadowInputError(f"redact map not found: {path}")
    except OSError as e:
        raise ShadowInputError(f"cannot read redact map {path}: {e.strerror}")
    except ValueError:
        raise ShadowInputError(f"redact map is not valid JSON: {path}")
    if not isinstance(data, dict):
        raise ShadowInputError("redact map must be a JSON object")
    flat = {}
    for key, value in data.items():
        pairs = value.items() if isinstance(value, dict) else [(key, value)]
        for raw, token in pairs:
            if not isinstance(raw, str) or not raw or not isinstance(token, str):
                raise ShadowInputError("redact map entries must be non-empty string -> string")
            flat[raw] = token
    return flat


def prepare(input_path: str, map_path: str, limit: int) -> tuple[list[tuple[str, str]], Redactor]:
    """Load, validate, and redact every record up front, so bad input fails
    before the first paid call. Returns [(redacted message, property token)];
    the raw text does not leave this function."""
    redactor = Redactor(load_redact_map(map_path))
    records = []
    try:
        with open(input_path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if len(records) >= limit:
                    break
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    raise ShadowInputError(f"{input_path} line {lineno}: not valid JSON")
                if not isinstance(rec, dict):
                    raise ShadowInputError(f"{input_path} line {lineno}: not a JSON object")
                text = rec.get("message") or rec.get("message_preview")
                if not isinstance(text, str) or not text.strip():
                    raise ShadowInputError(f"{input_path} line {lineno}: no message text")
                prop = str(rec.get("property") or rec.get("property_id") or "")
                records.append((redactor(text), redactor.property_token(prop)))
    except FileNotFoundError:
        raise ShadowInputError(f"input file not found: {input_path}")
    except UnicodeDecodeError:
        raise ShadowInputError(f"input file is not UTF-8 text: {input_path}")
    except OSError as e:
        raise ShadowInputError(f"cannot read input {input_path}: {e.strerror}")
    return records, redactor


def open_private(path: str):
    """Results are private: the file is created (or truncated) mode 600."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # an existing file keeps its old mode otherwise
    return os.fdopen(fd, "w", encoding="utf-8")


def run(records: list[tuple[str, str]], out_fh, client) -> list[dict]:
    """Triage each redacted record and stream its row to out_fh. The draft
    and the model's reason are not kept: rows hold no free text at all."""
    rows = []
    for i, (message, prop) in enumerate(records):
        d = decide(POLICY, message, {"property": prop}, client=client)
        row = {
            "index": i,
            "property": prop,
            "category": d.category.value,
            "confidence": d.confidence,
            "tier_path": d.tier_path,
            "cost_usd": round(d.cost_usd, 6),
            "failsafe": d.reason == FAIL_SAFE.reason,
        }
        out_fh.write(json.dumps(row) + "\n")
        out_fh.flush()
        rows.append(row)
    return rows


def summarize(rows: list[dict], redactor: Redactor) -> str:
    cats = Counter(r["category"] for r in rows)
    tiers = Counter(r["tier_path"] or "(no model call)" for r in rows)
    guard = _guard(Config.from_env())
    lines = [
        f"Shadow run, log-only (policy {POLICY}): {len(rows)} messages",
        "Redactions: " + ", ".join(f"{k} {v}" for k, v in redactor.counts.items()),
        "Decisions: " + ", ".join(f"{c.value} {cats.get(c.value, 0)}" for c in Category)
        + f" (fail-safe {sum(r['failsafe'] for r in rows)})",
        "Tier paths: " + ", ".join(f"{t} {n}" for t, n in tiers.most_common()),
        f"Total spend: ${sum(r['cost_usd'] for r in rows):.4f}",
        f"Daily spend guard: ${guard.spent():.2f} of ${guard.cap_usd:.2f} used today",
    ]
    if redactor.unmapped:
        lines.append(f"WARNING: {len(redactor.unmapped)} property id(s) not in the redact map; "
                     "run-local PROP_UNMAPPED_n tokens assigned")
    return "\n".join(lines)


def _inside_repo(path: str) -> bool:
    p = os.path.realpath(path)
    return p == REPO_ROOT or p.startswith(REPO_ROOT + os.sep)


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Log-only shadow triage of live guest messages.")
    ap.add_argument("--input", required=True, help="private JSONL of guest messages")
    ap.add_argument("--out", required=True, help="private results JSONL (written mode 600)")
    ap.add_argument("--redact-map", required=True, help="JSON {raw string: token}")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"max messages to triage (default {DEFAULT_LIMIT})")
    args = ap.parse_args(argv)
    if args.limit < 1:
        ap.error("--limit must be at least 1")

    for flag, path in (("--input", args.input), ("--redact-map", args.redact_map),
                       ("--out", args.out)):
        if _inside_repo(path):
            print(f"REFUSING TO RUN: {flag} {path} is inside the repo; live data, "
                  "the map, and results stay outside it.", file=sys.stderr)
            return 2
    try:
        records, redactor = prepare(args.input, args.redact_map, args.limit)
    except ShadowInputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if client is None:
        config = Config.from_env()
        if not config.api_key:
            print("REFUSING TO RUN: META_MODEL_API_KEY is not set; every decision "
                  "would be a fail-safe Confirm.", file=sys.stderr)
            return 2
        client = MetaModelClient(config)
    try:
        out_fh = open_private(args.out)
    except OSError as e:
        print(f"error: cannot write results {args.out}: {e.strerror}", file=sys.stderr)
        return 2
    with out_fh:
        rows = run(records, out_fh, client)
    print(summarize(rows, redactor))
    print(f"Wrote {len(rows)} rows to {args.out}")
    if rows and all(r["failsafe"] for r in rows):
        print("ERROR: every decision was fail-safe, so this run produced no real verdicts. "
              "Check the API key, base URL, network, and daily spend cap.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
