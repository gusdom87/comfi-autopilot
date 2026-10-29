"""Stay-change and money guard on the triage decision (design package
Phase 0, item 0a). Deterministic code on the model's output, never a
prompt instruction: the model does not see it and cannot argue past it.

A guest message that asks to change a stay (a later checkout, an
earlier check-in, an extra or dropped night, new dates, a cancellation)
or that puts money in play (a refund, a discount or price, a fee or a
charge, a deposit or claim, a dollar amount) is held for the operator:
an Act or Ignore verdict on it comes back as Confirm, whatever the
model said and however confident it was. The model's confidence is
kept as it was; the guard does not invent a number. A Confirm verdict,
the fail-safe included, comes back as the same object, and a message
that matches nothing is returned untouched.

Grant lint: when a held decision carries a draft that says yes ("sure",
"no problem", "that works", "claro", "está bien"), the draft is
replaced with HOLDING_REPLY. A yes to a stay change needs fresh
turnover context (this stay, the next arrival, the turnover's status);
the triage path carries only the property name, so here that context
is always absent.

The guard reads the guest's words only: the quoted host text in a
reaction ('Reacted 👍 to "..."') and host or co-host turns in a pasted
thread are dropped before matching.

Vocabulary is reused where it exists: the later-checkout cue, the
checkout-time reader, and the booking-extension pattern from
plays/late_checkout.py; the arrival cue and arrival-time reader from
plays/early_checkin.py; and the money and stay-change words in the
triage prompt's hard rules (a stay moved, shortened, extended, or
canceled; a disputed charge; a fee; a refund; a price adjustment; a
discount). Spanish is matched with accents folded.
"""
import re
import unicodedata
from dataclasses import replace

from .plays.context import to_minutes
from .plays.early_checkin import _CUE_RE as _ARRIVAL_CUE_RE
from .plays.early_checkin import extract_requested_minutes
from .plays.late_checkout import (_EXTENSION_RE, _LATE_CUE_RE,
                                  extract_requested_checkout)
from .types import Category, Decision

# Fixed text from the design package (A 2.4c). The exact words still
# wait on the operator's sign-off (package question Q7). It is only
# ever a draft on a held decision; nothing here sends it.
HOLDING_REPLY = "Let me check on that and get right back to you."

STANDARD_CHECKIN = "16:00"    # the early check-in play's default
STANDARD_CHECKOUT = "11:00"   # the late checkout play's default

# Not the guest's words: the host message a reaction quotes, and a
# host or co-host turn in a pasted thread ("## Name\nHost\n...").
_REACTION_RE = re.compile(
    r'\bReacted\b[^"“\n]{0,24}\bto\s+["“].*?'
    r'(?:["”][ \t]*(?=\n|\Z)|\Z)', re.S)
_HOST_TURN_RE = re.compile(
    r"^##[^\n]*\n[ \t]*(?:Host|Co-host)[ \t]*\n.*?(?=^##|\Z)",
    re.M | re.S)

# "early" is not a substring of "earlier", so the word is matched whole.
_EARLY_RE = re.compile(r"\bearl(?:y|ier|iest)\b", re.I)
_STAY_NOUN = (r"(?:stay|booking|reservation|trip|dates?|nights?"
              r"|check[\s-]?(?:in|out))")

_STAY_CHANGE = [
    ("cancellation", re.compile(r"\bcancel\w*", re.I)),
    ("dates or length", re.compile(
        r"\b(?:shorten|extend|change|move|modify|reschedul|push|cut)\w*\s+"
        r"(?:(?:our|my|the|this|that|your)\s+)?(?:\w+\s+)?" + _STAY_NOUN +
        r"\b"
        r"|\b(?:different|new|other)\s+dates\b"
        r"|\b(?:drop|dropped|dropping|remove|fewer|less)\s+(?:\w+\s+){0,2}"
        r"nights?\b"
        r"|\b(?:leav(?:e|ing)|check(?:ing)?[\s-]?out|depart\w*"
        r"|head(?:ing)?\s+out|go(?:ing)?\s+home)\s+"
        r"(?:(?:a|one|two|\d)\s+(?:day|night)s?\s+|a\s+(?:bit|little)\s+)?"
        r"earl(?:y|ier)\b"
        r"|\bcut\s+(?:our|my|the)\s+(?:stay|trip)\s+short\b", re.I)),
    ("extension", _EXTENSION_RE),
    ("stay change, Spanish", re.compile(
        r"\b(?:salida|salir|check[\s-]?out)\s+(?:un\s+poco\s+)?(?:mas\s+)?"
        r"tarde\b"
        r"|\b(?:entrada|llegada|llegar|entrar|check[\s-]?in)\s+"
        r"(?:un\s+poco\s+)?(?:mas\s+)?temprano\b"
        r"|\b(?:una|otra|dos|tres|\d)\s+noches?\s+mas\b|\botra\s+noche\b"
        r"|\b(?:extender|alargar|ampliar|acortar|cambiar|mover)\s+"
        r"(?:(?:la|las|mi|mis|nuestra|nuestras)\s+)?"
        r"(?:fechas?|reserva\w*|estancia|estadia)\b", re.I)),
]

