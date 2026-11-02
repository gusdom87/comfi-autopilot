"""Transports: the only way anything in jev.comms can reach Quo.

Two interfaces, deliberately incompatible:

  CrewTransport.send_text(to, body)   one worker, one text
  OperatorTransport.send(content)     the operator's own phone, bound
                                      at construction; there is no
                                      recipient argument to change

Crew side: MockCrewTransport and nothing else. No live crew transport
exists in this codebase, and CrewSendGate refuses any transport that
does not declare OFFLINE = True. Wiring a real one is a code change.

Operator side: MockOperatorTransport, and QuoCliOperatorTransport, the
one real class. The digest builds it only after every send gate has
held (digest.send_blockers). The tests never construct it with a real
runner and never run it.

Each transport names its connector operation before building a call
(deny_list.check_operation) and accepts exactly one E.164 string as the
recipient. A list, a tuple, or a joined string is refused, so a group
or bulk text can't be expressed.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from .deny_list import check_operation

# The recipient gate. ASCII digits only ([0-9]; \d also matches other
# scripts' digits, like a fullwidth 9) and anchored at the absolute end
# (\Z; $ also matches before a trailing newline). Use is_e164.
E164_RE = re.compile(r"\A\+[1-9][0-9]{7,14}\Z")

SENT, FAILED, UNKNOWN = "sent", "failed", "unknown"


def is_e164(value) -> bool:
    """One valid E.164 number, the whole string, nothing else."""
    return isinstance(value, str) and E164_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class SendResult:
    status: str                  # sent | failed | unknown
    message_id: str | None = None
    detail: str = ""


class RecipientError(ValueError):
    """Not exactly one valid E.164 recipient."""


def single_recipient(to) -> str:
    if not isinstance(to, str):
        raise RecipientError(f"exactly one recipient string is required, got {type(to).__name__}")
    if not is_e164(to):
        raise RecipientError("recipient is not a single E.164 number")
    return to


class CrewTransport:
    """One text to one worker. Subclasses that reach a network set
    OFFLINE = False; CrewSendGate accepts only OFFLINE = True."""
    OFFLINE = False

    def send_text(self, to: str, body: str) -> SendResult:
        raise NotImplementedError


class MockCrewTransport(CrewTransport):
    """Records what would have been sent. Nothing leaves the process."""
    OFFLINE = True

    def __init__(self, results=None, raises: Exception | None = None):
        self.sent: list[tuple[str, str]] = []
        self._results = list(results or [])
        self._raises = raises

    def send_text(self, to: str, body: str) -> SendResult:
        check_operation("quo", "send-message")
        to = single_recipient(to)
        if self._raises is not None:
            raise self._raises
        self.sent.append((to, body))
        if self._results:
            return self._results.pop(0)
        return SendResult(SENT, f"mock-{len(self.sent)}", "mock transport: nothing left this process")


class OperatorTransport:
    """One text to the operator's own number, bound at construction."""
    OFFLINE = False
    operator_number = ""

    def send(self, content: str) -> SendResult:
        raise NotImplementedError


class MockOperatorTransport(OperatorTransport):
    OFFLINE = True

    def __init__(self, operator_number: str, results=None, raises: Exception | None = None):
        self.operator_number = single_recipient(operator_number)
        self.sent: list[str] = []
        self._results = list(results or [])
        self._raises = raises

    def send(self, content: str) -> SendResult:
        check_operation("quo", "send-message")
        if self._raises is not None:
            raise self._raises
        self.sent.append(content)
        if self._results:
            return self._results.pop(0)
        return SendResult(SENT, f"mock-op-{len(self.sent)}", "mock transport: nothing left this process")


class QuoCliOperatorTransport(OperatorTransport):
    """Quo SMS through the connector CLI (quo.py send). The CLI attaches
    the stored credential itself; no key passes through here. `runner`
    is subprocess.run unless a test injects a fake."""

    def __init__(self, cli_path: str, from_number: str, operator_number: str,
                 pythonpath: str | None = None, runner=None, timeout: float = 45):
        self.cli_path = os.path.expanduser(cli_path)
        self.from_number = single_recipient(from_number)
        self.operator_number = single_recipient(operator_number)
        if self.from_number == self.operator_number:
            raise RecipientError("from and operator numbers are the same")
        self.pythonpath = pythonpath
        self.timeout = timeout
        self._runner = runner or subprocess.run

    def argv(self, content: str) -> list[str]:
        check_operation("quo", "send-message")
        return [sys.executable, self.cli_path, "send", "--from", self.from_number,
                "--to", self.operator_number, "--content", content]

    def send(self, content: str) -> SendResult:
        argv = self.argv(content)
        # Inherit the environment (the VM's proxy settings live there);
        # only PYTHONPATH is added, for the connector's credential helper.
        env = dict(os.environ)
        if self.pythonpath:
            env["PYTHONPATH"] = os.pathsep.join(p for p in (self.pythonpath, env.get("PYTHONPATH")) if p)
        try:
            proc = self._runner(argv, capture_output=True, text=True, timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            return SendResult(UNKNOWN, None, "timed out; status unknown, never retried")
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + (proc.stdout or ""))[-300:]
            # quo.py exits with "HTTP <code> ..." when the API answered
            # with an error. A 4xx was refused; anything else is unclear.
            status = FAILED if re.search(r"\bHTTP 4\d\d\b", detail) else UNKNOWN
            return SendResult(status, None, detail)
        try:
            out = json.loads(proc.stdout or "")
        except ValueError:
            return SendResult(UNKNOWN, None, "unparsable CLI output; status unknown")
        data = out.get("data") if isinstance(out, dict) else None
        mid = (data.get("id") if isinstance(data, dict) else None) or \
            (out.get("id") if isinstance(out, dict) else None)
        if not mid:
            return SendResult(UNKNOWN, None, "no message ID in the response; status unknown")
        return SendResult(SENT, str(mid), "")
