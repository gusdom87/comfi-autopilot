"""Tests for mapping answers (geo_answers.py, geo_fixtures.py). Synthetic
coordinates and invented places; the OSM client runs against a fake HTTP
layer. Nothing touches the network."""
import json
import os
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev import geo_answers as G
from jev import geo_fixtures as GF
from jev.hub_answers import sha256

OK = {"status": "accepted"}
SCOPE = ("cedar-1a", "cedar-1b", "cedar-2a", "cedar-2b")


def _plan(msg, provider=None, **kw):
    kw.setdefault("reservation", OK)
    kw.setdefault("scope", SCOPE)
    return G.plan(msg, "cedar-1a", GF.ORIGIN, provider or GF.FixtureProvider(), **kw)


def test_rounding_is_honest_and_approximate():
    mi = G.MILE_M
    assert G.fmt_miles(0.43 * mi) == "0.4 miles"
    assert G.fmt_miles(1.18 * mi) == "1 mile"
    assert G.fmt_miles(1.43 * mi) == "1.5 miles"
    assert G.fmt_miles(3.23 * mi) == "3 miles"
    assert G.fmt_miles(22.06 * mi) == "22 miles"
    assert G.fmt_miles(42.3 * mi) == "40 miles"
    assert G.fmt_minutes(0.4) == "1 minute"
    assert G.fmt_minutes(9) == "9 minutes"
    assert G.fmt_minutes(14) == "15 minutes"
    assert G.fmt_minutes(58) == "1 hour"
    assert G.fmt_minutes(72) == "1 hour 10 minutes"
    assert G.fmt_minutes(170) == "2 hours 50 minutes"


def test_the_two_templates():
    # a live lookup's drive time is free-flow (OSRM): it says so
    assert G.render("Oak Street Coffee", G.Route(1300.0, 180.0)) == \
        "Hi! Oak Street Coffee is about 0.8 miles away, roughly 3 minutes by car without traffic."
    # an operator-pinned answer (times the operator checked) keeps the plain wording
    assert G.render("Oak Street Coffee", G.Route(1300.0, 180.0), pinned=True) == \
        "Hi! Oak Street Coffee is about 0.8 miles away, roughly 3 minutes by car."
    # the short walk needs no qualifier either way
    for pinned in (False, True):
        assert G.render("Rosa's Taqueria", G.Route(350.0, 60.0), pinned=pinned) == \
            "Hi! Rosa's Taqueria is a short walk, about 4 minutes."


def test_live_lookups_say_without_traffic_and_pinned_answers_do_not():
    for msg in ("How far is Sakura Garden?", "Where's the nearest pharmacy?", "How far is Grand Harbor Pier?"):
        p = _plan(msg)
        assert p.verdict == "answer" and not p.override and p.text.endswith(" by car without traffic."), (msg, p)
    body = {"place": "Sakura Garden", "distance_m": 9000.0, "duration_s": 900.0}
    ov = dict(body, confirmed_sha256=sha256(json.dumps(body, sort_keys=True)))
    p = _plan("How far is Sakura Garden?", overrides={G.override_key("cedar-1a", "sakura garden"): ov})
    assert p.override and p.text.endswith(" by car.") and "traffic" not in p.text


def test_named_places_answer_with_their_fixture_route():
    for msg, pid in (("How far is Grand Harbor Pier?", "p-pier"), ("how far is Sakura Garden?", "p-sakura"),
                     ("How long to drive to Prairie Amphitheater?", "p-amph"),
                     ("Is Hollow Ridge Zoo far from here?", "p-zoo"), ("How close is Starlight Cinema?", "p-cinema")):
        p = _plan(msg)
        assert p.verdict == "answer" and p.place["osm_id"] == pid, (msg, p)
        assert p.text == G.render(GF.PLACES[pid].name, GF.ROUTES[pid])


