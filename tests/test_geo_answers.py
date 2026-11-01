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


ADDRESS_NAMES = ("45 Archer Ave", "12 Main St", "Unit 2 Apt 3", "45th St", "200 W Elm Street", "7 Oak Lane",
                 "Suite #4", "5 Points Plaza", "88 N Halsted St.")
NORMAL_NAMES = ("Sakura Garden", "Oak Street Coffee", "Main Street Cafe", "Archer Avenue Diner", "7-Eleven",
                "Route 66 Diner", "Studio 54", "Five Guys", "Joe's Place", "Lane Bryant", "Rosa's Taqueria",
                "2 Sisters Cafe", "24 Hour Fitness")


def _with(name, km=1.0, route_km=1.3, kinds=("amenity:restaurant",), cuisine=("pizza",), pid="p-addr"):
    """The fixture map plus one place called `name`, nearer than every pizza place."""
    lat, lon = GF._offset(GF.ORIGIN, km, 45)
    places = dict(GF.PLACES, **{pid: G.Place(name, lat, lon, kinds, 0.1, cuisine, pid)})
    routes = dict(GF.ROUTES, **{pid: G.Route(route_km * 1000.0, 180.0)})
    return GF.FixtureProvider(places, routes)


def test_address_shaped_place_names_are_recognized_and_normal_names_are_not():
    for name in ADDRESS_NAMES:
        assert G.address_like(name), name
        assert not G._name_ok(name), name
    for name in NORMAL_NAMES + tuple(p.name for p in GF.PLACES.values()):
        assert G.address_like(name) is None, name
        assert G._name_ok(name), name
    # the template's own words after a number never read as an address
    for route in (G.Route(350.0, 60.0), G.Route(1300.0, 180.0), G.Route(9000.0, 900.0), G.Route(98000.0, 4320.0)):
        for pinned in (False, True):
            for label in ("Sakura Garden", "The nearest pizza place, Cedar Slice Pizza,"):
                assert G.address_like(G.render(label, route, pinned=pinned)) is None


def test_an_address_shaped_nearest_place_holds_and_never_renders():
    for name in ("45 Archer Ave", "12 Main St", "Unit 2 Apt 3"):
        p = _plan("Any pizza places nearby?", _with(name))
        assert p.verdict == "hold" and p.reason == "place_name", (name, p)
        assert p.text is None and name not in str(p.as_log()["text"])
    # an address-shaped row that is NOT the nearest does not block the answer
    p = _plan("Any pizza places nearby?", _with("45 Archer Ave", km=6.0, route_km=7.0))
    assert p.verdict == "answer" and p.place["osm_id"] == "p-pizza" and "Archer" not in p.text
    # a normal name in the same spot answers as before
    p = _plan("Any pizza places nearby?", _with("Archer Avenue Diner"))
    assert p.verdict == "answer" and p.text.startswith("Hi! The nearest pizza place, Archer Avenue Diner, is about")


def test_an_address_shaped_named_match_or_pinned_place_holds():
    p = _plan("How far is Archer?", _with("45 Archer Ave", km=3.0, route_km=3.5, cuisine=()))
    assert p.verdict == "hold" and p.reason == "place_name", p
    body = {"place": "12 Main St", "distance_m": 9000.0, "duration_s": 900.0}
    ov = dict(body, confirmed_sha256=sha256(json.dumps(body, sort_keys=True)))
    p = _plan("How far is Sakura Garden?", overrides={G.override_key("cedar-1a", "sakura garden"): ov})
    assert p.verdict == "hold" and p.reason == "place_name" and p.text is None


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


# ---------- nearest of a type: a complete radius list, never a search list (Nov 1, 2026) ----------

def _grocery_world(extra=(), routes=None):
    """Two staged supermarkets plus `extra` places (pid, name, km, bearing, route_km, kinds)."""
    places = {k: v for k, v in GF.PLACES.items() if "shop:supermarket" not in v.kinds}
    rts = {k: v for k, v in GF.ROUTES.items() if k in places}
    for pid, name, km, bearing, route_km, kinds in extra:
        lat, lon = GF._offset(GF.ORIGIN, km, bearing)
        places[pid] = G.Place(name, lat, lon, kinds, 0.1, (), pid)
        rts[pid] = G.Route(route_km * 1000.0, route_km * 90.0)
    rts.update(routes or {})
    return places, rts


SUPER = ("shop:supermarket",)


