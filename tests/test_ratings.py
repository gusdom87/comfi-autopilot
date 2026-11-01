"""Tests for the ratings ledger and its adaptation rules (ratings.py).
Temp dirs only; pseudonym exchanges."""
import ast
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev import ratings as R

T0 = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def _led(tmp):
    return R.Ledger(tmp, clock=Clock(), sleep=lambda s: None)


def _send(led, sid, cat="hub_qa", flags=None):
    return led.record_send(sid, cat, "cedar-1a", "Where does the trash go?", "Hi! Trash goes in the gray bins.",
                           {"kind": "hub_item", "flag_ids": flags or [f"hub:cedar-1a:{cat}-src"]}, sent_at=T0)


def test_a_slot_per_send_null_until_rated_and_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        row = _send(led, "s1")
        assert row["score"] is None and row["exchange"]["sent_text"].startswith("Hi!")
        try:
            _send(led, "s1")
            raise AssertionError("second slot accepted")
        except R.RatingsError:
            pass
        assert led.report()["unrated"] == ["s1"]
        for bad in (0, 11, 7.5, True, "8"):
            try:
                led.rate("s1", bad)
                raise AssertionError(f"rating {bad!r} accepted")
            except R.RatingsError:
                pass
        try:
            led.rate("nope", 8)
            raise AssertionError("rated a send that does not exist")
        except R.RatingsError:
            pass


def test_a_rating_of_three_or_less_pauses_the_category_and_flags_the_source():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        _send(led, "s1")
        _send(led, "s2")
        assert led.rights("hub_qa") == (R.SEND, None)
        fired = led.rate("s1", 3, note="wrong bins")
        assert [f["event"] for f in fired] == ["pause"]
        right, why = led.rights("hub_qa")
        assert right == R.PAUSED and "s1" in why
        assert led.rights("geo_distance")[0] == R.SEND  # other categories untouched
        review = [json.loads(x) for x in open(os.path.join(tmp, R.REVIEW_FILE))]
        assert review[0]["exchange"]["guest_message"] == "Where does the trash go?"
        assert review[0]["source"]["flag_ids"] == ["hub:cedar-1a:hub_qa-src"]
        led.rate("s2", 10)                       # a later good rating does not lift the pause
        assert led.rights("hub_qa")[0] == R.PAUSED
        led.clear_pause("hub_qa", "fixed the bin location in the Hub")
        right, why = led.rights("hub_qa", flag_ids=["hub:cedar-1a:hub_qa-src"])
        assert right == R.PAUSED and "flagged" in why  # the flagged source stays blocked until corrected
        assert led.rights("hub_qa", flag_ids=["hub:cedar-1a:other"])[0] == R.SEND
        led.correct("hub:cedar-1a:hub_qa-src", "a" * 64, "b" * 64, "re-pinned")
        assert led.rights("hub_qa", flag_ids=["hub:cedar-1a:hub_qa-src"]) == (R.SEND, None)


def test_rights_are_recomputed_from_ratings_not_from_the_pause_row():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        _send(led, "s1")
        led.rate("s1", 2)
        path = os.path.join(tmp, R.LEDGER_FILE)
        rows = [r for r in open(path).read().splitlines() if '"event": "pause"' not in r]
        open(path, "w").write("\n".join(rows) + "\n")
        assert led.rights("hub_qa")[0] == R.PAUSED


def test_last_ten_mean_below_eight_with_five_rated_demotes_until_reapproved():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        for i in range(6):
            _send(led, f"s{i}", cat="geo_distance")
        for i, score in enumerate((7, 7, 7, 7)):
            led.rate(f"s{i}", score)
        assert led.rights("geo_distance")[0] == R.SEND           # four rated: not yet
        fired = led.rate("s4", 7)
        assert [f["event"] for f in fired] == ["demote"]
        right, why = led.rights("geo_distance")
        assert right == R.HELD and "7.0" in why
        assert [f["event"] for f in led.rate("s5", 8)] == []      # still demoted, not logged twice
        led.reapprove("geo_distance", "checked every answer by hand")
        assert led.rights("geo_distance")[0] == R.SEND            # the window restarts after re-approval
        rep = led.report()["categories"]["geo_distance"]
        assert rep["rated"] == 6 and rep["mean"] == round((7 * 5 + 8) / 6, 2) and rep["demoted"] is False


def test_only_the_last_ten_count():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        for i in range(12):
            _send(led, f"s{i}", cat="ack", flags=["template:ack:abc"])
        led.rate("s0", 4)
        led.rate("s1", 4)
        for i in range(2, 12):
            led.rate(f"s{i}", 9)
        c = led.report()["categories"]["ack"]
        assert c["mean_last10"] == 9.0 and c["rights"] == R.SEND