def test_nearest_of_type_is_the_shortest_drive():
    p = _plan("Where's the nearest pharmacy?")
    assert p.place["osm_id"] == "p-pharm" and p.text.startswith("Hi! The nearest pharmacy, Cedar Pharmacy, is about")
    assert _plan("Is there a Japanese restaurant nearby?").place["osm_id"] == "p-sakura"   # cuisine filtered
    assert _plan("Where is the closest train station?").place["osm_id"] == "p-station"
    assert _plan("how far is the train station?").place["osm_id"] == "p-station"           # "the <type>" is a type
    assert _plan("Any pizza places nearby?").place["osm_id"] == "p-pizza"


def test_ambiguity_holds():
    assert _plan("How far is Lakeview Park?").reason == "ambiguous"        # within 15%
    assert _plan("How far is Lincoln Square?").reason == "ambiguous"       # better-known one farther away
    p = _plan("How far is Riverside Park?")                                # far apart, same prominence
    assert p.verdict == "answer" and p.place["osm_id"] == "p-riverside-a"


def test_an_exact_name_beats_a_decoy_that_contains_it():
    p = _plan("How far is Grand Harbor Pier?")
    assert p.place["osm_id"] == "p-pier"  # not "Grand Harbor Pier Dental", 3 km away


def test_one_place_returned_twice_is_one_place():
    places = dict(GF.PLACES)
    dup = GF.PLACES["p-sakura"]
    places["p-sakura-way"] = G.Place(dup.name, dup.lat + 0.0003, dup.lon, dup.kinds, dup.importance, dup.cuisine,
                                     "p-sakura-way")
    routes = dict(GF.ROUTES, **{"p-sakura-way": GF.ROUTES["p-sakura"]})
    p = _plan("How far is Sakura Garden?", GF.FixtureProvider(places, routes))
    assert p.verdict == "answer"


def test_far_unknown_and_lookup_failures_hold():
    assert _plan("How far is Summit Peak Resort?").verdict == "hold"
    assert _plan("How far is Moonbase Alpha?").reason == "not_found"
    assert _plan("How far is Sakura Garden?", GF.FixtureProvider(fail_routes={"p-sakura"})).reason == "no_route"

    class Down(GF.FixtureProvider):
        def search(self, *a, **k):
            raise G.ProviderError("timeout")
    assert _plan("How far is Sakura Garden?", Down()).reason == "lookup_failed"
    bad = dict(GF.ROUTES, **{"p-sakura": G.Route(900000.0, 60.0)})  # absurd route: suspect data
    assert _plan("How far is Sakura Garden?", GF.FixtureProvider(routes=bad)).reason == "no_route"


def test_barred_types_and_personal_destinations_hold():
    for msg in ("How far is Cedar General Hospital?", "Where's the nearest urgent care?",
                "Where is the closest gas station?", "How far is the police station?",
                "How far is my friend's house?", "How far is 1427 Maple Ridge Dr?", "How far is Elm Street?",
                "how far is downtown?", "How far is the airport?"):
        p = _plan(msg)
        assert p.verdict == "hold", (msg, p)
    p = _plan("How far is Jordan Lake?", forbidden_tokens=("Jordan", "Avery"))
    assert p.verdict == "hold" and "guest's name" in p.detail


def test_whole_message_rules_hold():
    for msg in ("How far is Sakura Garden? Also it smells like gas", "How far is Sakura Garden? \U0001F6A8",
                "Can you tell me how far Sakura Garden is?", "How far is Sakura Garden? We leave tomorrow.",
                "How far is Sakura Garden and is there a cleaning fee?", "Could we check out late? How far is the zoo?",
                "How far is Sakura Garden and Starlight Cinema?"):
        assert _plan(msg).verdict == "hold", msg
    assert _plan("Hi! How far is Sakura Garden? Thanks!").verdict == "answer"


