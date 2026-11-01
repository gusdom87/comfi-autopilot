"""Hub answers: category hub_qa (built Oct 31, 2026).

Answers a guest's house question ("Where does the trash go?", "Is there
a coffee maker?") from the property's own Knowledge Hub, word for word.

  sources    ONLY (a) the property's human-edited Knowledge Hub items
             ("[edited]" in the private export; "[auto]" items, which
             Hospitable's AI detected from message history, are never a
             source) and (b) operator-confirmed fact-sheet facts: a value
             whose sha256 the operator confirmed within the last 7 days.
  scrub      every source is cut into statements. Barred, whatever the
             source says: entry, door, laundry and lockbox codes and
             anything code-shaped (3+ digit runs, dial digits like 9-6-9-2),
             keys and locks, the street address, pets, occupancy, parking,
             money, safety, stay changes (early, late, extend, cancel,
             check-in and checkout), contact details, links, and dashes.
             A barred statement is never rendered; a whole Hub topic about
             keys, codes, access, parking, pets, money, and the like is
             skipped. A statement opening with "It"/"They" rides with the
             sentence before it, and is dropped when that one was.
             Barred statements still count as EVIDENCE when two sources
             are compared (below), stored as words only (digits removed),
             so a hidden contradiction still holds the answer.
  question   one question sentence, one intent from a closed list (trash
             location, laundry location and supplies, coffee maker and
             supplies, kitchen appliances, towels, extra bedding, smoking
             policy, quiet hours, heating, TV), whole-message coverage by
             that intent's own vocabulary, no request or issue wording, no
             risky emoji, nothing barred, the Phase 0 guard silent. Check-in
             and checkout times, checkout steps, and wifi are NOT hub
             topics: the pinned answer library owns them.
  answer     "Hi! " + the source statement(s), verbatim. The statement must
             satisfy the intent's own requirement (a trash statement names
             a place; a coffee maker statement says there is one) and name
             every specific item the guest asked about. Absent: HOLD. Two
             sources disagree: HOLD. Agree: the statement whose every claim
             the other source also makes is the one sent.

Every no is a HOLD: no hub answer goes, and the message follows the
actuator's existing path (the acknowledgment, or a hold for a person).
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import answer_text as T

try:
    from .change_guard import guarded_class
except ImportError:  # pragma: no cover
    guarded_class = None

CATEGORY = "hub_qa"
SNAPSHOT_SCHEMA = "comfi-autopilot/hub-sources/v1"
FACT_TTL = timedelta(days=7)
MAX_MESSAGE_CHARS = 500
MAX_ANSWER_CHARS = 400
ANSWER_STATUSES = ("accepted", "confirmed")


def sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def reservation_problem(res):
    """None when every status the reservation carries is accepted or
    confirmed (the actuator's rule); otherwise why not."""
    if not isinstance(res, dict):
        return "no reservation record"
    seen = []
    if res.get("status") is not None:
        seen.append(("status", res.get("status")))
    rs = res.get("reservation_status")
    cur = rs.get("current") if isinstance(rs, dict) else None
    if isinstance(cur, dict) and cur.get("category") is not None:
        seen.append(("reservation_status.current.category", cur.get("category")))
    if not seen:
        return "reservation status unknown"
    bad = [f"{k}={v!r}" for k, v in seen if str(v).strip().lower() not in ANSWER_STATUSES]
    return f"reservation is not accepted ({', '.join(bad)})" if bad else None


# ---------- the Knowledge Hub export ----------

@dataclass(frozen=True)
class HubItem:
    heading: str
    tag: str          # "edited" | "auto" | other
    text: str         # the item's full text, multi-line
    line: int


_ITEM_RE = re.compile(r"^- \[([a-z]+)\]\s?(.*)$")


def parse_hub_markdown(md):
    """Items in the private Knowledge Hub export layout: "## Topic"
    headings, items opening "- [edited]" or "- [auto]", an item running
    on (blank lines included) until the next item or heading."""
    items, heading, cur = [], "", None

    def close():
        if cur is not None:
            items.append(HubItem(cur[0], cur[1], "\n".join(cur[2]).strip(), cur[3]))

    for n, line in enumerate((md or "").splitlines(), 1):
        if line.startswith("## "):
            close()
            cur, heading = None, line[3:].strip()
            continue
        if line.startswith("# "):
            close()
            cur = None
            continue
        m = _ITEM_RE.match(line)
        if m:
            close()
            cur = [heading, m.group(1), [m.group(2)], n]
            continue
        if cur is not None:
            cur[2].append(line)
    close()
    return items


# ---------- the scrub ----------

# A Hub topic about any of these is skipped whole (its statements are
# evidence only, never rendered).
BARRED_HEADING = re.compile(
    r"\b(?:keys?|codes?|access|entry|lock\w*|doors?|parking|pets?|animals?|occupancy|capacity|address|location"
    r"|directions|payments?|fees?|pric\w*|costs?|money|refunds?|compensation|cancel\w*|booking|bookings|reservations?"
    r"|extensions?|changes?|safety|emergenc\w*|security|cameras?|contact\w*|host\s+entry|lost|wi-?fi|internet"
    r"|arrival|check-?in|requirements|verification|guests?)\b")
# The lines inside an item that name a sub-section ("Smart Lock Access:").
_SUBHEAD_RE = re.compile(r"^[^.!?]{1,48}:\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[•●▪*\-]|\d+[.)])\s+")
_PRONOUN_START = re.compile(r"^(?:it|its|it's|they|they're|these|those|this|them)\b", re.I)

BARRED_RULES = [
    ("code", re.compile(r"\b(?:codes?|keypads?|lock\s?box(?:es)?|deadbolts?|smart\s?locks?|combination|combo"
                        r"|pass\s?codes?|pins?|keys?|lock|locks|locked|locking|unlock\w*)\b")),
    ("code_shaped", re.compile(r"\d{3,}|\d(?:\s*[-\s.]\s*\d){2,}|#\s*\d")),
    ("access", re.compile(r"\b(?:entry|enter|access|get\s+in|let\s+yourself\s+in|self[\s-]?check[\s-]?in)\b")),
    ("address", re.compile(r"\b\d+\s+(?:[a-z]+\s+){1,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive"
                           r"|dr|court|ct|way)\b|\baddress\b|\bzip\b|\b(?:unit|apartment|apt)\s+#?\d")),
    ("pets", re.compile(r"\b(?:pets?|dogs?|cats?|animals?|pupp(?:y|ies))\b")),
    ("occupancy", re.compile(r"\b(?:occupancy|accommodat\w*|sleeps|capacity|adults?|children|kids|infants?"
                             r"|up\s+to\s+\w+\s+(?:guests|people|persons)|max(?:imum)?\s+(?:of\s+)?\w*\s*guests)\b")),
    ("parking", re.compile(r"\b(?:park|parking|parked|vehicles?|tow|towed|towing|garage\s+door)\b|(?<![-\w])cars?\b")),
    ("money", re.compile(r"\$|\b(?:fees?|charges?|charged|costs?|pric\w*|pay|paid|payment\w*|refund\w*|deposit\w*"
                         r"|discount\w*|free|complimentary|rates?)\b")),
    ("safety", re.compile(r"\b(?:fire|extinguishers?|smoke\s+(?:detectors?|alarms?)|alarms?|carbon\s+monoxide"
                          r"|emergenc\w*|first[\s-]aid|911|police|gas|leak\w*|flood\w*|danger\w*|unsafe|safety"
                          r"|security|cameras?)\b")),
    ("stay_change", re.compile(r"\b(?:early|earlier|late|later|extend\w*|extension|cancel\w*|modify|request\w*"
                               r"|approv\w*|subject\s+to|turnover|same-day|check[\s-]?in|check[\s-]?out|checkout"
                               r"|checkin|booking|reservation)\b")),
    ("contact", re.compile(r"@|https?://|www\.|\b(?:call|text|phone|email|whatsapp)\b")),
    ("dash", re.compile("[—–]")),
]


def barred_reasons(sentence):
    f = T.fold(sentence)
    return [name for name, rx in BARRED_RULES if rx.search(f if name != "dash" else sentence)]


# ---------- intents (the closed question list) ----------

_LOC_NOUN = (r"(?:bins?|cans?|carts?|containers?|dumpsters?|garage|alley|curb|driveway|street|yard|side|back|front"
             r"|corner|end|basement|closets?|hallway|hall|kitchen|bathroom|bedroom|laundry\s+room|counter|drawers?"
             r"|cabinets?|shelf|shelves|pantry|porch|patio|deck|stairs|steps|door|entrance|building|house|unit"
             r"|studio|apartment|room|left|right|upstairs|downstairs|sink|tub|bathtub|washer|fridge)")
LOCATION_RE = re.compile(
    r"\b(?:in|into|inside|to|by|behind|beside|next\s+to|near|on|at|under|underneath|above|below|beneath|outside"
    r"|off|down|up)\s+(?:the\s+|a\s+|an\s+|its\s+|our\s+|your\s+)?(?:[a-z'-]+\s+){0,3}?" + _LOC_NOUN + r"\b"
    r"|\b(?:located|upstairs|downstairs|on\s+site|on-site|in\s+the\s+unit|inside\s+the\s+unit)\b")
_PRESENCE = r"\b(?:has|have|there\s+is|there's|comes\s+with|includes?|provides?|equipped\s+with)\b"


def _phrases(*words):
    # (?!') : a phrase never matches the front of a contraction ("where" in
    # "where's"), which would leave a stray "s" behind as content
    return re.compile(r"\b(?:" + "|".join(sorted(words, key=len, reverse=True)) + r")\b(?!')")


_WHERE_WORDS = ("where", "kept", "keep", "find", "located", "stored", "there", "here", "the unit", "the studio",
                "the apartment", "the house", "the place", "in the unit")


@dataclass(frozen=True)
class Intent:
    id: str
    topic: str              # the answer library's catalog category it belongs to
    question: tuple         # regexes on the folded question
    require: tuple          # every one must match a statement for it to answer
    cover: object           # compiled vocabulary for whole-message coverage
    items: tuple = ()       # (name, regex): named in the question -> must be in the answer


INTENTS = [
    Intent("trash.where", "trash",
           (re.compile(r"\bwhere\b.{0,40}\b(?:trash|garbage|rubbish|recycl\w*|bins?|dumpsters?)\b"),),
           (re.compile(r"\b(?:trash|garbage|recycl\w*|rubbish)\b"), LOCATION_RE),
           _phrases("trash", "garbage", "rubbish", "recycling", "recyclables", "bins", "bin", "trash cans", "trash can",
                    "garbage cans", "garbage can", "dumpster", "put", "throw", "throw away", "throw out", "take",
                    "take out", "out", "goes", "go", "leave", "outside", *_WHERE_WORDS)),
    Intent("laundry.where", "laundry",
           (re.compile(r"\bwhere\b.{0,30}\b(?:laundry|washer|(?<!hair )dryer|washing\s+machines?)\b(?!\s+sheets)"),
            re.compile(r"\b(?:is\s+there|are\s+there|do\s+you\s+have|does\s+(?:it|the\s+(?:unit|studio|place|apartment"
                       r"|house))\s+have|any)\s+(?:a\s+|an\s+)?(?:laundry|washer|(?<!hair )dryer|washing\s+machines?)"
                       r"\b(?!\s+sheets)")),
           (re.compile(r"^(?:the|a|an|our|your)?\s*(?:[a-z'-]+\s+){0,2}?(?:laundry\s+room|laundry|washer\s+and\s+dryer"
                       r"|washer|dryer|washing\s+machine)s?\s+(?:is|are)\b"),
            re.compile(LOCATION_RE.pattern + r"|\b(?:shared|on-site|on\s+site|in-unit|in\s+unit)\b")),
           _phrases("laundry", "laundry room", "room", "washer", "dryer", "washer and dryer", "washing machine",
                    "washing machines", "machines", "on site", "on-site", "in the unit", "unit", *_WHERE_WORDS),
           (("washer", re.compile(r"\bwasher\b|\bwashing\s+machine")), ("dryer", re.compile(r"\bdryer\b")))),
    Intent("laundry.supplies", "laundry",
           (re.compile(r"\b(?:detergent|laundry\s+soap|laundry\s+pods|detergent\s+pods|dryer\s+sheets)\b"),),
           (re.compile(r"\b(?:detergent|laundry\s+soap|dryer\s+sheets|laundry\s+pods)\b"),),
           _phrases("detergent", "laundry detergent", "laundry soap", "laundry pods", "detergent pods", "pods",
                    "dryer sheets", "washer", "laundry", "provide", "provided", "supplied", "have", "any",
                    "for the washer", *_WHERE_WORDS),
           (("detergent", re.compile(r"\bdetergent|\blaundry\s+(?:soap|pods)")),
            ("dryer sheets", re.compile(r"\bdryer\s+sheets?\b")))),
    Intent("coffee.maker", "coffee",
           (re.compile(r"\b(?:coffee\s+(?:maker|machine|pot)|keurig|kettle|espresso\s+machine|french\s+press)\b"),),
           (re.compile(r"\b(?:coffee\s+(?:maker|machine|setup|pot)|keurig|kettle|espresso\s+machine|french\s+press"
                       r"|pour[\s-]over)\b"), re.compile(_PRESENCE)),
           _phrases("coffee maker", "coffee machine", "coffee pot", "keurig", "kettle", "electric kettle", "kind",
                    "kind of", "type", "type of", "what kind", "kitchen", "have", "has", "any", "the unit", "the studio",
                    "the apartment", "the house", "the place", "there"),
           (("keurig", re.compile(r"\bkeurig\b")), ("kettle", re.compile(r"\bkettle\b")),
            ("espresso", re.compile(r"\bespresso\b")), ("french press", re.compile(r"\bfrench\s+press\b")))),
    Intent("coffee.supplies", "coffee",
           (re.compile(r"\b(?:ground\s+coffee|coffee\s+(?:grounds|pods|beans|filters)|k-?cups|filters)\b"),
            re.compile(r"\b(?:is\s+there|any|do\s+you\s+(?:have|provide))\s+coffee\b"),
            re.compile(r"\bwhere\b.{0,25}\bcoffee\b(?!\s+(?:maker|machine|shop|place|pot))")),
           (re.compile(r"\b(?:ground\s+coffee|coffee\s+(?:pods|grounds|beans|filters)|k-?cups)\b"
                       r"|\bcoffee\b.{0,40}\b(?:pods|filters?|grounds|beans)\b|\b(?:pods|filters?|grounds|beans)\b.{0,40}"
                       r"\bcoffee\b"),),
           _phrases("coffee", "ground coffee", "coffee grounds", "coffee pods", "pods", "k-cups", "k cups", "filters",
                    "coffee filters", "beans", "provide", "provided", "have", "stocked", "any", *_WHERE_WORDS),
           (("pods", re.compile(r"\bpods?\b|\bk-?cups?\b")), ("filters", re.compile(r"\bfilters?\b")),
            ("ground coffee", re.compile(r"\bground\s+coffee\b|\bcoffee\s+grounds\b|\bgrounds\b")),
            ("beans", re.compile(r"\bbeans\b")))),
    Intent("kitchen.appliance", "kitchen",
           (re.compile(r"\b(?:is\s+there|are\s+there|do\s+you\s+have|does\s+(?:it|the\s+(?:kitchen|unit|studio|place"
                       r"|apartment))\s+have|any)\b.{0,30}\b(?:microwave|toaster|oven|stove|blender|dishwasher"
                       r"|air\s?fryer)\b"),),
           (re.compile(r"\b(?:microwave|toaster|oven|stove|blender|dishwasher|air\s?fryer)\b"), re.compile(_PRESENCE)),
           _phrases("kitchen", "have", "has", "microwave", "toaster", "oven", "stove", "blender", "dishwasher",
                    "air fryer", "airfryer", "full-size", "any", "the unit", "the studio", "the apartment", "the place",
                    "there"),
           tuple((w, re.compile(r"\b" + w + r"\b")) for w in ("microwave", "toaster", "oven", "stove", "blender",
                                                              "dishwasher")) + (("air fryer",
                                                                                 re.compile(r"\bair\s?fryer\b")),)),
    Intent("towels.where", "towels_toiletries",
           (re.compile(r"\b(?:where|is\s+there|are\s+there|any|do\s+you\s+have)\b.{0,30}\btowels?\b"),),
           (re.compile(r"^(?:[a-z'-]+\s+){0,3}?towels?\s+(?:is|are)\b"), LOCATION_RE),
           _phrases("towels", "towel", "extra", "spare", "bath", "bath towels", "hand towels", *_WHERE_WORDS)),
    Intent("bedding.extra", "extra_bedding",
           (re.compile(r"\b(?:extra|spare|additional)\s+(?:blankets?|pillows?|bedding|comforters?)\b"),
            re.compile(r"\b(?:where|is\s+there|are\s+there|any|do\s+you\s+have)\b.{0,30}\b(?:blankets?|pillows?)\b")),
           (re.compile(r"\b(?:blankets?|pillows?)\b"), re.compile(r"\b(?:extra|spare|additional)\b")),
           _phrases("extra", "spare", "additional", "blanket", "blankets", "pillow", "pillows", "bedding", "anywhere",
                    *_WHERE_WORDS),
           (("blanket", re.compile(r"\bblankets?\b")), ("pillow", re.compile(r"\bpillows?\b")))),
    Intent("smoking.policy", "smoking",
           (re.compile(r"\bsmok\w*\b"),),
           (re.compile(r"\bsmok\w*\b"),),
           _phrases("smoking", "smoke", "policy", "allowed", "permitted", "area", "rules", "outside", "inside",
                    "indoors", "outdoors", "porch", "patio", "balcony", "yard", "deck", "anywhere", "where", "there"),
           (("outside", re.compile(r"\b(?:outside|outdoors?|out)\b")),
            ("inside", re.compile(r"\b(?:inside|indoors?)\b")), ("porch", re.compile(r"\bporch\b")),
            ("patio", re.compile(r"\bpatio\b")), ("balcony", re.compile(r"\bbalcony\b")),
            ("yard", re.compile(r"\byard\b")), ("deck", re.compile(r"\bdeck\b")))),
    Intent("quiet.hours", "noise_quiet_hours",
           (re.compile(r"\bquiet\s+hours?\b|\bnoise\s+(?:policy|rules?)\b"),),
           (re.compile(r"\bquiet\s+hours?\b|\bnoise\b"),),
           _phrases("quiet hours", "quiet", "hours", "noise", "policy", "rules", "time")),
    Intent("heating.how", "thermostat",
           (re.compile(r"\b(?:how|where)\b.{0,40}\b(?:thermostat|heat|heater|heating|radiators?)\b"),),
           (re.compile(r"\b(?:radiators?|thermostat|heat\w*)\b"),),
           _phrases("heat", "heating", "heater", "thermostat", "radiators", "radiator", "work", "works", "adjust",
                    "use", "turn", "turn up", "turn on", "warm", "warm up")),
    Intent("tv.what", "tv",
           (re.compile(r"\b(?:tv|television|roku|netflix|hulu|streaming|youtube)\b"),),
           (re.compile(r"\b(?:tv|television|roku|netflix|hulu|streaming|youtube)\b"),),
           _phrases("tv", "television", "roku", "netflix", "hulu", "youtube", "streaming", "apps", "services",
                    "smart tv", "have", "has", "kind", "kind of"),
           tuple((w, re.compile(r"\b" + w + r"\b")) for w in ("netflix", "hulu", "youtube", "roku", "disney", "hbo",
                                                              "prime"))),
]
INTENT_BY_ID = {i.id: i for i in INTENTS}
HUB_TOPICS = tuple(dict.fromkeys(i.topic for i in INTENTS))

# Asked about in the question: never a hub answer (codes and access,
# the address, parking, pets, occupancy, money, stay changes, and the
# pinned library's own topics).
BARRED_QUESTION = re.compile(
    r"\b(?:codes?|combo|combination|pass\s?codes?|pins?|keypads?|lock\s?box(?:es)?|locks?|locked|unlock\w*|keys?"
    r"|get\s+in(?:to)?|entry|enter|access|address|street|park|parking|cars?|vehicles?|pets?|dogs?|cats?|animals?"
    r"|how\s+many\s+(?:people|guests|persons|adults|kids)|occupancy|capacity|fees?|costs?|pric\w*|charge\w*|pay\w*"
    r"|refund\w*|deposit\w*|early|earlier|late|later|extend\w*|cancel\w*|check[\s-]?in|check[\s-]?out|checkout"
    r"|checkin|wi-?fi|password|internet|network)\b")


# ---------- the source set ----------

_ANCHOR_STOP = frozenset("""a an the is are was be been of and or it its it's this that these those please all your you
our we us there here will should can may have has with for as so just also any some guests guest""".split())
CRITICAL = frozenset("""in into inside on by behind beside next near under underneath above below beneath outside off
down up front back left right upstairs downstairs top bottom end side corner basement alley curb garage driveway
street yard closet hallway hall kitchen bathroom bedroom counter drawer cabinet shelf pantry porch patio deck sink
tub bathtub fridge no not never only without nothing none except gray grey black blue green white red brown yellow
silver am pm morning night daily one two three four five six seven eight nine ten""".split())


def anchors(text):
    """The claim words of a statement, for comparing two sources: content
    words (plurals folded), digits dropped."""
    out = set()
    for w in re.findall(r"[a-z]+(?:'[a-z]+)?", T.fold(text)):
        if w in _ANCHOR_STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    for d in re.findall(r"\d+", T.fold(text)):
        out.add("#" + d)  # numbers in a claim (times, counts) compare too; kept as markers only
    return out


def _is_critical(word):
    return word in CRITICAL or word.startswith("#")


def statement_intents(text):
    f = T.fold(text)
    return [i.id for i in INTENTS if all(rx.search(f) for rx in i.require)]


@dataclass(frozen=True)
class Statement:
    text: object        # verbatim, or None for evidence-only (barred) statements
    intents: tuple
    anchors: tuple      # sorted claim words (no digits for barred text: markers only)
    barred: tuple = ()  # why it can never be rendered


@dataclass(frozen=True)
class Source:
    id: str
    kind: str           # "hub" | "fact"
    label: str          # the Hub heading, or the fact key
    sha256: str         # of the raw source text
    statements: tuple

    def renderable(self):
        return [s for s in self.statements if s.text is not None]


def _statements_from_text(raw, heading_barred):
    out = []
    passage_barred = heading_barred
    prev = None  # the last statement in this passage: (index in out) or "dropped"
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            passage_barred, prev = heading_barred, None
            continue
        if _SUBHEAD_RE.match(s) and not _BULLET_RE.match(line):
            passage_barred = heading_barred or bool(BARRED_HEADING.search(T.fold(s)))
            prev = None
            continue
        s = _BULLET_RE.sub("", s).strip()
        for sent in [x.strip() for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]:
            reasons = barred_reasons(sent)
            if passage_barred:
                reasons = ["heading"] + reasons
            if _PRONOUN_START.match(sent):
                if prev is None or prev == "dropped" or reasons:
                    out.append(("evidence", sent, tuple(reasons or ["pronoun without its sentence"])))
                    prev = "dropped"
                    continue
                kind, text, why = out[prev]
                out[prev] = (kind, text + " " + sent, why)
                continue
            if reasons:
                out.append(("evidence", sent, tuple(reasons)))
                prev = "dropped"
            else:
                out.append(("ok", sent, ()))
                prev = len(out) - 1
    stmts = []
    for kind, text, why in out:
        a = anchors(text)
        if kind == "ok":
            stmts.append(Statement(text, tuple(statement_intents(text)), tuple(sorted(a))))
        else:  # evidence only: no text kept, numbers reduced to markers
            stmts.append(Statement(None, tuple(statement_intents(text)),
                                   tuple(sorted(w for w in a if not w.startswith("#"))) + ("#",) * any(
                                       w.startswith("#") for w in a), tuple(why)))
    return tuple(stmts)


def _aware(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else None


def confirmed_fact_problem(fact, now):
    """None when the operator confirmed this exact value within FACT_TTL."""
    if not isinstance(fact, dict) or not isinstance(fact.get("value"), str) or not fact["value"].strip():
        return "no value"
    if not fact.get("confirmed_at") or fact.get("confirmed_sha256") != sha256(fact["value"]):
        return "no operator confirmation of the current value"
    at = _aware(fact.get("confirmed_at"))
    if at is None or at > now + timedelta(minutes=5):
        return "unreadable or future confirmation time"
    if now - at > FACT_TTL:
        return f"confirmation older than {FACT_TTL.days} days"
    return None


@dataclass(frozen=True)
class SourceSet:
    property_id: str
    sources: tuple
    skipped: tuple      # (what, why) for everything not used as a source
    digest: str

    def to_dict(self):
        return {"schema": SNAPSHOT_SCHEMA, "property_id": self.property_id, "digest": self.digest,
                "skipped": [list(s) for s in self.skipped],
                "sources": [{"id": s.id, "kind": s.kind, "label": s.label, "sha256": s.sha256,
                             "statements": [{"text": st.text, "intents": list(st.intents),
                                             "anchors": list(st.anchors), "barred": list(st.barred)}
                                            for st in s.statements]} for s in self.sources]}


def _digest(property_id, sources):
    body = json.dumps([[s.id, s.kind, s.label, s.sha256, [[st.text, list(st.intents), list(st.anchors),
                                                          list(st.barred)] for st in s.statements]]
                       for s in sources], sort_keys=True)
    return sha256(f"hub-sources/v1:{property_id}:{body}")


def build_source_set(property_id, hub_items, facts, now):
    """The source set for one property: its [edited] Hub items and its
    operator-confirmed facts (the fact sheet's `facts` dict), cut into
    statements. Everything else is listed in `skipped` with why."""
    sources, skipped = [], []
    for it in hub_items or ():
        if it.tag != "edited":
            skipped.append((f"hub item ({it.heading}, line {it.line})", f"tagged [{it.tag}]: never a source"))
            continue
        hb = bool(BARRED_HEADING.search(T.fold(it.heading)))
        stmts = _statements_from_text(it.text, hb)
        sources.append(Source(f"hub:{sha256(it.heading + chr(10) + it.text)[:12]}", "hub", it.heading,
                              sha256(it.text), stmts))
    for key in sorted((facts or {})):
        fact = facts[key]
        why = confirmed_fact_problem(fact, now)
        if why:
            skipped.append((f"fact {key}", why))
            continue
        stmts = _statements_from_text(fact["value"], False)
        sources.append(Source(f"fact:{key}", "fact", key, sha256(fact["value"]), stmts))
    return SourceSet(property_id, tuple(sources), tuple(skipped), _digest(property_id, sources))


def source_set_from_dict(doc):
    """A SourceSet from its snapshot dict; the digest is recomputed and must
    match the one stored (a hand edit is refused)."""
    if not isinstance(doc, dict) or doc.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("not a hub source snapshot")
    sources = tuple(Source(s["id"], s["kind"], s["label"], s["sha256"],
                           tuple(Statement(st["text"], tuple(st["intents"]), tuple(st["anchors"]),
                                           tuple(st.get("barred") or ())) for st in s["statements"]))
                    for s in doc.get("sources") or [])
    d = _digest(doc["property_id"], sources)
    if d != doc.get("digest"):
        raise ValueError("hub source snapshot digest mismatch")
    return SourceSet(doc["property_id"], sources, tuple(tuple(x) for x in doc.get("skipped") or ()), d)


# ---------- the plan ----------

@dataclass(frozen=True)
class HubPlan:
    verdict: str              # "answer" | "hold"
    reason: str
    detail: str = ""
    text: object = None
    sha256: object = None
    intent: object = None
    topic: object = None
    items: tuple = ()
    source_ids: tuple = ()
    statement: object = None
    source_digest: object = None

    def as_log(self):
        return {"category": CATEGORY, "verdict": self.verdict, "reason": self.reason, "detail": self.detail,
                "text": self.text, "text_sha256": self.sha256, "intent": self.intent, "topic": self.topic,
                "items": list(self.items), "source_ids": list(self.source_ids), "statement": self.statement,
                "source_digest": self.source_digest}


def _hold(reason, detail="", **kw):
    return HubPlan("hold", reason, detail, **kw)


def classify(message):
    """(intent, items, None) for a message that is exactly one hub question;
    (None, (), (reason, detail)) otherwise. The repo's own whole-message
    checks; the actuator's gates run as well, unchanged."""
    raw = message or ""
    if len(raw) > MAX_MESSAGE_CHARS:
        return None, (), ("too_long", f"{len(raw)} chars")
    sym = T.risky_symbols(raw)
    if sym:
        return None, (), ("safety_symbol", f"symbols {''.join(sym)!r}")
    if guarded_class is None:
        return None, (), ("guard_unavailable", "jev.change_guard is not importable")
    g = guarded_class(raw)
    if g:
        return None, (), ("stay_or_money", f"phase0 guard: {g}")
    text = T.fold(T.guest_words(raw))
    b = BARRED_QUESTION.search(text)
    if b:
        return None, (), ("barred", f"asks about a barred subject: {b.group(0)!r}")
    for name, rx in BARRED_RULES:
        if name in ("safety", "money", "pets", "occupancy", "parking", "code", "code_shaped"):
            m = rx.search(text)
            if m:
                return None, (), ("barred", f"{name}: {m.group(0)!r}")
    asks = T.request_hits(text)
    if asks:
        return None, (), ("request", f"request or issue: {', '.join(asks)[:120]}")
    content = T.content_sentences(text)
    if len(content) != 1:
        return None, (), ("not_one_question", f"{len(content)} content sentences")
    sent = content[0]
    if not T.is_question(sent):
        return None, (), ("not_a_question", sent[:80])
    hit = [i for i in INTENTS if any(rx.search(sent) for rx in i.question)]
    if not hit:
        return None, (), ("no_hub_topic", sent[:80])
    if len(hit) > 1:
        return None, (), ("multi", f"intents {[i.id for i in hit]}")
    intent = hit[0]
    gaps = T.coverage_gaps(text, intent.cover)
    if gaps:
        return None, (), ("coverage", f"not covered by {intent.id}: {', '.join(gaps)}"[:200])
    items = tuple(name for name, rx in intent.items if rx.search(text))
    return intent, items, None


def _consistent(a, b):
    """Two sources' claims agree when one's claim words are all in the
    other's and nothing they differ on is a place, a side, a color, a
    number, a time, or a negation."""
    a, b = set(a), set(b)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if not small <= big:
        return False
    return not any(_is_critical(w) for w in big - small)


def answer_for(intent, items, source_set):
    """The answer from the source set for a classified question, or a hold."""
    by_source = []
    for src in source_set.sources:
        ev = [st for st in src.statements if intent.id in st.intents]
        if ev:
            by_source.append((src, ev))
    if not by_source:
        return _hold("missing", f"no source states {intent.id}", intent=intent.id, topic=intent.topic, items=items)
    renderable = [(src, [st for st in ev if st.text is not None]) for src, ev in by_source]
    renderable = [(src, ev) for src, ev in renderable if ev]
    if not renderable:
        return _hold("barred_source", f"every source for {intent.id} is barred", intent=intent.id,
                     topic=intent.topic, items=items)
    # Compare every source's claims against every other source's (barred
    # evidence included): any disagreement holds.
    claims = [(src.id, set().union(*[set(st.anchors) for st in ev])) for src, ev in by_source]
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if not _consistent(claims[i][1], claims[j][1]):
                return _hold("conflict", f"{claims[i][0]} and {claims[j][0]} disagree on {intent.id}",
                             intent=intent.id, topic=intent.topic, items=items,
                             source_ids=(claims[i][0], claims[j][0]))
    # Agreeing sources: send the one whose claims every other source makes.
    src, ev = min(renderable, key=lambda p: (len(set().union(*[set(st.anchors) for st in p[1]])), p[0].id))
    statement = " ".join(st.text for st in ev)
    folded = T.fold(statement)
    missing = [name for name, rx in intent.items if name in items and not rx.search(folded)]
    if missing:
        return _hold("item_not_covered", f"the source does not mention {', '.join(missing)}", intent=intent.id,
                     topic=intent.topic, items=items, source_ids=(src.id,))
    text = "Hi! " + statement
    problems = barred_reasons(text)
    if len(text) > MAX_ANSWER_CHARS:
        problems.append("too long")
    if problems:
        return _hold("render_check", f"rendered answer fails: {problems}", intent=intent.id, topic=intent.topic,
                     items=items, source_ids=(src.id,))
    return HubPlan("answer", "answer", f"{len(by_source)} source(s) agree", text, sha256(text), intent.id,
                   intent.topic, items, tuple(s.id for s, _ in by_source), statement, source_set.digest)


def plan(message, property_id, source_set, *, reservation=None, scope=None, excluded=()):
    """The one function that produces a hub answer, or a hold."""
    if scope is not None and property_id not in scope:
        return _hold("scope", f"{property_id} is not one of the single units in scope")
    if property_id in set(excluded or ()):
        return _hold("excluded", "this listing never gets an answer")
    why = reservation_problem(reservation)
    if why:
        return _hold("reservation", why)
    if source_set is None or source_set.property_id != property_id:
        return _hold("no_sources", "no hub source set for this property")
    intent, items, prob = classify(message)
    if prob:
        return _hold(*prob)
    return answer_for(intent, items, source_set)