class SearchMisses(GF.FixtureProvider):
    """A geocoder that leaves the closest places out of its list (what
    Nominatim did from the real building): `search` never returns `hidden`."""
    def __init__(self, hidden, *a, **k):
        super().__init__(*a, **k)
        self.hidden = set(hidden)

    def search(self, query, near, radius_km, limit=10):
        return [p for p in super().search(query, near, radius_km, limit) if p.osm_id not in self.hidden]


def test_the_nearest_comes_from_the_radius_list_not_the_search_list():
    places, routes = _grocery_world([("near-1", "Corner Market", 2.3, 10, 2.9, SUPER),
                                     ("near-2", "Prairie Foods", 2.4, 200, 3.1, SUPER),
                                     ("far-1", "Big Box Grocery", 16.0, 90, 17.5, SUPER)])
    prov = SearchMisses({"near-1", "near-2"}, places, routes)
    assert {p.osm_id for p in prov.search("supermarket", GF.ORIGIN, 25.0, 20)} == {"far-1"}  # the old failure
    prov.sent.clear()
    p = _plan("Where's the closest grocery store?", prov)
    assert p.verdict == "answer" and p.place["osm_id"] == "near-1", p
    assert p.text.startswith("Hi! The nearest grocery store, Corner Market, is about 2 miles away")
    assert p.text.endswith(" by car without traffic.")
    assert not [s for s in prov.sent if s[0] == "search"]  # a type question never searches


def test_a_provider_without_a_radius_list_holds():
    class SearchOnly:
        coverage_verified = True
        sent = []

        def search(self, *a, **k):
            return list(GF.PLACES.values())

        def route(self, origin, dest):
            return GF.ROUTES.get(dest.osm_id)
    p = _plan("Where's the nearest pharmacy?", SearchOnly())
    assert p.verdict == "hold" and p.reason == "no_poi_source"
    assert _plan("How far is Sakura Garden?", SearchOnly()).verdict == "answer"  # named places as before


def test_an_incomplete_radius_list_holds():
    class Partial(GF.FixtureProvider):
        def nearby(self, *a, **k):
            full = super().nearby(*a, **k)
            return G.Nearby(full.places, False, "partial")
    p = _plan("Where's the nearest pharmacy?", Partial())
    assert p.verdict == "hold" and p.reason == "incomplete"


def test_the_shortest_drive_wins_over_the_shortest_straight_line():
    # across a river: 1.0 km away in a straight line, 3.5 km by road; the other 2.0 km and 2.6 km
    places, routes = _grocery_world([("across", "River Market", 1.0, 0, 3.5, SUPER),
                                     ("bridge", "Bridge Street Foods", 2.0, 180, 2.6, SUPER)])
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes))
    assert p.verdict == "answer" and p.place["osm_id"] == "bridge", p


def test_a_candidate_that_could_be_nearer_without_a_route_holds():
    places, routes = _grocery_world([("noroute", "Lost Market", 1.0, 0, 1.3, SUPER),
                                     ("ok", "Found Market", 2.0, 180, 2.6, SUPER)])
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes, fail_routes={"noroute"}))
    assert p.verdict == "hold" and p.reason == "no_route"
    # a place without a route that is farther in a straight line than the best drive does not matter
    places, routes = _grocery_world([("noroute", "Lost Market", 3.5, 0, 4.0, SUPER),
                                     ("ok", "Found Market", 2.0, 180, 2.6, SUPER)])
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes, fail_routes={"noroute"}))
    assert p.verdict == "answer" and p.place["osm_id"] == "ok"


def test_more_candidates_than_routed_that_could_be_nearer_hold():
    # nine stores between 1.0 and 1.8 km in a straight line, every drive 3.5 km: the ninth, never routed,
    # could be the nearest by road
    extra = [(f"s{i}", f"Store {chr(65 + i)}", 1.0 + 0.1 * i, 40 * i, 3.5, SUPER) for i in range(9)]
    places, routes = _grocery_world(extra)
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes))
    assert p.verdict == "hold" and p.reason == "unverified_nearest"


def test_the_radius_widens_until_it_covers_the_drive():
    # nothing within 5 km: the list widens and finds the one at 9 km
    places, routes = _grocery_world([("far", "Valley Foods", 9.0, 45, 10.5, SUPER)])
    prov = GF.FixtureProvider(places, routes)
    p = _plan("Is there a grocery store nearby?", prov)
    assert p.verdict == "answer" and p.place["osm_id"] == "far"
    radii = [s[2] for s in prov.sent if s[0] == "nearby"]
    assert radii[0] == G.NEARBY_START_KM and radii[-1] >= 10.5 and radii == sorted(radii)
    # 4 km away in a straight line but a 9 km drive, with a 6 km / 6.5 km store just outside the first list
    places, routes = _grocery_world([("loop", "Loop Market", 4.0, 0, 9.0, SUPER),
                                     ("edge", "Edge Market", 6.0, 180, 6.5, SUPER)])
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes))
    assert p.verdict == "answer" and p.place["osm_id"] == "edge", p
    # nothing within the type's whole radius: hold, never "there is none"
    places, routes = _grocery_world()
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes))
    assert p.verdict == "hold" and p.reason == "not_found" and p.text is None