def test_scope_and_reservation_status():
    assert _plan("How far is Sakura Garden?", reservation={"status": "inquiry"}).reason == "reservation"
    assert _plan("How far is Sakura Garden?", reservation={"status": "accepted",
                                                           "reservation_status": {"current": {"category": "request"}}}
                 ).reason == "reservation"
    assert G.plan("How far is Sakura Garden?", "cedar-retreat", GF.ORIGIN, GF.FixtureProvider(), reservation=OK,
                  scope=SCOPE).reason == "scope"
    assert G.plan("How far is Sakura Garden?", "cedar-1a", None, GF.FixtureProvider(), reservation=OK,
                  scope=SCOPE).reason == "no_origin"


def test_only_the_destination_phrase_leaves():
    prov = GF.FixtureProvider()
    msg = "Hi there! How far is Sakura Garden? Thanks so much!"
    p = _plan(msg, prov, forbidden_tokens=("Jordan",))
    assert p.verdict == "answer"
    searches = [s for s in prov.sent if s[0] == "search"]
    assert searches == [("search", "sakura garden", GF.ORIGIN, G.NAMED_RADIUS_KM)]
    assert p.sent and all("there" not in str(s).lower() and "thanks" not in str(s).lower() for s in p.sent)


def test_operator_pinned_override_is_used_only_while_its_hash_matches():
    body = {"place": "Sakura Garden", "distance_m": 9000.0, "duration_s": 900.0}
    ov = dict(body, confirmed_sha256=sha256(json.dumps(body, sort_keys=True)), note="checked on Google Maps")
    key = G.override_key("cedar-1a", "sakura garden")
    prov = GF.FixtureProvider()
    p = _plan("How far is Sakura Garden?", prov, overrides={key: ov})
    assert p.override and p.text == "Hi! Sakura Garden is about 5.5 miles away, roughly 15 minutes by car."
    assert prov.sent == []  # a pinned answer makes no lookup
    tampered = dict(ov, distance_m=100.0)
    assert _plan("How far is Sakura Garden?", overrides={key: tampered}).reason == "override_tampered"


# ---------- the OSM client against a fake HTTP layer ----------

class FakeHTTP:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "/search?" in url:
            return [{"name": "Sakura Garden", "lat": "41.0123", "lon": "-86.9187", "category": "amenity",
                     "type": "restaurant", "importance": 0.1, "extratags": {"cuisine": "japanese;sushi"},
                     "osm_type": "node", "osm_id": 1}]
        return {"code": "Ok", "routes": [{"distance": 8900.0, "duration": 840.0}]}


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _osm(tmp, http=None, clock=None, cache="geo-cache.json"):
    clock = clock or Clock()
    return G.OSMProvider(os.path.join(tmp, cache), http_get=http or FakeHTTP(), clock=clock,
                         sleep=clock.sleep, rate_path=os.path.join(tmp, "osm-rate.json")), clock


def test_osm_client_rate_limit_cache_and_coarse_coordinates():
    with tempfile.TemporaryDirectory() as tmp:
        http = FakeHTTP()
        prov, clock = _osm(tmp, http)
        origin = (41.012345, -87.006789)
        places = prov.search("sakura garden", origin, 80.0)
        route = prov.route(origin, places[0])
        assert places[0].name == "Sakura Garden" and places[0].cuisine == ("japanese", "sushi")
        assert route == G.Route(8900.0, 840.0)
        assert clock.slept and all(s <= 1.0 for s in clock.slept) and abs(sum(clock.slept) - 1.0) < 1e-9
        q = urllib.parse.parse_qs(urllib.parse.urlparse(http.urls[0]).query)
        assert q["q"] == ["sakura garden"] and q["bounded"] == ["1"]
        assert all(len(v.split(".")[1]) == 1 for v in q["viewbox"][0].split(","))  # 0.1 degree box
        assert "-87.007,41.012;" in http.urls[1]                                   # origin to 3 decimals
        assert "41.012345" not in "".join(http.urls)
        # cached: the same lookups make no second request
        prov2, _ = _osm(tmp, http)
        prov2.search("sakura garden", origin, 80.0)
        prov2.route(origin, places[0])
        assert len(http.urls) == 2
        doc = json.load(open(os.path.join(tmp, "geo-cache.json")))
        assert doc["schema"] == "comfi-autopilot/geo-cache/v1" and len(doc["entries"]) == 2
        assert oct(os.stat(os.path.join(tmp, "geo-cache.json")).st_mode & 0o777) == "0o600"


