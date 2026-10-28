import ast, json, os, stat, subprocess, sys, time
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
import shadow_run
from shadow_run import Redactor, main

REPO = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(REPO, "eval", "shadow_run.py")


# -- redaction ----------------------------------------------------------

def test_map_longest_key_first():
    r = Redactor({"Jordan": "GUEST_02", "Jordan Avery": "GUEST_01"})
    assert r("Jordan Avery and Jordan") == "GUEST_01 and GUEST_02"
    assert r.counts["map"] == 2


def test_map_is_one_pass_tokens_never_rereplaced():
    r = Redactor({"Ann": "TOKEN_1", "TOKEN": "LEAK"})
    assert r("Ann says hi") == "TOKEN_1 says hi"


def test_map_runs_before_patterns_and_tokens_survive_them():
    r = Redactor({"jordan.avery@example.com": "EMAIL_07", "Avery": "GUEST_019"})
    assert r("mail jordan.avery@example.com, Avery") == "mail EMAIL_07, GUEST_019"
    assert r.counts["map"] == 2 and r.counts["email"] == 0


def test_map_is_case_insensitive():
    # Guests sign in lowercase or shout; the map holds the name as booked.
    r = Redactor({"Jordan": "G"})
    assert r("jordan JORDAN Jordan's") == "G G G's"
    assert r.counts["map"] == 3
    r = Redactor({"Jordan Avery": "GUEST_01", "Jordan": "GUEST_02"})
    assert r("HI ITS JORDAN AVERY, jordan avery") == "HI ITS GUEST_01, GUEST_01"
    # The token comes from the key that matched, so a casing that does not
    # round-trip (Turkish dotless i) still redacts instead of a KeyError.
    assert Redactor({"Yıldız": "GUEST_09"})("hi its YILDIZ") == "hi its GUEST_09"


def test_load_redact_map_flat_and_grouped(tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"guests": {"Jordan": "GUEST_01"}, "Cedar Hollow 2": "PROP_CH2"}))
    assert shadow_run.load_redact_map(str(p)) == {"Jordan": "GUEST_01", "Cedar Hollow 2": "PROP_CH2"}
    p.write_text(json.dumps({"": "GUEST_01"}))
    with pytest.raises(shadow_run.ShadowInputError):
        shadow_run.load_redact_map(str(p))


@pytest.mark.parametrize("raw", [
    "jordan.avery@example.com", "jo.ann+stay@mail.example.co.uk", "A_B-9@x.io"])
def test_email_pattern(raw):
    r = Redactor({})
    assert r(f"write me at {raw}.") == "write me at [EMAIL]."
    assert r.counts["email"] == 1


@pytest.mark.parametrize("raw", [
    "(312) 555-0187", "312-555-0187", "312.555.0187", "3125550187",
    "1-312-555-0187", "+1 312 555 0187", "+1 (312) 555-0187", "+44 20 7946 0958"])
def test_phone_pattern(raw):
    r = Redactor({})
    assert r(f"call me at {raw} tonight") == "call me at [PHONE] tonight"
    assert r.counts["phone"] == 1


@pytest.mark.parametrize("raw", [
    "https://www.airbnb.com/rooms/123?check_in=2026-10-28", "http://x.co/abc",
    "www.example.org/path", "airbnb.com/h/cedar-hollow-farmhouse", "comfistays.info"])
def test_url_pattern(raw):
    r = Redactor({})
    assert r(f"see {raw} for details") == "see [URL] for details"
    assert r.counts["url"] == 1


@pytest.mark.parametrize("raw", [
    "HMXK4P9QZ2", "HMJYSZFNPA", "HA-7KD93XQ", "5123456789012", "41234567",
    "3f2a1b4c-9d8e-4f00-a1b2-c3d4e5f60718", "wifipass2024xyz"])
def test_code_pattern(raw):
    r = Redactor({})
    assert r(f"reservation {raw} is mine") == "reservation [CODE] is mine"
    assert r.counts["code"] == 1


def test_patterns_leave_ordinary_text_alone():
    text = ("Checking in 10/28/2026 or 2026-10-28 at 3:30pm, the 2-bedroom "
            "check-in is $1,250.00 for PROP_03 and GUEST_019, unit 2B, code 4821.")
    r = Redactor({})
    assert r(text) == text
    assert sum(r.counts.values()) == 0


@pytest.mark.parametrize("blob", ["a." * 10_000, "a_b-c." * 3_000, "a" * 60_000],
                         ids=["dotted", "mixed-separators", "letters"])
def test_long_separator_free_run_redacts_in_linear_time(blob):
    # A pasted blob used to cost seconds to minutes (email and URL rescanned
    # from every position); a stray dot before a bare domain still redacts.
    r = Redactor({})
    start = time.perf_counter()
    out = r(f"{blob} mail jordan.avery@example.com, thanks...airbnb.com/h/x")
    assert time.perf_counter() - start < 1.0
    assert out == f"{blob} mail [EMAIL], thanks...[URL]"


