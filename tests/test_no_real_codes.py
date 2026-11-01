"""Guard: no real access code from the live Knowledge Hub is committed.

The repo is public, so fixtures and tests use invented digits only. This
test reads a PRIVATE denylist of real codes (one per line, kept outside the
repo) from the path in JEV_CODE_DENYLIST and fails if any tracked file, or
any commit message reachable from HEAD, contains a listed string. Entries
made of digits also match with the other separators a code gets written
with ("1-2-3-4", "1 2 3 4", "12-34", "1234").

With the variable unset the guard skips: a clone without the private list
cannot vouch for itself, so it must not pass. Failure messages name the
file and the denylist line, never the string.
"""
import os, re, subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DENYLIST = os.environ.get("JEV_CODE_DENYLIST")


def load_denylist(path):
    entries = []
    with open(path, encoding="utf-8") as fh:
        for n, ln in enumerate(fh, 1):
            s = ln.strip()
            if s and not s.startswith("#"):
                entries.append((n, s))
    return entries


def patterns(entry):
    pats = [re.compile(re.escape(entry.encode()))]
    digits = re.sub(r"\D", "", entry)
    if len(digits) >= 4 and re.fullmatch(r"[\d .-]+", entry):
        body = rb"[-. ]?".join(d.encode() for d in digits)
        pats.append(re.compile(rb"(?<!\d)" + body + rb"(?!\d)"))
    return pats


def hits(blob, entries):
    return [n for n, e in entries if any(p.search(blob) for p in patterns(e))]


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, check=True).stdout


def test_matcher_catches_each_written_form():
    entries = [(7, "9692")]
    for text in (b"combination 9692.", b"dial 9-6-9-2", b"9 6 9 2", b"9.6.9.2", b"96-92"):
        assert hits(text, entries) == [7], text
    assert hits(b"combination 9-6-9-3, room 692", entries) == []
    assert hits(b"dial 9-6-9-2", [(3, "9-6-9-2")]) == [3]
    assert hits(b"code 9692", [(3, "9-6-9-2")]) == [3]


@pytest.mark.skipif(not DENYLIST, reason="JEV_CODE_DENYLIST not set; private denylist absent")
def test_no_denylisted_code_in_committed_text():
    assert os.path.isfile(DENYLIST), "JEV_CODE_DENYLIST points at a missing file"
    entries = load_denylist(DENYLIST)
    assert entries, "the private denylist has no entries"
    found = []
    for rel in _git("ls-files", "-z").decode("utf-8").split("\0"):
        path = os.path.join(ROOT, rel)
        if not rel or not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            blob = fh.read()
        found += [f"{rel} (denylist line {n})" for n in hits(blob, entries)]
    for rec in _git("log", "--format=%h%n%B%x1e", "HEAD").split(b"\x1e"):
        sha, _, msg = rec.strip().partition(b"\n")
        found += [f"commit {sha.decode()} message (denylist line {n})" for n in hits(msg, entries)]
    assert not found, "real code in committed text: " + "; ".join(found)
