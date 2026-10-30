"""Connector deny-list for the comms layer (design package 5.2, L7).

"Never automatic" has to mean "not callable". The Hospitable and Quo
connectors expose operations that are Class D by nature: lock or
unlock a door, cancel a reservation, create a quote, rewrite a
reservation or the calendar, message a guest outside the gated guest
lane, start a Quo AI agent call or conversation, ask the knowledge
agent (both non-Meta models, audited spec 2.0 rule 4), text a group or
a bulk list, pull call transcripts (Illinois all-party consent, L8).
None of them exists anywhere in jev.comms. This module is the written
list, enforced three ways:

1. check_operation(connector, operation): every call a comms transport
   makes names its connector operation first. Only ALLOWED_OPERATIONS
   pass: one Quo text to one number. A denied operation raises
   DeniedOperation with its reason; an operation on neither list
   raises too (deny by default). The comms layer is allowed no
   Hospitable operation at all.
2. surface_violations(obj): scans the public names of a module or a
   class for the words of a denied operation. The tests run it over
   every comms module and class, so adding something like
   `unlock_door` or `send_bulk` to the API surface fails the suite.
3. Transports take exactly one recipient string (transport.py); a
   list, a tuple, or a joined string is refused before anything is
   built, so a group or bulk text can't be expressed.
"""
import re
from types import ModuleType

# The twelve operations package L7 names, as the connectors name them.
DENIED_OPERATIONS = {
    "hospitable:lock-smartlock":
        "access: lock actions are share_access, Class D (Appendix B)",
    "hospitable:unlock-smartlock":
        "access: unlock actions are share_access, Class D (Appendix B)",
    "hospitable:cancel-reservation":
        "stay change and money: a cancellation is never automatic",
    "hospitable:create-quote":
        "money: a quote is approve_spend or price_change, Class D",
    "hospitable:update-reservation":
        "stay change: dates and times change only by the operator",
    "hospitable:update-property-calendar":
        "availability and price: calendar writes follow the recovery "
        "protocol by hand, never from comms",
    "hospitable:send-reservation-message":
        "guest message: guests are reached only through the gated guest "
        "lane (message_guest), never through crew or operator comms",
    "quo:start-agent-call":
        "Quo AI agent: a non-Meta model at runtime, and a call is not a "
        "fixed template",
    "quo:start-agent-conversation":
        "Quo AI agent: a non-Meta model at runtime",
    "quo:ask-knowledge-agent":
        "Quo knowledge agent: a non-Meta model at runtime",
    "quo:send-bulk-messages":
        "bulk texting: one text to one verified recipient only (package "
        "cut list: group or bulk crew texts)",
    "quo:send-group-message":
        "group texting: one text to one verified recipient only",
    "quo:fetch-call-transcripts":
        "call transcripts: recording or transcribing calls is cut "
        "(Illinois all-party consent, L8)",
}

# Everything comms may call. One Quo text to one number; nothing else.
ALLOWED_OPERATIONS = frozenset({"quo:send-message"})

CONNECTORS = ("hospitable", "quo")

# Words that mark a denied operation in a public name. Single words are
# enough on their own; the pairs catch the reservation writes.
SURFACE_WORDS = frozenset({
    "lock", "locks", "unlock", "unlocks", "smartlock",
    "cancel", "cancels", "cancellation",
    "quote", "quotes",
    "calendar",
    "agent", "agents", "knowledge",
    "bulk", "group", "groups", "broadcast", "blast",
    "transcript", "transcripts",
    "call", "calls", "dial",
})
SURFACE_PAIRS = (
    frozenset({"update", "reservation"}),
    frozenset({"reservation", "message"}),
    frozenset({"inquiry", "message"}),
)


class DeniedOperation(PermissionError):
    """A comms call named an operation that is not callable from here."""


def normalize(operation: str) -> str:
    """'Unlock_Smartlock', 'unlockSmartlock', ' unlock-smartlock ' ->
    'unlock-smartlock'."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", str(operation).strip())
    return re.sub(r"[\s_]+", "-", text).lower()


def qualified(connector: str, operation: str) -> str:
    return f"{normalize(connector)}:{normalize(operation)}"


def is_denied(operation: str) -> bool:
    """True for a denied operation, named with or without its connector
    ('unlock-smartlock', 'hospitable:unlock_smartlock')."""
    op = str(operation)
    if ":" in op:
        connector, _, name = op.partition(":")
        return qualified(connector, name) in DENIED_OPERATIONS
    name = normalize(op)
    return any(qualified(c, name) in DENIED_OPERATIONS for c in CONNECTORS)


def check_operation(connector: str, operation: str) -> str:
    """The qualified name of an allowed operation; raises
    DeniedOperation for anything else."""
    name = qualified(connector, operation)
    if name in DENIED_OPERATIONS:
        raise DeniedOperation(f"{name} is not callable from comms: "
                              f"{DENIED_OPERATIONS[name]}")
    if name not in ALLOWED_OPERATIONS:
        raise DeniedOperation(f"{name} is not on the comms allowlist "
                              f"(deny by default; allowed: "
                              f"{', '.join(sorted(ALLOWED_OPERATIONS))})")
    return name


def _words(name: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w}


def name_violation(name: str) -> str | None:
    """The denied word (or pair) a public name carries, if any."""
    words = _words(name)
    hit = sorted(words & SURFACE_WORDS)
    if hit:
        return hit[0]
    for pair in SURFACE_PAIRS:
        if pair <= words:
            return "+".join(sorted(pair))
    return None


def _public_names(obj) -> set[str]:
    names = {n for n in dir(obj) if not n.startswith("_")}
    fields = getattr(obj, "__dataclass_fields__", None)
    if fields:
        names |= {n for n in fields if not n.startswith("_")}
    return names


def surface_violations(obj) -> list[tuple[str, str]]:
    """(name, denied word) for every public name on a module or class
    that carries a denied operation's words. On a module, the classes
    it defines are scanned too; imported modules are skipped."""
    found = []
    for name in sorted(_public_names(obj)):
        value = getattr(obj, name, None)
        if isinstance(obj, ModuleType) and isinstance(value, ModuleType):
            continue
        word = name_violation(name)
        if word:
            found.append((name, word))
        if (isinstance(obj, ModuleType) and isinstance(value, type)
                and value.__module__ == obj.__name__):
            found += [(f"{name}.{n}", w) for n, w in surface_violations(value)]
    return found