def test_property_tokens():
    r = Redactor({"Cedar Hollow Unit 2": "PROP_CH2"})
    assert r.property_token("Cedar Hollow Unit 2") == "PROP_CH2"
    assert r.property_token("PROP_03") == "PROP_03"          # already a token
    assert r.property_token("") == ""
    first = r.property_token("Bright Cedar Hollow Farmhouse")
    assert first == "PROP_UNMAPPED_1"
    assert r.property_token("Bright Cedar Hollow Farmhouse") == first  # stable within a run
    assert r.property_token("3f2a1b4c-9d8e") == "PROP_UNMAPPED_2"


def test_export_prop_ln_ids_are_already_tokens():
    # export.py emits PROP_L1..PROP_L4 and its map holds guest names only.
    r = Redactor({"Jordan": "GUEST_01"})
    assert [r.property_token(p) for p in ("PROP_L1", "PROP_L4")] == ["PROP_L1", "PROP_L4"]
    assert r.unmapped == {}


# -- runner -------------------------------------------------------------

class RecordingClient:
    """Fake model client: records everything it is handed."""
    def __init__(self):
        self.seen = []

    def complete(self, model, system, user):
        self.seen.append((model, system, user))
        if system.startswith("You draft"):
            text = "On it, someone is headed over."
        elif "leak" in user:
            text = json.dumps({"category": "Act", "confidence": 0.92, "route": "host", "reason": "r"})
        elif "door" in user:  # below the threshold: escalates, then drafts
            text = json.dumps({"category": "Act", "confidence": 0.6, "route": "host", "reason": "r"})
        else:
            text = json.dumps({"category": "Confirm", "confidence": 0.9, "route": "host", "reason": "r"})
        return {"output_text": text, "usage": {"input_tokens": 100, "output_tokens": 20}}


RAW = [
    {"message": "Hi, Jordan Avery here. The front door code isn't working, call me at "
                "(312) 555-0187 or jordan.avery@example.com", "property": "Cedar Hollow Unit 2"},
    {"message": "Booking HMXK4P9QZ2: can we check out at noon? Details at "
                "https://www.airbnb.com/trips/v1/reservation-details/abc123def456",
     "property": "3f2a1b4c-9d8e-4f00-a1b2-c3d4e5f60718"},
    {"message": "Jordan again, there is a leak under the sink. +44 20 7946 0958, "
                "ref 5123456789012", "property": "Cedar Hollow Unit 2"},
]
MAP = {"Jordan Avery": "GUEST_01", "Jordan": "GUEST_01", "Cedar Hollow Unit 2": "PROP_CH2"}
SENSITIVE = [
    "Jordan", "Avery", "Cedar Hollow Unit 2", "(312) 555-0187", "555-0187",
    "jordan.avery@example.com", "example.com", "HMXK4P9QZ2", "airbnb.com", "abc123def456",
    "3f2a1b4c", "+44 20 7946 0958", "7946 0958", "5123456789012"]


def write_inputs(tmp_path, records=RAW, mapping=MAP):
    inp, mp = tmp_path / "input.jsonl", tmp_path / "map.json"
    inp.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    mp.write_text(json.dumps(mapping))
    return inp, mp


def windows(text, n=16):
    return {text[i:i + n] for i in range(max(len(text) - n + 1, 1))}


def test_runner_client_sees_only_redacted_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JEV_SPEND_STATE", str(tmp_path / "spend.json"))
    inp, mp = write_inputs(tmp_path)
    out = tmp_path / "shadow" / "results.jsonl"
    fake = RecordingClient()
    assert main(["--input", str(inp), "--out", str(out), "--redact-map", str(mp)],
                client=fake) == 0

    # 3 messages: Confirm (1 call), Act at 0.6 (fast + deep + draft), Act at 0.92 (fast + draft)
    assert len(fake.seen) == 6
    sent = "\n".join(system + "\n" + user for _, system, user in fake.seen)
    for lit in SENSITIVE:
        assert lit not in sent, lit
    for r in RAW:
        assert r["message"] not in sent
    for token in ("GUEST_01", "PROP_CH2", "PROP_UNMAPPED_1", "[PHONE]", "[EMAIL]", "[URL]", "[CODE]"):
        assert token in sent, token        # the redacted text really went through

    body = out.read_text()
    printed = capsys.readouterr().out
    for lit in SENSITIVE:
        assert lit not in body and lit not in printed, lit
    for r in RAW:
        assert not any(w in body for w in windows(r["message"]))
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600

    rows = [json.loads(l) for l in body.splitlines()]
    assert [r["index"] for r in rows] == [0, 1, 2]
    assert [r["property"] for r in rows] == ["PROP_CH2", "PROP_UNMAPPED_1", "PROP_CH2"]
    assert [r["category"] for r in rows] == ["Act", "Confirm", "Act"]
    assert "->" in rows[0]["tier_path"] and "->" not in rows[2]["tier_path"]
    assert all(r["cost_usd"] > 0 and not r["failsafe"] for r in rows)
    assert set(rows[0]) == {"index", "property", "category", "confidence",
                            "tier_path", "cost_usd", "failsafe"}
    assert "Decisions: Confirm 1, Act 2, Ignore 0" in printed
    assert "Total spend: $" in printed and "WARNING: 1 property id" in printed