def test_a_nameless_nearest_place_holds():
    places, routes = _grocery_world([("anon", "", 1.0, 0, 1.3, SUPER), ("named", "Named Market", 2.0, 180, 2.6, SUPER)])
    p = _plan("Is there a grocery store nearby?", GF.FixtureProvider(places, routes))
    assert p.verdict == "hold" and p.reason == "place_name"


class RealMap(GF.FixtureProvider):
    """The fixture map behaving like the real one: not ground truth."""
    coverage_verified = False


def test_the_real_map_needs_a_coverage_record_naming_the_checked_place():
    prov = RealMap()
    p = _plan("Where's the nearest pharmacy?", prov)
    assert p.verdict == "hold" and p.reason == "unverified_type" and prov.sent == []  # no lookup at all
    rec = G.coverage_record("pharmacy", "p-pharm", "Cedar Pharmacy", "checked on Google Maps", "2026-11-01T07:00:00+00:00")
    key = G.coverage_key("cedar-1a", "pharmacy")
    p = _plan("Where's the nearest pharmacy?", RealMap(), overrides={key: rec})
    assert p.verdict == "answer" and p.place["osm_id"] == "p-pharm" and p.text.endswith(" without traffic.")
    # the map's winner is not the place that was checked: hold
    other = G.coverage_record("pharmacy", "p-pharm2", "Millbrook Drug Mart", "x", "2026-11-01T07:00:00+00:00")
    assert _plan("Where's the nearest pharmacy?", RealMap(), overrides={key: other}).reason == "coverage_changed"
    # a tampered record, or one for another type, holds
    assert _plan("Where's the nearest pharmacy?", RealMap(), overrides={key: dict(rec, nearest="p-pharm2")}).reason \
        == "unverified_type"
    assert _plan("Where's the nearest pharmacy?", RealMap(),
                 overrides={key: G.coverage_record("grocery", "p-pharm", "Cedar Pharmacy", "x", "t")}).reason \
        == "unverified_type"
    # a record for one listing does not cover another
    assert G.plan("Where's the nearest pharmacy?", "cedar-1b", GF.ORIGIN, RealMap(), reservation=OK, scope=SCOPE,
                  overrides={key: rec}).reason == "unverified_type"
    # named places need no coverage record
    assert _plan("How far is Sakura Garden?", RealMap()).verdict == "answer"


def test_a_pinned_nearest_of_type_answer_keeps_the_type_wording_and_makes_no_lookup():
    body = {"place": "Cedar Pharmacy", "distance_m": 2400.0, "duration_s": 300.0}
    ov = dict(body, confirmed_sha256=sha256(json.dumps(body, sort_keys=True)), note="Google Maps, Sat 10 AM")
    key = G.type_override_key("cedar-1a", "pharmacy")
    for msg in ("Where's the nearest pharmacy?", "Is there a drug store nearby?", "closest pharmacy?"):
        prov = RealMap()
        p = _plan(msg, prov, overrides={key: ov})
        assert p.verdict == "answer" and p.override and prov.sent == [], (msg, p)
        assert p.text == "Hi! The nearest pharmacy, Cedar Pharmacy, is about 1.5 miles away, roughly 5 minutes by car."
    assert _plan("Where's the nearest pharmacy?", overrides={key: dict(ov, duration_s=60.0)}).reason \
        == "override_tampered"
    addr = {"place": "45 Archer Ave", "distance_m": 2400.0, "duration_s": 300.0}
    bad = dict(addr, confirmed_sha256=sha256(json.dumps(addr, sort_keys=True)))
    p = _plan("Where's the nearest pharmacy?", overrides={key: bad})
    assert p.verdict == "hold" and p.reason == "place_name" and p.text is None
    # the three key kinds never collide
    assert len({G.override_key("cedar-1a", "nearest pharmacy"), G.type_override_key("cedar-1a", "pharmacy"),
                G.coverage_key("cedar-1a", "pharmacy")}) == 3