def test_osm_client_errors_become_provider_errors_and_bad_routes_none():
    with tempfile.TemporaryDirectory() as tmp:
        def boom(url):
            raise OSError("proxy reset")
        prov, _ = _osm(tmp, boom)
        try:
            prov.search("x", (41.0, -87.0), 10)
            raise AssertionError("no error")
        except G.ProviderError as e:
            assert "proxy reset" in str(e)
        prov2, _ = _osm(tmp, lambda url: {"code": "NoRoute"})
        assert prov2.route((41.0, -87.0), G.Place("x", 41.1, -87.1)) is None


def test_osm_client_retries_one_transient_failure_with_the_same_spacing():
    with tempfile.TemporaryDirectory() as tmp:
        real, calls = FakeHTTP(), []

        def flaky(url):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("IncompleteRead")
            return real(url)
        prov, clock = _osm(tmp, flaky)
        assert prov.search("sakura garden", (41.0, -87.0), 80.0)[0].name == "Sakura Garden"
        assert len(calls) == 2 and clock.slept == [1.0]  # the retry waited out the 1 req/s spacing
        prov2, _ = _osm(tmp, lambda url: (_ for _ in ()).throw(OSError("down")))
        try:
            prov2.route((41.0, -87.0), G.Place("x", 41.1, -87.1))
            raise AssertionError("no error")
        except G.ProviderError:
            pass


def test_cache_write_rides_out_eperm():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        real, calls, waits = os.replace, [], []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(1, "Operation not permitted")
            return real(a, b)
        os.replace = flaky
        try:
            G.write_json_atomic(path, {"ok": 1}, sleep=waits.append)
        finally:
            os.replace = real
        assert json.load(open(path)) == {"ok": 1} and waits == [5, 10]
        assert [f for f in os.listdir(tmp) if f.startswith(".tmp-")] == []


def test_the_end_to_end_plan_runs_on_the_osm_client():
    with tempfile.TemporaryDirectory() as tmp:
        prov, _ = _osm(tmp)
        p = G.plan("How far is Sakura Garden?", "cedar-1a", (41.0, -87.0), prov, reservation=OK, scope=SCOPE)
        assert p.verdict == "answer" and p.text == \
            "Hi! Sakura Garden is about 5.5 miles away, roughly 15 minutes by car without traffic."
        assert [s[0] for s in p.sent] == ["search", "route"] and p.sent[0][1] == "sakura garden"


# ---------- one request per second across instances and processes ----------

def test_two_instances_share_one_limit():
    """The actuator glue builds a new provider per message: two providers
    (different caches, so neither is a cache hit) still space their
    requests one second apart, because they share the machine's gate."""
    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        a, _ = _osm(tmp, clock=clock, cache="cache-a.json")
        b, _ = _osm(tmp, clock=clock, cache="cache-b.json")
        a.search("sakura garden", (41.0, -87.0), 80.0)
        assert clock.slept == []                       # the first request of all waits for nothing
        b.search("sakura garden", (41.0, -87.0), 80.0)
        assert clock.slept == [1.0]                    # a second instance waits out the first one's second
        clock.t += 0.4
        a.route((41.0, -87.0), G.Place("Sakura Garden", 41.0123, -86.9187))
        assert abs(clock.slept[-1] - 0.6) < 1e-9       # and the first waits for the second
        doc = json.load(open(os.path.join(tmp, "osm-rate.json")))
        assert doc["schema"] == "comfi-autopilot/osm-rate/v1" and doc["last_request_at"] == clock.t
        assert oct(os.stat(os.path.join(tmp, "osm-rate.json")).st_mode & 0o777) == "0o600"


