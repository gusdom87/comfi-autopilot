"""Blind sets H, H2 (hub answers) and G (mapping answers), built Oct 31, 2026.

The messages were written by separate model runs that made zero tool
calls: they never saw the engines, the fixtures' code, or these tests.
They were given only the fact space in plain words (set H, H2) or the
place list (set G) and a class mix, and labeled every row themselves.
Set H and G arrived before the hub engine was finished (the geo engine
was already written); both engines were frozen in commit f1c9327 before
either set ran. Set H2 was commissioned after that freeze and run once.

The bar (the brief, Oct 31, 2026): ZERO wrong answers and ZERO trap
answers. Recall is reported, whatever it is: `python3 tests/test_blind_sets_hg.py`
prints the table and every false hold with its reason.

Grading. A hub answer is right only when it is exactly "Hi! " + the
labeled fact's source statement. A mapping answer is right only when it
names the labeled place and its numbers are that place's fixture route.
Any answer on a row labeled hold is a trap answer.
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev import geo_answers as G
from jev import geo_fixtures as GF
from jev import hub_answers as H
from jev import hub_fixtures as HF

HERE = os.path.join(os.path.dirname(__file__), "blind_sets")
UNIT = {"1A": HF.CEDAR_1A, "1B": HF.CEDAR_1B, "2A": HF.CEDAR_2A, "2B": HF.CEDAR_2B, "retreat": HF.CEDAR_RETREAT}

FACT_STATEMENT = {
    "H1A-trash": "Trash goes in the gray bins on the side of the garage, next to the recycling cart.",
    "H1A-laundry-where": "The shared laundry room is in the basement, down the back stairs.",
    "H1A-laundry-supplies": "Detergent and dryer sheets are on the shelf above the washer.",
    "H1A-coffee-maker": "The kitchen has a drip coffee maker and an electric kettle.",
    "H1A-coffee-supplies": "Ground coffee and paper filters are in the cabinet above the sink.",
    "H1A-smoking": "No smoking anywhere on the property, including the porch.",
    "H1A-quiet": "Quiet hours are 10:00 PM to 7:00 AM.",
    "H1B-laundry-where": "The washer and dryer are inside the unit, in the hallway closet.",
    "H1B-laundry-supplies": "Laundry detergent pods are under the kitchen sink.",
    "H1B-towels": "Extra bath towels are on the top shelf of the bathroom closet.",
    "H1B-smoking": "Smoking is not allowed anywhere at the unit, inside or out.",
    "H2A-laundry-where": ("The laundry room is shared with the other units. It is at the front of the building, "
                          "on the left side when you face the house."),
    "H2A-bedding": "An extra blanket and two spare pillows are in the closet by the front door.",
    "H2A-smoking": "Smoking is not allowed inside the studio. Smoking is allowed only at the bench by the side yard.",
    "H2B-laundry-where": "The on-site laundry room is on the left side of the building, shared with the other apartments.",
    "H2B-heating": ("The radiators heat slowly, so give the unit about an hour to warm up after you change "
                    "the thermostat."),
    "H2B-trash": "Trash and recycling go in the blue bins at the end of the driveway.",
    "H2B-kitchen": "The kitchen has a microwave, a toaster, and a full-size oven.",
    "H2B-quiet": "Quiet hours are 10:00 PM to 8:00 AM.",
}


def load(name):
    path = os.path.join(HERE, name)
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def source_sets():
    sheet = HF.fact_sheet()["properties"]
    return {pid: H.build_source_set(pid, H.parse_hub_markdown(HF.HUB_MARKDOWN[pid]), sheet[pid]["facts"],
                                    HF.FIXTURE_NOW) for pid in UNIT.values()}


def run_hub(rows):
    sets = source_sets()
    out = []
    for r in rows:
        pid = UNIT[r["unit"]]
        p = H.plan(r["message"], pid, sets[pid], reservation=HF.ACCEPTED, scope=HF.SINGLE_UNITS,
                   excluded=(HF.CEDAR_RETREAT,))
        grade = "hold"
        if p.verdict == "answer":
            if r["expect"] != "answer":
                grade = "trap_answer"
            elif p.text == "Hi! " + FACT_STATEMENT[r["expect_fact"]]:
                grade = "right"
            else:
                grade = "wrong"
        out.append((r, p, grade))
    return out


def run_geo(rows):
    out = []
    for r in rows:
        prov = GF.FixtureProvider()
        p = G.plan(r["message"], HF.CEDAR_1A, GF.ORIGIN, prov, reservation={"status": r["reservation"]},
                   scope=HF.SINGLE_UNITS, excluded=(HF.CEDAR_RETREAT,))
        grade = "hold"
        if p.verdict == "answer":
            want = r.get("expect_place")
            if r["expect"] != "answer":
                grade = "trap_answer"
            elif (want and p.place and p.place.get("osm_id") == want
                  and p.route == {"distance_m": GF.ROUTES[want].distance_m, "duration_s": GF.ROUTES[want].duration_s}
                  and p.text in (G.render(GF.PLACES[want].name, GF.ROUTES[want]),
                                 G.render(f"The nearest {G.type_for(p.dest).label}, {GF.PLACES[want].name},"
                                          if p.mode == "type" else "", GF.ROUTES[want]))):
                grade = "right"
            else:
                grade = "wrong"
        out.append((r, p, grade, prov.sent))
    return out


def summary(results):
    by = defaultdict(Counter)
    for res in results:
        r, grade = res[0], res[2]
        by[r["class"]][grade] += 1
        by["all"][grade] += 1
    return by


# ---------- the bar ----------

def test_set_h_zero_wrong_zero_trap():
    res = run_hub(load("set_h.jsonl"))
    assert len(res) == 60
    bad = [(r["id"], g, p.text) for r, p, g in res if g in ("wrong", "trap_answer")]
    assert bad == []


def test_set_h2_zero_wrong_zero_trap():
    path = os.path.join(HERE, "set_h2.jsonl")
    if not os.path.exists(path):  # commissioned after the freeze; absent only before it landed
        return
    res = run_hub(load("set_h2.jsonl"))
    assert len(res) == 60
    bad = [(r["id"], g, p.text) for r, p, g in res if g in ("wrong", "trap_answer")]
    assert bad == []


def test_set_g_zero_wrong_zero_trap():
    res = run_geo(load("set_g.jsonl"))
    assert len(res) == 40
    bad = [(r["id"], g, p.text) for r, p, g, _ in res if g in ("wrong", "trap_answer")]
    assert bad == []


def test_set_g_only_the_destination_leaves():
    """Privacy: each search the provider was sent carries the parsed
    destination phrase or a type's fixed search words, nothing else from
    the message; routes carry coordinates only."""
    type_queries = {spec.query for _, spec in G.TYPES}
    for r, p, g, sent in run_geo(load("set_g.jsonl")):
        for s in sent:
            if s[0] == "search":
                assert s[1] == p.dest or s[1] in type_queries, (r["id"], s)
                assert len(s[1].split()) <= 6
            else:
                assert s[0] == "route" and all(isinstance(x, float) for x in s[1] + s[2])


def test_sets_are_well_formed():
    for name, n in (("set_h.jsonl", 60), ("set_g.jsonl", 40)):
        rows = load(name)
        assert len(rows) == n and len({r["id"] for r in rows}) == n
        for r in rows:
            assert r["expect"] in ("answer", "hold")
            if "expect_fact" in r and r["expect"] == "answer":
                assert r["expect_fact"] in FACT_STATEMENT
            if "expect_place" in r and r["expect"] == "answer":
                assert r["expect_place"] in GF.PLACES


def _table(title, results):
    by = summary(results)
    lines = [f"\n{title}", f"{'class':16} {'n':>3} {'right':>6} {'held':>5} {'wrong':>6} {'trap':>5}"]
    for cls in sorted(by, key=lambda c: (c == "all", c)):
        c = by[cls]
        lines.append(f"{cls:16} {sum(c.values()):>3} {c['right']:>6} {c['hold']:>5} {c['wrong']:>6} "
                     f"{c['trap_answer']:>5}")
    answerable = [x for x in results if x[0]["expect"] == "answer"]
    right = sum(1 for x in answerable if x[2] == "right")
    lines.append(f"recall: {right}/{len(answerable)} genuine questions answered")
    lines.append("false holds:")
    for x in answerable:
        if x[2] == "hold":
            lines.append(f"  {x[0]['id']}: {x[1].reason}: {x[1].detail[:110]}  <- {x[0]['message'][:70]!r}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(_table("SET H (hub)", run_hub(load("set_h.jsonl"))))
    if os.path.exists(os.path.join(HERE, "set_h2.jsonl")):
        print(_table("SET H2 (hub, held out)", run_hub(load("set_h2.jsonl"))))
    print(_table("SET G (geo)", run_geo(load("set_g.jsonl"))))
    for r, p, g, _ in run_geo(load("set_g.jsonl")):
        if g in ("wrong", "trap_answer"):
            print("BAD", r["id"], g, p.text)
    for r, p, g in run_hub(load("set_h.jsonl")):
        if g in ("wrong", "trap_answer"):
            print("BAD", r["id"], g, p.text)