class OverpassHTTP:
    """A fake Overpass and OSRM table: two supermarkets (a node and a way
    with a center) and a route table."""
    def __init__(self, remark=None):
        self.urls, self.remark = [], remark

    def __call__(self, url):
        self.urls.append(url)
        if "/interpreter?" in url:
            doc = {"osm3s": {"timestamp_osm_base": "2026-11-01T00:00:00Z"}, "elements": [
                {"type": "node", "id": 11, "lat": 41.0101, "lon": -86.9902,
                 "tags": {"shop": "supermarket", "name": "Corner Market"}},
                {"type": "way", "id": 22, "center": {"lat": 40.9871, "lon": -87.0123},
                 "tags": {"shop": "supermarket", "name": "Prairie Foods"}}]}
            if self.remark:
                doc["remark"] = self.remark
            return doc
        if "/table/v1/driving/" in url:
            return {"code": "Ok", "distances": [[0, 1900.0, 2600.0]], "durations": [[0, 240.0, 330.0]]}
        raise AssertionError(url)


def test_osm_radius_list_and_route_table():
    with tempfile.TemporaryDirectory() as tmp:
        http = OverpassHTTP()
        prov = G.OSMProvider(os.path.join(tmp, "geo-cache.json"), http_get=http, clock=Clock(), sleep=lambda s: None,
                             rate_path=os.path.join(tmp, "osm-rate.json"))
        origin = (41.002345, -87.001234)
        found = prov.nearby(("shop:supermarket", "shop:grocery"), origin, 5.0)
        assert found.complete and [p.osm_id for p in found.places] == ["node/11", "way/22"]
        assert found.places[1].lat == 40.9871 and found.places[0].kinds == ("shop:supermarket",)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(http.urls[0]).query)["data"][0]
        assert q.startswith("[out:json]") and q.endswith("out center tags;")
        assert 'nwr["shop"="grocery"](around:5250,41.002,-87.001);' in q and '"shop"="supermarket"' in q
        assert "41.002345" not in http.urls[0]
        routes = prov.routes(origin, found.places)
        assert routes == [G.Route(1900.0, 240.0), G.Route(2600.0, 330.0)]
        assert "/table/v1/driving/-87.001,41.002;" in http.urls[1] and "sources=0" in http.urls[1]
        # cached: no second request
        prov.nearby(("shop:supermarket", "shop:grocery"), origin, 5.0)
        prov.routes(origin, found.places)
        assert len(http.urls) == 2
        assert [s[0] for s in prov.sent] == ["nearby", "table"] and prov.sent[0][1] == "shop:grocery,shop:supermarket"
        # an Overpass remark (a server timeout) is an incomplete list: an error, never cached
        bad = OverpassHTTP(remark="runtime error: Query timed out in \"query\" at line 1 after 26 seconds.")
        prov2 = G.OSMProvider(os.path.join(tmp, "other-cache.json"), http_get=bad, clock=Clock(), sleep=lambda s: None,
                              rate_path=os.path.join(tmp, "osm-rate.json"), retries=0)
        try:
            prov2.nearby(("shop:supermarket",), origin, 5.0)
            raise AssertionError("no error")
        except G.ProviderError as e:
            assert "incomplete" in str(e)
        assert not os.path.exists(os.path.join(tmp, "other-cache.json"))
        try:
            prov.nearby(('shop"];out;', ), origin, 5.0)  # never anything but an OSM key:value in the query
            raise AssertionError("no error")
        except G.ProviderError:
            pass
        assert G.OSMProvider.coverage_verified is False


def test_the_end_to_end_type_plan_runs_on_the_osm_client():
    with tempfile.TemporaryDirectory() as tmp:
        http = OverpassHTTP()
        prov = G.OSMProvider(os.path.join(tmp, "geo-cache.json"), http_get=http, clock=Clock(), sleep=lambda s: None,
                             rate_path=os.path.join(tmp, "osm-rate.json"))
        origin = (41.0, -87.0)
        assert G.plan("Is there a grocery store nearby?", "cedar-1a", origin, prov, reservation=OK,
                      scope=SCOPE).reason == "unverified_type"
        assert http.urls == []
        rec = G.coverage_record("grocery", "node/11", "Corner Market", "checked", "2026-11-01T07:00:00+00:00")
        p = G.plan("Is there a grocery store nearby?", "cedar-1a", origin, prov, reservation=OK, scope=SCOPE,
                   overrides={G.coverage_key("cedar-1a", "grocery"): rec})
        assert p.verdict == "answer" and p.text == \
            "Hi! The nearest grocery store, Corner Market, is about 1 mile away, roughly 4 minutes by car without traffic."
        assert [s[0] for s in p.sent] == ["nearby", "table"]