def test_the_default_gate_is_one_per_machine_whatever_the_cache():
    old = os.environ.pop("JEV_OSM_RATE_FILE", None)
    try:
        a = G.OSMProvider("/nonexistent/a/geo-cache.json")
        b = G.OSMProvider("/nonexistent/b/other-cache.json")
        assert a.gate.path == b.gate.path == os.path.expanduser(G.DEFAULT_RATE_FILE)
        os.environ["JEV_OSM_RATE_FILE"] = "/nonexistent/x/rate.json"
        assert G.OSMProvider("/nonexistent/c.json").gate.path == "/nonexistent/x/rate.json"
    finally:
        os.environ.pop("JEV_OSM_RATE_FILE", None)
        if old is not None:
            os.environ["JEV_OSM_RATE_FILE"] = old


def test_a_clock_behind_the_stamp_or_an_unreadable_stamp_waits_the_full_interval():
    with tempfile.TemporaryDirectory() as tmp:
        rate = os.path.join(tmp, "osm-rate.json")
        G.write_json_atomic(rate, {"schema": G.RATE_SCHEMA, "last_request_at": 5000.0})  # ahead of the clock
        prov, clock = _osm(tmp)
        prov.search("sakura garden", (41.0, -87.0), 80.0)
        assert clock.slept == [1.0]
        with open(rate, "w") as fh:
            fh.write("{not json")
        prov2, clock2 = _osm(tmp, cache="other.json")
        prov2.search("sakura garden", (41.0, -87.0), 80.0)
        assert clock2.slept == [1.0]


def test_a_gate_that_stays_locked_holds_instead_of_requesting():
    import fcntl
    with tempfile.TemporaryDirectory() as tmp:
        http = FakeHTTP()
        prov, _ = _osm(tmp, http)
        prov.gate.lock_timeout = 0.2
        fd = os.open(os.path.join(tmp, "osm-rate.json.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)              # another process mid-request
            p = G.plan("How far is Sakura Garden?", "cedar-1a", (41.0, -87.0), prov, reservation=OK, scope=SCOPE)
        finally:
            os.close(fd)
        assert p.verdict == "hold" and p.reason == "lookup_failed" and "rate gate" in p.detail
        assert http.urls == []                          # nothing went out around the gate


_WORKER = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from jev import geo_answers as G
log, rate, cache, tag, interval = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], float(sys.argv[6])
go = float(sys.argv[7])

def http(url):
    t = time.time()
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, (json.dumps([t, tag]) + "\n").encode())
    os.close(fd)
    return []

prov = G.OSMProvider(cache, http_get=http, min_interval=interval, rate_path=rate)
while time.time() < go:
    time.sleep(0.005)
for i in range(3):
    prov.search(f"{tag} place {i}", (41.0, -87.0), 10.0)
"""


def test_two_processes_share_one_limit():
    """Two OS processes, each with its own provider and cache, started at
    the same instant: every request (six in all) starts at least one
    interval after the previous one ended, whichever process made it. The
    interval is shortened to 0.25 s to keep the suite fast; the mechanism
    is the one the 1 s default uses."""
    import subprocess
    import sys as _sys
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    interval = 0.25
    with tempfile.TemporaryDirectory() as tmp:
        log, rate = os.path.join(tmp, "requests.jsonl"), os.path.join(tmp, "osm-rate.json")
        go = f"{__import__('time').time() + 1.0:.3f}"
        procs = [subprocess.Popen([_sys.executable, "-c", _WORKER, src, log, rate, os.path.join(tmp, f"c-{t}.json"),
                                   t, str(interval), go]) for t in ("a", "b")]
        assert [p.wait(timeout=60) for p in procs] == [0, 0]
        rows = sorted(json.loads(x) for x in open(log))
        assert len(rows) == 6 and {t for _, t in rows} == {"a", "b"}
        gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        assert min(gaps) >= interval - 1e-3, gaps