_MONEY = [
    ("refund", re.compile(
        r"\brefund\w*|\bmoney\s+back\b|\breimburs\w*|\bcompensat\w*"
        r"|\breembols\w*|\bdevolucion\b"
        r"|\b(?:devolver|devuelvan|regresar)\w*\s+"
        r"(?:(?:el|mi|nuestro)\s+)?dinero\b", re.I)),
    ("discount or price", re.compile(
        r"\bdiscount\w*|\bpric(?:e|es|ed|ing)\b|\bcost(?:s|ing)?\b"
        r"|\b(?:nightly|weekly|monthly|lower|better|special|discounted)\s+"
        r"rates?\b"
        r"|\bdescuento\w*|\bprecio\w*|\btarifa\w*", re.I)),
    ("fee or charge", re.compile(
        r"\bfees?\b|\bovercharg\w*|\bdisput\w*"
        r"|\bcharg(?:e|es|ed|ing)\s+(?:me|us|you|extra|again|twice|for)\b"
        r"|\bcharg(?:e|es|ed|ing)\s+(?:my|our|the|your)\s+"
        r"(?:card|credit|account)\b"
        r"|\b(?:extra|additional|late|cleaning|pet|cancell?ation|hidden"
        r"|double|duplicate|unexpected)\s+charges?\b"
        r"|\bcobr(?:ar|aron|an|en|o|ado|aran)\b"
        r"|\bcargos?\s+(?:extra|adicional\w*)\b", re.I)),
    ("amount", re.compile(r"\$\s?\d|\b\d+\s*(?:dollars|bucks|usd)\b", re.I)),
    ("deposit or claim", re.compile(
        r"\bdeposit\w*|\bdeposito\b"
        r"|\b(?:damage|insurance|aircover)\s+claims?\b"
        r"|\b(?:file|filed|filing|open|opened|submit\w*)\s+(?:a\s+)?"
        r"(?:claim|case|dispute)\b"
        r"|\bresolution\s+(?:center|centre|request)\b", re.I)),
]

# A 2.4c's grant words. On a money or stay-change draft a bare yes is
# already a grant, so no time is required alongside it. "sí" keeps its
# accent: without one it is "if".
_GRANT_RE = re.compile(
    r"\b(?:yes|sure|of\s+course|no\s+problem|that(?:'?s|\s+is)\s+fine"
    r"|works)\b"
    r"|\b(?:claro|esta\s+bien|no\s+hay\s+problema)\b", re.I)
_SI_RE = re.compile(r"(?<!\w)sí(?!\w)", re.I)


def _fold(text: str) -> str:
    """Curly apostrophes straightened, accents dropped ("más" -> "mas")."""
    text = text.replace("’", "'").replace("‘", "'")
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _guest_words(message: str) -> str:
    text = _REACTION_RE.sub(" ", message or "")
    return _fold(_HOST_TURN_RE.sub(" ", text))


def _late_checkout(text: str) -> bool:
    return bool(_LATE_CUE_RE.search(text)) or \
        extract_requested_checkout(text, STANDARD_CHECKOUT) is not None


def _early_checkin(text: str) -> bool:
    """An arrival cue with "early" or a time before standard check-in.
    A later arrival time ("we'll get there around 6pm") changes no
    turnover, so it is not held."""
    if not _ARRIVAL_CUE_RE.search(text):
        return False
    if _EARLY_RE.search(text):
        return True
    asked = extract_requested_minutes(text, STANDARD_CHECKIN)
    return asked is not None and asked < to_minutes(STANDARD_CHECKIN)


def guarded_class(message: str) -> str | None:
    """Why this message is held ("stay change: late checkout",
    "money: refund", ...), or None when it is neither."""
    text = _guest_words(message)
    if _late_checkout(text):
        return "stay change: late checkout"
    if _early_checkin(text):
        return "stay change: early check-in"
    for label, pattern in _STAY_CHANGE:
        if pattern.search(text):
            return f"stay change: {label}"
    for label, pattern in _MONEY:
        if pattern.search(text):
            return f"money: {label}"
    return None


def has_grant_wording(draft: str) -> bool:
    return bool(_GRANT_RE.search(_fold(draft or ""))
                or _SI_RE.search(draft or ""))


def apply_guard(decision: Decision, message: str) -> Decision:
    """Cap a money or stay-change decision at Confirm, then lint its
    draft. Anything else comes back as the same object."""
    if decision.category is Category.CONFIRM:
        return decision
    held = guarded_class(message)
    if held is None:
        return decision
    draft = decision.draft
    reason = (f"Guard ({held}): held for the operator; the model said "
              f"{decision.category.value}")
    if draft and has_grant_wording(draft):
        draft = HOLDING_REPLY
        reason += "; its yes draft was replaced with the holding reply"
    if decision.reason:
        reason += f". Model's reason: {decision.reason}"
    return replace(decision, category=Category.CONFIRM, route="host",
                   draft=draft, reason=reason)