def test_nine_or_more_goes_to_the_gold_candidates_and_nothing_reads_it_back():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        _send(led, "s1")
        _send(led, "s2")
        led.rate("s1", 9, note="perfect")
        led.rate("s2", 8)
        gold = [json.loads(x) for x in open(os.path.join(tmp, R.GOLD_FILE))]
        assert [g["send_id"] for g in gold] == ["s1"] and gold[0]["status"] == "candidate"
    src = open(os.path.join(os.path.dirname(__file__), "..", "src", "jev", "ratings.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
    assert all("gold" not in ast.unparse(n) for n in reads)   # the module never opens the gold file to read


def test_corrections_need_a_new_hash_and_an_open_flag():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        _send(led, "s1", flags=["fact:cedar-1a:trash_location"])
        try:
            led.correct("fact:cedar-1a:trash_location", "a", "b", "x")
            raise AssertionError("corrected an unflagged source")
        except R.RatingsError:
            pass
        led.rate("s1", 1)
        try:
            led.correct("fact:cedar-1a:trash_location", "a" * 64, "a" * 64, "same text")
            raise AssertionError("a correction that changes nothing was accepted")
        except R.RatingsError:
            pass
        led.dismiss_flag("fact:cedar-1a:trash_location", "the fact was right; the guest misread it")
        assert led.state()["flags"] == {}


def test_clear_and_reapprove_refuse_when_nothing_is_in_effect():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        for fn in (led.clear_pause, led.reapprove):
            try:
                fn("hub_qa", "note")
                raise AssertionError("refusal expected")
            except R.RatingsError:
                pass


def test_appends_ride_out_eperm():
    with tempfile.TemporaryDirectory() as tmp:
        waits, real, calls = [], os.write, []

        def flaky(fd, data):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(1, "Operation not permitted")
            return real(fd, data)
        led = R.Ledger(tmp, clock=Clock(), sleep=waits.append)
        os.write = flaky
        try:
            _send(led, "s1")
        finally:
            os.write = real
        assert waits == [5] and len(led.rows()) == 1
        assert oct(os.stat(os.path.join(tmp, R.LEDGER_FILE)).st_mode & 0o777) == "0o600"


CORRUPT_LINES = (
    "{not json",
    "[1, 2]",
    '"a string"',
    "17",
    '{"no_event": true}',
    '{"event": "send", "category": "hub_qa", "source": {"flag_ids": ["x"]}}',                 # no send_id
    '{"event": "send", "send_id": "s9", "category": "hub_qa", "source": "not a dict"}',
    '{"event": "send", "send_id": "s9", "category": "hub_qa", "source": {"flag_ids": 5}}',
    '{"event": "send", "send_id": ["s9"], "category": "hub_qa", "source": {"flag_ids": ["x"]}}',
    '{"event": "rating", "send_id": "s1", "score": "8"}',
    '{"event": "rating", "send_id": "s1", "score": true}',
    '{"event": "rating", "send_id": "s1"}',
    '{"event": "clear_pause"}',
    '{"event": "reapprove", "category": 3}',
    '{"event": "correction", "flag_id": ["x"]}',
)


def test_a_malformed_ledger_line_is_a_ratings_error_on_every_read():
    """Audit R1 (Nov 1, 2026): every malformed line, JSON or not, is refused
    as RatingsError (never a KeyError or TypeError out of state()), so a
    caller that catches RatingsError catches every corrupt ledger."""
    for bad in CORRUPT_LINES:
        with tempfile.TemporaryDirectory() as tmp:
            led = _led(tmp)
            _send(led, "s1")
            with open(led.path, "a", encoding="utf-8") as fh:
                fh.write(bad + "\n")
            for read in (led.rows, led.state, led.report, lambda: led.rights("hub_qa", ["x"])):
                try:
                    read()
                    raise AssertionError(f"read accepted {bad!r}")
                except R.RatingsError as e:
                    assert ":2: " in str(e), (bad, e)
            out = []
            assert R.main(["--dir", tmp, "report"], out=out.append) == 2 and out[0].startswith("refused"), bad
    with tempfile.TemporaryDirectory() as tmp:  # a clean ledger still reads
        led = _led(tmp)
        _send(led, "s1")
        led.rate("s1", 9)
        assert led.rights("hub_qa") == (R.SEND, None)
        for bad_id in (7, None, ""):
            try:
                led.record_send(bad_id, "hub_qa", "cedar-1a", "q", "a", {"flag_ids": ["x"]})
                raise AssertionError(f"send id {bad_id!r} accepted")
            except R.RatingsError:
                pass


def test_flag_ids_name_each_piece_a_correction_would_repin():
    assert R.flag_ids_for("ack", "cedar-1a", template_sha256="ab" * 32) == ["template:ack:" + "ab" * 8]
    assert R.flag_ids_for("wifi", "cedar-1a", template_id="tpl-wifi", facts=("wifi_password",)) == [
        "template:tpl-wifi", "fact:cedar-1a:wifi_password"]
    assert R.flag_ids_for("hub_qa", "cedar-1a", hub_source_ids=("hub:1", "fact:trash")) == [
        "hub:cedar-1a:hub:1", "hub:cedar-1a:fact:trash"]
    assert R.flag_ids_for("geo_distance", "cedar-1a", geo_key="cedar-1a|sakura garden") == [
        "geo:cedar-1a|sakura garden"]


def test_cli_rate_report_and_refusals():
    with tempfile.TemporaryDirectory() as tmp:
        led = _led(tmp)
        _send(led, "s1")
        out = []
        assert R.main(["--dir", tmp, "rate", "s1", "2", "--note", "wrong"], out=out.append) == 0
        assert any("pause" in line for line in out)
        out.clear()
        assert R.main(["--dir", tmp, "report", "--json"], out=out.append) == 0
        rep = json.loads(out[0])
        assert rep["categories"]["hub_qa"]["rights"] == R.PAUSED and rep["categories"]["hub_qa"]["rated"] == 1
        out.clear()
        assert R.main(["--dir", tmp, "rate", "s1", "11"], out=out.append) == 2 and out[0].startswith("refused")
        assert R.main(["rate", "s1", "5"], out=out.append) == 2  # no dir
        out.clear()
        assert R.main(["--dir", tmp, "report"], out=out.append) == 0
        assert out[0].startswith("category") and "paused" in out[1]
