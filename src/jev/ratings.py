"""Operator ratings and adaptation for autonomous sends (built Oct 31, 2026).

The learning loop. Every autonomous send (the pinned acknowledgment, a
pinned answer, a hub answer, a mapping answer) gets a rating slot: send
id, category, property, time, the exchange (the guest's words and the
sent text), and the exact source behind the text (the template, the fact,
the Hub item, the map lookup), with a score that stays null until the
operator rates it 1-10.

Adaptation rules, in code, deterministic, logged, and reversible by the
operator. They change RIGHTS and SOURCES, never a prompt:

  pause     any rating <= 3: the category sends nothing new until the
            operator clears it (`clear-pause`). The exchange and its exact
            source go to the review list, and that source is flagged: no
            send that uses it goes out until it is corrected (re-pinned,
            new hash) or the operator dismisses the flag.
  demote    the mean of the category's last 10 rated sends < 8, with at
            least 5 rated: the category drops to held (draft and hold) until
            the operator re-approves it (`reapprove`). Only ratings recorded
            after the last re-approval count.
  gold      a rating >= 9: the exchange is appended to the gold-examples
            CANDIDATE file for the next version's eval and few-shot set.
            Nothing here reads that file back; candidates are never
            applied to a live configuration.
  correct   fixing a flagged source re-pins it through the existing pinning
            flow (the caller does that), then `correct` records the new
            hash and clears the flag. A correction that does not change the
            hash is refused.

Rights are recomputed from the rating rows every time (a deleted "pause"
row cannot unpause anything); the pause, demote, gold, and review rows are
the audit log of each rule firing. Storage is append-only JSONL with an
flock and the VM's EPERM retry (AGENTS.md, Oct 30, 2026).
"""
import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone

LEDGER_FILE = "ratings.jsonl"
REVIEW_FILE = "ratings-review.jsonl"
GOLD_FILE = "gold-candidates.jsonl"
SCHEMA = "comfi-autopilot/ratings/v1"

PAUSE_AT_OR_BELOW = 3
DEMOTE_MEAN_BELOW = 8.0
DEMOTE_MIN_RATED = 5
WINDOW = 10
GOLD_AT_OR_ABOVE = 9

SEND, PAUSED, HELD = "send", "paused", "held"
ENV_DIR = "JEV_RATINGS_DIR"


def utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _retry_eperm(fn, sleep=time.sleep, waits=(5, 10, 20, 30, 45, 60)):
    for w in tuple(waits) + (None,):
        try:
            return fn()
        except PermissionError:
            if w is None:
                raise
            sleep(w)


class RatingsError(ValueError):
    pass


# The fields state() reads from each event, by type (audit R1, Nov 1, 2026):
# a row missing one, or holding the wrong type, is a malformed line, refused
# as RatingsError like a line that is not JSON, never a stray KeyError.
_ROW_FIELDS = {
    "send": {"send_id": str, "category": str, "source": dict},
    "rating": {"send_id": str, "score": int},
    "clear_pause": {"category": str},
    "reapprove": {"category": str},
    "demote": {"category": str},
}


def _row_problem(row):
    if not isinstance(row, dict):
        return "not a JSON object"
    if not isinstance(row.get("event"), str):
        return "no event name"
    for key, kind in _ROW_FIELDS.get(row["event"], {}).items():
        v = row.get(key)
        if not isinstance(v, kind) or isinstance(v, bool):
            return f"{row['event']} row: {key} is not a {kind.__name__}"
    if row["event"] == "send":
        flags = row["source"].get("flag_ids")
        if flags is not None and not (isinstance(flags, list) and all(isinstance(f, str) for f in flags)):
            return "send row: source.flag_ids is not a list of strings"
    if row["event"] in ("correction", "dismiss_flag") and not isinstance(row.get("flag_id"), (str, type(None))):
        return f"{row['event']} row: flag_id is not a string"
    return None