def test_limit_caps_messages(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JEV_SPEND_STATE", str(tmp_path / "spend.json"))
    inp, mp = write_inputs(tmp_path)
    out = tmp_path / "results.jsonl"
    fake = RecordingClient()
    assert main(["--input", str(inp), "--out", str(out), "--redact-map", str(mp),
                 "--limit", "1"], client=fake) == 0
    assert len(out.read_text().splitlines()) == 1
    assert not any("leak" in user for _, _, user in fake.seen)


def test_bad_line_fails_before_any_call_without_echoing_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JEV_SPEND_STATE", str(tmp_path / "spend.json"))
    inp, mp = write_inputs(tmp_path)
    with open(inp, "a") as fh:
        fh.write('{"message": "Jordan Avery, (312) 555-0187" BROKEN\n')
    out = tmp_path / "results.jsonl"
    fake = RecordingClient()
    assert main(["--input", str(inp), "--out", str(out), "--redact-map", str(mp)],
                client=fake) == 2
    err = capsys.readouterr().err
    assert "line 4: not valid JSON" in err and "Jordan" not in err and "(312)" not in err
    assert fake.seen == [] and not out.exists()


def test_missing_input_fails_cleanly(tmp_path):
    mp = tmp_path / "map.json"
    mp.write_text("{}")
    out = tmp_path / "results.jsonl"
    env = {k: v for k, v in os.environ.items() if k != "META_MODEL_API_KEY"}
    r = subprocess.run([sys.executable, SCRIPT, "--input", str(tmp_path / "nope.jsonl"),
                        "--out", str(out), "--redact-map", str(mp)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "input file not found" in r.stderr and "Traceback" not in r.stderr
    assert not out.exists()


def test_refuses_paths_inside_repo(tmp_path):
    inp, mp = write_inputs(tmp_path)
    out = os.path.join(REPO, "eval", "runs", "shadow-should-not-exist.jsonl")
    fake = RecordingClient()
    assert main(["--input", str(inp), "--out", out, "--redact-map", str(mp)], client=fake) == 2
    assert fake.seen == [] and not os.path.exists(out)


def test_no_key_refuses_instead_of_failing_safe_silently(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    inp, mp = write_inputs(tmp_path)
    out = tmp_path / "results.jsonl"
    assert main(["--input", str(inp), "--out", str(out), "--redact-map", str(mp)]) == 2
    assert "META_MODEL_API_KEY" in capsys.readouterr().err and not out.exists()


class DeadClient:
    """A wrong key, blank base URL, or outage: every call comes back None."""
    def complete(self, model, system, user):
        return None


class LeakDeadClient(RecordingClient):
    """Fails only on the leak message: one fail-safe among real verdicts."""
    def complete(self, model, system, user):
        return None if "leak" in user else super().complete(model, system, user)


def test_all_failsafe_run_exits_nonzero_and_keeps_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JEV_SPEND_STATE", str(tmp_path / "spend.json"))
    inp, mp = write_inputs(tmp_path)
    out = tmp_path / "results.jsonl"
    argv = ["--input", str(inp), "--out", str(out), "--redact-map", str(mp)]
    assert main(argv, client=DeadClient()) == 3
    printed = capsys.readouterr()
    assert "(fail-safe 3)" in printed.out and "Wrote 3 rows" in printed.out
    assert "no real verdicts" in printed.err
    assert all(json.loads(l)["failsafe"] for l in out.read_text().splitlines())

    assert main(argv, client=LeakDeadClient()) == 0  # some real verdicts: still a run
    assert "(fail-safe 1)" in capsys.readouterr().out


def test_tool_imports_no_write_or_network_path():
    # The only outbound path is the model client via tiers.decide: no PMS,
    # messaging, HTTP, or subprocess module is importable from the tool.
    tree = ast.parse(open(SCRIPT).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert mods <= {"argparse", "json", "os", "re", "sys", "collections",
                    "jev.client", "jev.config", "jev.tiers", "jev.types"}, mods
