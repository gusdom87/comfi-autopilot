"""Shared text rules for the repo's answer engines (hub_answers, geo_answers).

Built Oct 31, 2026. Deterministic stdlib code, no model. These are the
repo's OWN standalone checks, so the engines are safe to run and test
without the private live actuator. In the actuator (staging copy), every
existing answer-path gate runs as well, unchanged: the safety-anywhere
scan with its emergency emoji, the request filter, whole-message
coverage, the access hold, the accepted-reservation rule. These checks
only ever add holds on top of those.

The core rule is whole-message coverage, the same shape as the actuator's:
every word of the guest's own words must be the matched question's own
vocabulary, a closed-class function word, or a greeting or thanks at the
start or end of a sentence; and every sentence with anything left must be
a question. Anything else ("the power went out", "my son has a fever",
an unlisted problem) is a leftover word, so no danger list is needed for
it to hold. On top of that: any request or issue wording, and any symbol
or emoji outside a short benign list, holds.
"""
import re
import unicodedata

try:  # the Phase 0 guard's reader: drops quoted host text from a reaction
    from .change_guard import _guest_words
except ImportError:  # pragma: no cover
    _guest_words = None


def fold(text):
    """Lowercase, accents dropped, curly quotes straightened, spaces collapsed."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t.lower()).strip()


def guest_words(raw):
    """The guest's own words: a reaction's quote of host text removed."""
    return _guest_words(raw or "") if _guest_words is not None else (raw or "")


_SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")


def sentences(text):
    return [s.strip() for s in _SENT_RE.findall(text or "") if s.strip(" .!?\t")]


_QSTART = re.compile(
    r"^\W*(?:(?:hi|hey|hello|quick\s+question|also|and|so|btw|ok(?:ay)?|thanks?)\b[\s,!.:-]*)*"
    r"(?:what|whats|what's|where|wheres|where's|when|how|hows|how's|is|are|was|do|does|did|any|which|who)\b")
_PLEASANTRY = re.compile(r"^\W*(?:(?:hi|hey|hello)\W*)*(?:how\s+are\s+you(?:\s+doing)?|how'?s\s+it\s+going"
                         r"|hope\s+(?:you|all)\b.*|(?:i\s+have\s+a\s+|(?:one\s+)?(?:more\s+|quick\s+)?)questions?"
                         r"(?:\s+for\s+you)?)\W*$")


def is_question(sentence):
    if _PLEASANTRY.match(sentence):
        return False
    return "?" in sentence or bool(_QSTART.match(sentence))


# Closed-class function words: never content on their own.
FRAME = frozenset("""
a an the this that these those it it's its there there's theres
i i'm im we we're us our you you're your u ur me my
is are am be do does have has
what what's whats when where where's wheres which who how how's hows
can could will would should
to of for at in on by with from about and or also so just please pls plz btw any some
""".split())
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)*")

_COURTESY = (
    r"good\s+(?:morning|afternoon|evening|day)|(?:hi|hey|hello)\s+there|hi+|hey+|hello+|hiya|howdy"
    r"|thanks?(?:\s+(?:you|u|ya))?(?:\s+(?:so|very)\s+much|\s+a\s+(?:lot|bunch|ton))?(?:\s+in\s+advance)?"
    r"|many\s+thanks|much\s+appreciated|(?:(?:i|we)\s+)?appreciate\s+(?:it|you|that)|thx|ty|tysm"
    r"|quick\s+question|(?:i\s+have\s+a\s+|one\s+more\s+|another\s+)?questions?(?:\s+for\s+you)?"
    r"|hope\s+(?:you'?re|you\s+are|all\s+is)\s+(?:well|good|great|doing\s+(?:well|good|great))"
    r"|have\s+a\s+(?:good|great|nice)\s+(?:day|night|evening|weekend)|cheers|best\s+regards|regards")
_ACK = r"ok(?:ay)?|great|perfect|awesome|cool|got\s+it|sounds\s+good|yes|yeah|yep"
_LEAD = re.compile(r"^\W*(?:" + _COURTESY + "|" + _ACK + r")\b")
_TAIL = re.compile(r"\b(?:" + _COURTESY + r")\W*$")


def strip_courtesy(sentence):
    s = sentence
    while True:
        m = _LEAD.match(s)
        if not m or m.end() == 0:
            break
        s = s[m.end():]
    while True:
        m = _TAIL.search(s)
        if not m or m.start() == len(s):
            break
        s = s[:m.start()]
    return s


def coverage_gaps(text, cover):
    """What in folded guest text a question vocabulary `cover` (a compiled
    regex of whole phrases) leaves unaccounted for: each leftover word, and
    each sentence that says something without asking. [] means the whole
    message is that one kind of question."""
    words, statements = [], []
    for s in sentences(text):
        rest = strip_courtesy(s)
        if not [w for w in WORD.findall(rest) if w not in FRAME]:
            continue
        if not is_question(s):
            statements.append(f"a statement: {s[:60]!r}")
        left = cover.sub(" ", rest)
        words += [w for w in WORD.findall(left) if w not in FRAME]
    return [repr(w) for w in dict.fromkeys(words)] + statements


def content_sentences(text):
    """Sentences with something besides greetings, thanks, and function words."""
    out = []
    for s in sentences(text):
        if [w for w in WORD.findall(strip_courtesy(s)) if w not in FRAME]:
            out.append(s)
    return out


# A modal is a request ("can we...", "could you...") unless it directly
# follows a where/when/how/what-time question word ("where can I find...").
_MODAL = re.compile(r"\b(?:can|could|may|might|would|will|shall)\b")
_MODAL_OK_BEFORE = re.compile(r"(?:\bwhere|\bwhen|\bhow|\bwhat\s+time|\bwhich)\s*$")
_ASK = re.compile(
    r"\b(?:mind\s+if|ok(?:ay)?\s+(?:if|to)|is\s+it\s+(?:ok(?:ay)?|possible|fine|alright)|possib\w*|any\s+(?:chance"
    r"|shot|way)|let\s+(?:us|me)|able\s+to|(?:we|i)'?d\s+(?:like|love|prefer)|(?:we|i)\s+would\s+(?:like|love)"
    r"|hop(?:e|ing)\s+to|prefer\w*|rather|bring|drop\s+off|send\s+(?:us|me)|more|another|refill|restock"
    r"|need\s+(?:some|more|a|an)|if)\b")
_ISSUE = re.compile(
    r"\b(?:not|no|never|nothing|nobody|doesn'?t|dont|don'?t|didn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|won'?t|wont"
    r"|can'?t|cant|cannot|couldn'?t|haven'?t|hasn'?t|wrong|broken?|stuck|leak\w*|problems?|issues?|trouble"
    r"|error\w*|fail\w*|still|again|anymore|help|fix\w*|ready|yet|someone|somebody|missing|lost|dirty|smell\w*"
    r"|urgent\w*|asap|emergenc\w*)\b")


def request_hits(folded):
    hits = [m.group(0) for rx in (_ASK, _ISSUE) for m in rx.finditer(folded or "")]
    hits += [m.group(0) for m in _MODAL.finditer(folded or "") if not _MODAL_OK_BEFORE.search(folded[:m.start()])]
    return list(dict.fromkeys(hits))


# Emoji and symbols: only these are read as friendly. Anything else (an
# emergency emoji, an arrow, a unknown pictograph) holds the message.
BENIGN_SYMBOLS = frozenset("🙏😊🙂😀😃😄😁☺👍❤♥💕🥰😍👋🎉✨⭐😅🤗👌")
_IGNORABLE = {"️", "‍"}


def risky_symbols(raw):
    out = []
    for ch in str(raw or ""):
        if ch in _IGNORABLE or ch in BENIGN_SYMBOLS:
            continue
        cat = unicodedata.category(ch)
        if 0x1F3FB <= ord(ch) <= 0x1F3FF:  # skin-tone modifiers ride on another emoji
            continue
        if cat in ("So", "Sk", "Cs", "Co", "Cn"):
            out.append(ch)
    return list(dict.fromkeys(out))


DASHES = ("—", "–")  # em and en dash: never in anything written as the operator