class Ledger:
    """One data dir: ratings.jsonl (every event), ratings-review.jsonl (the
    review list), gold-candidates.jsonl (candidates only)."""

    def __init__(self, base, clock=utcnow, sleep=time.sleep):
        self.base = base
        self.clock = clock
        self.sleep = sleep
        self.path = os.path.join(base, LEDGER_FILE)
        self.review_path = os.path.join(base, REVIEW_FILE)
        self.gold_path = os.path.join(base, GOLD_FILE)

    # -- storage --
    def _append(self, path, row):
        line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"

        def go():
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        _retry_eperm(go, sleep=self.sleep)
        return row

    def rows(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for n, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as e:
                raise RatingsError(f"{self.path}:{n}: not a JSON line ({e})") from None
            problem = _row_problem(row)
            if problem:
                raise RatingsError(f"{self.path}:{n}: malformed row ({problem})")
            row["_seq"] = n
            out.append(row)
        return out

    def _event(self, kind, **fields):
        row = {"schema": SCHEMA, "event": kind, "at": _iso(self.clock()), **fields}
        return self._append(self.path, row)

    # -- writes --
    def record_send(self, send_id, category, property_id, guest_message, sent_text, source, sent_at=None, **extra):
        """The rating slot for one autonomous send. `source` names the exact
        text or fact behind it and carries `flag_ids`, the keys a bad rating
        flags. Idempotent per send_id: a second slot is refused."""
        if not send_id or not category or not isinstance(send_id, str) or not isinstance(category, str):
            raise RatingsError("a send needs an id and a category")
        if any(r.get("event") == "send" and r.get("send_id") == send_id for r in self.rows()):
            raise RatingsError(f"send {send_id} already has a rating slot")
        flags = list((source or {}).get("flag_ids") or [])
        if not flags or not all(isinstance(f, str) for f in flags):
            raise RatingsError("a send's source must name at least one flag id")
        return self._event("send", send_id=send_id, category=category, property_id=property_id,
                           sent_at=_iso(sent_at) if isinstance(sent_at, datetime) else sent_at,
                           exchange={"guest_message": guest_message, "sent_text": sent_text},
                           source=dict(source), score=None, note=None, **extra)

    def rate(self, send_id, score, note=None, by="operator"):
        """Rate a send 1-10. Returns the adaptation rows this rating fired."""
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
            raise RatingsError("a rating is a whole number from 1 to 10")
        slot = self._slot(send_id)
        if slot is None:
            raise RatingsError(f"no send {send_id} on file")
        cat = slot["category"]
        self._event("rating", send_id=send_id, category=cat, score=score, note=note, by=by)
        fired = []
        if score <= PAUSE_AT_OR_BELOW:
            item = {"send_id": send_id, "category": cat, "property_id": slot.get("property_id"),
                    "score": score, "note": note, "exchange": slot.get("exchange"), "source": slot.get("source"),
                    "flag_ids": list(slot["source"].get("flag_ids") or []), "at": _iso(self.clock())}
            self._append(self.review_path, item)
            fired.append(self._event("pause", category=cat, send_id=send_id, score=score,
                                     flag_ids=item["flag_ids"], why=f"rating {score} <= {PAUSE_AT_OR_BELOW}"))
        st = self.state()["categories"].get(cat, {})
        if st.get("demoted") and not st.get("demote_logged"):
            fired.append(self._event("demote", category=cat, mean_last10=st.get("mean_last10"),
                                     rated_window=st.get("window_rated"),
                                     why=f"last-{WINDOW} mean {st.get('mean_last10')} < {DEMOTE_MEAN_BELOW}"))
        if score >= GOLD_AT_OR_ABOVE:
            gold = {"send_id": send_id, "category": cat, "property_id": slot.get("property_id"), "score": score,
                    "note": note, "exchange": slot.get("exchange"), "source": slot.get("source"),
                    "status": "candidate", "at": _iso(self.clock())}
            self._append(self.gold_path, gold)
            fired.append(self._event("gold_candidate", category=cat, send_id=send_id, score=score))
        return fired

    def clear_pause(self, category, note, by="operator"):
        if not note:
            raise RatingsError("clearing a pause needs the operator's note")
        if not self.state()["categories"].get(category, {}).get("paused"):
            raise RatingsError(f"{category} is not paused")
        return self._event("clear_pause", category=category, note=note, by=by)

    def reapprove(self, category, note, by="operator"):
        if not note:
            raise RatingsError("re-approval needs the operator's note")
        if not self.state()["categories"].get(category, {}).get("demoted"):
            raise RatingsError(f"{category} is not demoted")
        return self._event("reapprove", category=category, note=note, by=by)

    def correct(self, flag_id, old_sha256, new_sha256, note, by="operator"):
        """Record that a flagged source was fixed and re-pinned: its hash
        moved from old to new. Clears the flag."""
        if not note:
            raise RatingsError("a correction needs the operator's note")
        if flag_id not in self.state()["flags"]:
            raise RatingsError(f"{flag_id} is not flagged")
        if not new_sha256 or new_sha256 == old_sha256:
            raise RatingsError("a correction must re-pin the source with a new hash")
        return self._event("correction", flag_id=flag_id, old_sha256=old_sha256, new_sha256=new_sha256, note=note,
                           by=by)

    def dismiss_flag(self, flag_id, note, by="operator"):
        if not note:
            raise RatingsError("dismissing a flag needs the operator's note")
        if flag_id not in self.state()["flags"]:
            raise RatingsError(f"{flag_id} is not flagged")
        return self._event("dismiss_flag", flag_id=flag_id, note=note, by=by)

    # -- reads --
    def _slot(self, send_id):
        for r in self.rows():
            if r.get("event") == "send" and r.get("send_id") == send_id:
                return r
        return None

    def state(self):
        """Everything derived from the rows, in ledger order."""
        rows = self.rows()
        slots, ratings = {}, {}           # send_id -> slot; send_id -> (seq, score, note)
        last_clear, last_reapprove = {}, {}
        pause_trigger = {}                # category -> seq of the first <=3 rating since the last clear
        flags = {}                        # flag_id -> {"since", "send_id", "category"}
        demote_logged = {}
        for r in rows:
            ev, seq = r.get("event"), r["_seq"]
            if ev == "send":
                slots[r["send_id"]] = r
            elif ev == "rating" and r.get("send_id") in slots:
                ratings[r["send_id"]] = (seq, r["score"], r.get("note"))
                cat = slots[r["send_id"]]["category"]
                if r["score"] <= PAUSE_AT_OR_BELOW:
                    pause_trigger.setdefault(cat, (seq, r["send_id"]))
                    for f in slots[r["send_id"]]["source"].get("flag_ids") or []:
                        flags.setdefault(f, {"since": r.get("at"), "send_id": r["send_id"], "category": cat})
            elif ev == "clear_pause":
                last_clear[r["category"]] = seq
                pause_trigger.pop(r["category"], None)
            elif ev == "reapprove":
                last_reapprove[r["category"]] = seq
                demote_logged.pop(r["category"], None)
            elif ev == "demote":
                demote_logged[r["category"]] = seq
            elif ev in ("correction", "dismiss_flag"):
                flags.pop(r.get("flag_id"), None)
        cats = {}
        for sid, slot in slots.items():
            c = cats.setdefault(slot["category"], {"sends": 0, "rated": 0, "_scores": [], "_window": []})
            c["sends"] += 1
            if sid in ratings:
                seq, score, _ = ratings[sid]
                c["rated"] += 1
                c["_scores"].append((seq, score))
                if seq > last_reapprove.get(slot["category"], 0):
                    c["_window"].append((seq, score))
        out = {}
        for cat, c in sorted(cats.items()):
            scores = [s for _, s in sorted(c["_scores"])]
            window = [s for _, s in sorted(c["_window"])][-WINDOW:]
            mean10 = round(sum(window) / len(window), 2) if window else None
            demoted = len(window) >= DEMOTE_MIN_RATED and mean10 < DEMOTE_MEAN_BELOW
            trig = pause_trigger.get(cat)
            out[cat] = {
                "sends": c["sends"], "rated": c["rated"],
                "mean": round(sum(scores) / len(scores), 2) if scores else None,
                "mean_last10": round(sum(s for s in scores[-WINDOW:]) / len(scores[-WINDOW:]), 2) if scores else None,
                "window_mean": mean10, "window_rated": len(window),
                "paused": trig is not None, "paused_by": trig[1] if trig else None,
                "demoted": demoted, "demote_logged": cat in demote_logged,
            }
        return {"slots": slots, "ratings": ratings, "categories": out, "flags": flags}

    def rights(self, category, flag_ids=()):
        """(SEND | PAUSED | HELD, why) for a new send in `category` that uses
        the sources `flag_ids`."""
        st = self.state()
        c = st["categories"].get(category)
        if c and c["paused"]:
            return PAUSED, f"{category} is paused by a rating <= {PAUSE_AT_OR_BELOW} on send {c['paused_by']}"
        if c and c["demoted"]:
            return HELD, (f"{category} is demoted to held: last-{WINDOW} mean {c['window_mean']} < "
                          f"{DEMOTE_MEAN_BELOW} over {c['window_rated']} rated sends")
        open_flags = [f for f in flag_ids if f in st["flags"]]
        if open_flags:
            return PAUSED, f"source flagged for correction: {', '.join(open_flags)}"
        return SEND, None

    def report(self):
        """Per category: sends, rated count, mean, mean of the last 10, pauses
        and demotions in effect, open flags. Data only."""
        st = self.state()
        cats = {}
        for cat, c in st["categories"].items():
            right, why = self.rights(cat)
            cats[cat] = {"sends": c["sends"], "rated": c["rated"], "mean": c["mean"], "mean_last10": c["mean_last10"],
                         "paused": c["paused"], "demoted": c["demoted"], "rights": right, "why": why,
                         "open_flags": sorted(f for f, v in st["flags"].items() if v["category"] == cat)}
        return {"schema": SCHEMA, "categories": cats, "open_flags": sorted(st["flags"]),
                "unrated": sorted(s for s in st["slots"] if s not in st["ratings"])}


def flag_ids_for(category, property_id, template_sha256=None, template_id=None, facts=(), hub_source_ids=(),
                 geo_key=None):
    """The flag keys for a send's source, one per piece a correction would
    re-pin: the ack template by hash, an answer template by id, each fact by
    property and key, each Hub source by id, a map answer by destination."""
    out = []
    if category == "ack" and template_sha256:
        out.append(f"template:ack:{template_sha256[:16]}")
    if template_id:
        out.append(f"template:{template_id}")
    out += [f"fact:{property_id}:{k}" for k in facts]
    out += [f"hub:{property_id}:{s}" for s in hub_source_ids]
    if geo_key:
        out.append(f"geo:{geo_key}")
    return out


# ---------- operator CLI ----------

def main(argv=None, out=print, clock=utcnow):
    ap = argparse.ArgumentParser(prog="python3 -m jev.ratings", description="Rate autonomous sends; report.")
    ap.add_argument("--dir", default=os.environ.get(ENV_DIR), help=f"ratings data dir (or ${ENV_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rate")
    r.add_argument("send_id")
    r.add_argument("score", type=int)
    r.add_argument("--note")
    sub.add_parser("report").add_argument("--json", action="store_true")
    sub.add_parser("unrated")
    for name in ("clear-pause", "reapprove"):
        p = sub.add_parser(name)
        p.add_argument("category")
        p.add_argument("--note", required=True)
    d = sub.add_parser("dismiss-flag")
    d.add_argument("flag_id")
    d.add_argument("--note", required=True)
    c = sub.add_parser("correct")
    c.add_argument("flag_id")
    c.add_argument("--old-sha256", required=True)
    c.add_argument("--new-sha256", required=True)
    c.add_argument("--note", required=True)
    a = ap.parse_args(argv)
    if not a.dir:
        out(f"refused: no ratings dir (pass --dir or set ${ENV_DIR})")
        return 2
    led = Ledger(a.dir, clock=clock)
    try:
        if a.cmd == "rate":
            fired = led.rate(a.send_id, a.score, a.note)
            out(f"rated {a.send_id}: {a.score}")
            for f in fired:
                out(f"  rule fired: {f['event']} ({f.get('category')})")
        elif a.cmd == "report":
            rep = led.report()
            if a.json:
                out(json.dumps(rep, indent=1, sort_keys=True))
            else:
                out(f"{'category':16} {'sends':>5} {'rated':>5} {'mean':>5} {'last10':>6}  rights")
                for cat, c in sorted(rep["categories"].items()):
                    out(f"{cat:16} {c['sends']:>5} {c['rated']:>5} {c['mean'] if c['mean'] is not None else '-':>5} "
                        f"{c['mean_last10'] if c['mean_last10'] is not None else '-':>6}  {c['rights']}"
                        + (f" ({c['why']})" if c["why"] else ""))
                out(f"open flags: {', '.join(rep['open_flags']) or 'none'}")
        elif a.cmd == "unrated":
            for s in led.report()["unrated"]:
                out(s)
        elif a.cmd == "clear-pause":
            led.clear_pause(a.category, a.note)
            out(f"cleared the pause on {a.category}")
        elif a.cmd == "reapprove":
            led.reapprove(a.category, a.note)
            out(f"re-approved {a.category}")
        elif a.cmd == "dismiss-flag":
            led.dismiss_flag(a.flag_id, a.note)
            out(f"dismissed {a.flag_id}")
        elif a.cmd == "correct":
            led.correct(a.flag_id, a.old_sha256, a.new_sha256, a.note)
            out(f"corrected {a.flag_id}")
    except RatingsError as e:
        out(f"refused: {e}")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
