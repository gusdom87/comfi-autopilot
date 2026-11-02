#!/usr/bin/env python3
"""Mutation pass for jev.comms: break one gate at a time in a scratch
copy and confirm the comms tests turn red for every one.

    python3 scripts/mutation_check_comms.py      (a minute or two; offline)

Each mutation is an exact text replacement that must match exactly once
(so a refactor that moves a gate fails loudly here instead of silently
testing nothing). The real files are never modified: src/ and tests/
are copied into a temp dir and the comms tests run there. The copy gets
an empty .git so the repo-path tests behave as they do in the worktree,
and the unmutated copy must pass before any mutant counts as killed.
Exit 0 only if the baseline is green and every mutant is killed.

The unmarked mutations port the Fable audit's independent pass (Oct 29,
2026). The F-tagged ones revert the audit fixes F1 to F6.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = "src/jev/comms/"
TESTS = ["tests/test_comms_digest.py", "tests/test_comms_crew_gate.py",
         "tests/test_comms_deny_list.py"]


def one(name, path, old, new):
    return (name, path, [(old, new)])


M = [
    # --- digest ---
    one("digest: a2p gate removed", C + "digest.py",
        "    if cfg.a2p_confirmed is not True:\n", "    if False:\n"),
    one("digest: a2p accepts truthy", C + "digest.py",
        'a2p_confirmed=d.get("a2p_confirmed") is True,', 'a2p_confirmed=bool(d.get("a2p_confirmed")),'),
    one("digest: op==from allowed", C + "digest.py",
        "    if op and frm and op == frm:\n", "    if False:\n"),
    one("digest: crew-number check removed", C + "digest.py",
        "            if op in crew:\n", "            if False:\n"),
    one("digest: sent-twice check removed", C + "digest.py",
        '    if digest_id in state.get("sent", {}):\n', "    if False:\n"),
    one("digest: transport binding check removed", C + "digest.py",
        "            transport.operator_number != cfg.operator_number:", "            False:"),
    one("digest: --send ignored", C + "digest.py",
        "    if not send:\n        if advance:", "    if False:\n        if advance:"),
    one("digest: client rows mixed into new", C + "digest.py",
        "new = sorted((i for i in windowed if i.property_id in own and i.triaged)",
        "new = sorted((i for i in windowed if i.triaged)"),
    one("digest: client waiting mixed in", C + "digest.py",
        '        if i.property_id in own:\n            if st == "waiting":',
        '        if True:\n            if st == "waiting":'),
    one("digest: waiting ignores host reply", C + "digest.py",
        '    if _answered(item):\n        return ""', '    if False:\n        return ""'),
    one("digest: intent written after send", C + "digest.py",
        '    state["sent"][did] = {"at": _iso(now), "outcome": "sending"}\n'
        "    save_state(cfg.out_dir, state)\n", ""),
    one("digest: watermark advances on unknown send", C + "digest.py",
        '    if outcome == "sent" and advance:', "    if advance:"),
    one("digest: sms cap removed", C + "digest.py",
        '    return sms_ascii("\\n".join(lines))[:cap]', '    return sms_ascii("\\n".join(lines))'),
    one("digest: out_dir private check removed", C + "digest.py",
        'out_dir=check_private_path(path("out_dir")),', 'out_dir=path("out_dir"),'),
    one("F1 digest: no_reply_24h reads as answered", C + "digest.py",
        'item.raw.get("host_outcome") == "host_replied"', 'item.raw.get("host_outcome")'),
    one("F2 digest: queue copy wins over an answered shadow row", C + "digest.py",
        "        if i.at > now or i.key in answered:\n", "        if i.at > now:\n"),
    one("F4 digest: missing crew directory is not a blocker", C + "digest.py",
        "    if not cfg.crew_directory:\n"
        '        out.append("No crew directory configured: the operator number can\'t be checked "\n'
        '                   "against worker numbers.")\n'
        "    elif op:\n",
        "    if op and cfg.crew_directory:\n"),
    one("F5 digest: non-string number or path accepted", C + "digest.py",
        "        if d.get(key) is not None and not isinstance(d[key], str):\n", "        if False:\n"),
    # --- private files ---
    one("files: repo-path check removed", C + "_files.py",
        "    if inside_git_tree(expanded):\n", "    if False:\n"),
    one("files: mode 644", C + "_files.py",
        "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n    try:\n        os.fchmod(fd, 0o600)",
        "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)\n    try:\n        os.fchmod(fd, 0o644)"),
    # --- crew gate ---
    one("crew: offline check removed", C + "crew_gate.py",
        '        if getattr(transport, "OFFLINE", False) is not True:\n', "        if False:\n"),
    one("crew: deny-list skipped", C + "crew_gate.py",
        "        if deny_list.is_denied(key):\n", "        if False:\n"),
    one("crew: allowlist skipped", C + "crew_gate.py",
        "        if req.property_id not in self._own:\n", "        if False:\n"),
    one("crew: unapproved template allowed", C + "crew_gate.py",
        "        if not t.approved:\n", "        if False:\n"),
    one("crew: hash check (approved) always true", C + "templates.py",
        "        return bool(self.approved_by) and self.approved_sha256 == self.sha256",
        "        return bool(self.approved_by)"),
    one("crew: ambiguous recipient picks first", C + "crew_gate.py",
        "        if len(people) > 1:\n", "        if False:\n"),
    one("crew: recipient_id mismatch ignored", C + "crew_gate.py",
        "        if req.recipient_id is not None and req.recipient_id != person.person_id:\n",
        "        if False:\n"),
    one("crew: language check skipped", C + "crew_gate.py",
        "        if t.language != person.language:\n", "        if False:\n"),
    one("crew: content lint skipped", C + "crew_gate.py",
        '        if hits:\n            return refuse("content"', '        if False:\n            return refuse("content"'),
    one("crew: opt-out ignored", C + "crew_gate.py",
        "        if person.opted_out:\n", "        if False:\n"),
    one("crew: missing opt-out flag reads opted in", C + "crew_gate.py",
        'opted_out=p.get("opted_out") is not False,', 'opted_out=p.get("opted_out") is True,'),
    one("crew: consent YES not required", C + "crew_gate.py",
        "            if not person.consent_yes_at or person.notice_sha256 != notice.sha256:\n",
        "            if False:\n"),
    one("F3 crew: consent only for job-kind templates", C + "crew_gate.py",
        '        if not (t.template_id == self._notice_id and t.kind == "notice"):\n',
        '        if t.kind == "job":\n'),
    one("F3 crew: notice ID skips consent whatever its kind", C + "crew_gate.py",
        't.template_id == self._notice_id and t.kind == "notice"', "t.template_id == self._notice_id"),
    one("crew: duplicate check removed", C + "crew_gate.py",
        "        if res.idempotency_key in history:\n", "        if False:\n"),
    one("crew: thread read failure ignored", C + "crew_gate.py",
        '        except Exception as e:\n            return refuse("thread"',
        '        except Exception as e:\n            thread = []\n        if False:\n            return refuse("thread"'),
    one("crew: cleaner hold removed", C + "crew_gate.py",
        '        if req.role == "cleaner" and not CLEANER_ASSIGNEE_READ:\n', "        if False:\n"),
    one("crew: yield window 0", C + "crew_gate.py",
        "    yield_window: timedelta = timedelta(hours=2)", "    yield_window: timedelta = timedelta(hours=0)"),
    one("crew: loop guard removed", C + "crew_gate.py",
        "        if len(last_two) == 2 and all(", "        if False and all("),
    one("crew: per-worker cap 99", C + "crew_gate.py",
        "    per_worker_day: int = 3", "    per_worker_day: int = 99"),
    one("crew: per-job cap 99", C + "crew_gate.py",
        "    per_job: int = 3", "    per_job: int = 99"),
    one("crew: global cap 99", C + "crew_gate.py",
        "    all_workers_day: int = 10", "    all_workers_day: int = 99"),
    one("crew: autonomy hold removed", C + "crew_gate.py",
        '        if not allowed:\n            holds.append(("autonomy"',
        '        if False:\n            holds.append(("autonomy"'),
    one("crew: quiet hours in UTC", C + "crew_gate.py",
        "        local_now = now.astimezone(tz)", "        local_now = now"),
    # --- templates ---
    one("templates: slot type check removed (free text)", C + "templates.py",
        '    raise ValueError(f"{slot}: not a slot the caller may fill")', "    return str(value)"),
    one("templates: extra slots allowed", C + "templates.py",
        "    if missing or extra:\n", "    if missing:\n"),
    one("templates: unknown slot accepted at load", C + "templates.py",
        "    bad = [s for s in t.slots if s not in SLOT_TYPES]\n", "    bad = []\n"),
    one("F3 templates: notice kind allowed on any key", C + "templates.py",
        '    if t.kind == "notice" and t.action_key != NOTICE_ACTION_KEY:\n', "    if False:\n"),
    one("F3 templates: job kind allowed on crew_notice", C + "templates.py",
        '    if t.kind == "job" and t.action_key == NOTICE_ACTION_KEY:\n', "    if False:\n"),
    # --- classes ---
    one("classes: C may run alone", C + "classes.py",
        "    if cls == C:\n        return False,", "    if cls == C:\n        return True,"),
    one("classes: D may run alone with ledger", C + "classes.py",
        "    if cls == D:\n        return False,", "    if cls == D:\n        return True,"),
    one("classes: unknown key is B", C + "classes.py",
        "    return ACTION_CLASSES.get(key, D)", "    return ACTION_CLASSES.get(key, B)"),
    one("classes: B borrows the Act record", C + "classes.py",
        "    right = right_for(key, ledger or {})", '    right = right_for("Act", ledger or {})'),
    # --- deny list ---
    one("deny: allowlist grows send-bulk", C + "deny_list.py",
        'ALLOWED_OPERATIONS = frozenset({"quo:send-message"})',
        'ALLOWED_OPERATIONS = frozenset({"quo:send-message", "quo:send-bulk-messages"})'),
    one("deny: unlock dropped from list", C + "deny_list.py",
        '    "hospitable:unlock-smartlock":\n'
        '        "access: unlock actions are share_access, Class D (Appendix B)",\n', ""),
    one("deny: check_operation deny-by-default removed", C + "deny_list.py",
        "    if name not in ALLOWED_OPERATIONS:\n        raise", "    if False:\n        raise"),
    # --- transports ---
    # The audit's "list recipient accepted" mutant (dropping the isinstance
    # check in single_recipient) is equivalent after F6: is_e164 refuses
    # non-strings too. Both checks go together here.
    ("transport: list recipient accepted", C + "transport.py", [
        ("    if not isinstance(to, str):\n        raise RecipientError",
         "    if False:\n        raise RecipientError"),
        ("    return isinstance(value, str) and E164_RE.fullmatch(value) is not None",
         "    return not isinstance(value, str) or E164_RE.fullmatch(value) is not None")]),
    one("transport: mock skips check_operation", C + "transport.py",
        '    def send_text(self, to: str, body: str) -> SendResult:\n'
        '        check_operation("quo", "send-message")\n        to = single_recipient(to)',
        '    def send_text(self, to: str, body: str) -> SendResult:\n        to = single_recipient(to)'),
    one("transport: argv targets from_number", C + "transport.py",
        '"--to", self.operator_number, "--content", content]', '"--to", self.from_number, "--content", content]'),
    one("F6 transport: \\d instead of [0-9]", C + "transport.py",
        r'E164_RE = re.compile(r"\A\+[1-9][0-9]{7,14}\Z")', r'E164_RE = re.compile(r"\A\+[1-9]\d{7,14}\Z")'),
    ("F6 transport: $ and match instead of \\Z and fullmatch", C + "transport.py", [
        (r'E164_RE = re.compile(r"\A\+[1-9][0-9]{7,14}\Z")', r'E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")'),
        ("E164_RE.fullmatch(value)", "E164_RE.match(value)")]),
]


def scratch_copy() -> str:
    d = tempfile.mkdtemp(prefix="qc-mut-")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for sub in ("src", "tests"):
        shutil.copytree(os.path.join(ROOT, sub), os.path.join(d, sub), ignore=ignore)
    os.mkdir(os.path.join(d, ".git"))       # the repo-path tests need a work tree
    return d


def run_tests(d: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                          cwd=d, capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def last_line(proc) -> str:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else proc.stderr.strip()[-200:]


def mutate(entry):
    name, rel, pairs = entry
    d = scratch_copy()
    try:
        path = os.path.join(d, rel)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for old, new in pairs:
            if src.count(old) != 1:
                return name, "SKIP", f"pattern found {src.count(old)} times in {rel}"
            src = src.replace(old, new)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = run_tests(d)
        return name, "KILLED" if proc.returncode else "SURVIVED", last_line(proc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    d = scratch_copy()
    try:
        base = run_tests(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"baseline (no mutation): {last_line(base)}")
    if base.returncode:
        print("The unmutated copy is red, so no mutant can be judged. Fix that first.")
        return 2
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        results = list(pool.map(mutate, M))
    for name, status, detail in results:
        print(f"{status:8}  {name}: {detail}")
    bad = [r for r in results if r[1] != "KILLED"]
    print(f"\n{len(results) - len(bad)} of {len(results)} mutants killed.")
    for name, status, _ in bad:
        print(f"  {status}: {name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
