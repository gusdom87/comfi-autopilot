"""Staged map data for geo_answers.py (built Oct 31, 2026).

Synthetic coordinates and invented places only. The origin is not any
listing's location; no place here is real. Every place carries a fixed
route (driving distance and minutes) so the tests run fully offline and
give the same numbers every time. FixtureProvider answers searches the
way a geocoder does: it returns every place that shares a significant word
with the query (decoys and partial matches included), so the engine's own
plausibility and ambiguity rules are what decide.

Built in on purpose: two same-name parks within 15% of each other
(ambiguous, must hold), two same-name parks far apart (the nearer
answers), a well-known far square sharing its name with a minor near one
(prominence conflict, must hold), a decoy whose name contains a landmark's
full name, a place past the radius, a place a short walk away, and places
of barred types (a hospital, a fuel station).
"""
import math
from dataclasses import dataclass

from .geo_answers import Nearby, Place, Route, haversine_m

# Synthetic origin: the four Cedar Hollow single units share one building.
ORIGIN = (41.0, -87.0)
LISTING_COORDS = {pid: ORIGIN for pid in ("cedar-1a", "cedar-1b", "cedar-2a", "cedar-2b")}
LISTING_COORDS["cedar-retreat"] = ORIGIN


def _offset(origin, km, bearing_deg):
    """The point `km` from origin along `bearing_deg` (spherical earth)."""
    r = 6371.0088
    lat1, lon1, b = math.radians(origin[0]), math.radians(origin[1]), math.radians(bearing_deg)
    d = km / r
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(b))
    lon2 = lon1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return round(math.degrees(lat2), 6), round(math.degrees(lon2), 6)


@dataclass(frozen=True)
class _Spec:
    pid: str
    name: str
    kinds: tuple
    cuisine: tuple
    importance: float
    km: float          # straight-line distance from the origin
    bearing: float
    route_km: float    # driving distance
    route_min: float   # driving minutes


_SPECS = [
    _Spec("p-market", "Hollow Creek Market", ("shop:supermarket",), (), 0.2, 2.8, 40, 3.4, 6),
    _Spec("p-freshway", "FreshWay Grocery", ("shop:supermarket",), (), 0.2, 5.5, 200, 6.4, 10),
    _Spec("p-pharm", "Cedar Pharmacy", ("amenity:pharmacy",), (), 0.15, 1.9, 120, 2.3, 5),
    _Spec("p-pharm2", "Millbrook Drug Mart", ("amenity:pharmacy",), (), 0.15, 7.0, 300, 8.2, 12),
    _Spec("p-station", "Millbrook Station", ("railway:station", "public_transport:station"), (), 0.3, 4.4, 330,
          5.2, 9),
    _Spec("p-terminal", "Union Terminal", ("railway:station",), (), 0.7, 58.0, 10, 66.0, 62),
    _Spec("p-sakura", "Sakura Garden", ("amenity:restaurant",), ("japanese",), 0.1, 7.6, 75, 8.9, 14),
    _Spec("p-tokyo", "Tokyo Grill", ("amenity:restaurant",), ("japanese", "sushi"), 0.1, 13.0, 160, 14.6, 19),
    _Spec("p-rosa", "Rosa's Taqueria", ("amenity:restaurant",), ("mexican",), 0.1, 0.25, 260, 0.35, 1),
    _Spec("p-pizza", "Cedar Slice Pizza", ("amenity:restaurant",), ("pizza",), 0.1, 1.6, 90, 2.0, 5),
    _Spec("p-lakeview-a", "Lakeview Park", ("leisure:park",), (), 0.25, 9.0, 20, 10.0, 13),
    _Spec("p-lakeview-b", "Lakeview Park", ("leisure:park",), (), 0.25, 9.8, 140, 11.5, 16),
    _Spec("p-riverside-a", "Riverside Park", ("leisure:park",), (), 0.2, 1.5, 230, 1.9, 4),
    _Spec("p-riverside-b", "Riverside Park", ("leisure:park",), (), 0.2, 19.0, 50, 22.0, 25),
    _Spec("p-pier", "Grand Harbor Pier", ("tourism:attraction",), (), 0.65, 60.0, 15, 68.0, 58),
    _Spec("p-pier-dental", "Grand Harbor Pier Dental", ("amenity:dentist",), (), 0.05, 3.0, 100, 3.6, 7),
    _Spec("p-summit", "Summit Peak Resort", ("tourism:attraction",), (), 0.5, 230.0, 270, 260.0, 170),
    _Spec("p-amph", "Prairie Amphitheater", ("amenity:theatre", "tourism:attraction"), (), 0.4, 30.0, 300, 35.5,
          31),
    _Spec("p-oak", "Oak Street Coffee", ("amenity:cafe",), (), 0.1, 1.1, 10, 1.3, 3),
    _Spec("p-cinema", "Starlight Cinema", ("amenity:cinema",), (), 0.2, 5.0, 180, 5.8, 9),
    _Spec("p-library", "Cedar Hollow Library", ("amenity:library",), (), 0.2, 0.55, 315, 0.7, 2),
    _Spec("p-lincoln-local", "Lincoln Square", ("place:square",), (), 0.15, 1.4, 60, 1.7, 4),
    _Spec("p-lincoln-famous", "Lincoln Square", ("place:neighbourhood",), (), 0.55, 55.0, 5, 63.0, 60),
    _Spec("p-hospital", "Cedar General Hospital", ("amenity:hospital",), (), 0.3, 6.0, 250, 7.1, 11),
    _Spec("p-fuel", "QuickFuel", ("amenity:fuel",), (), 0.1, 1.2, 170, 1.5, 3),
    _Spec("p-zoo", "Hollow Ridge Zoo", ("tourism:zoo",), (), 0.5, 38.0, 340, 45.0, 40),
    _Spec("p-hardware", "Hollow Hardware", ("shop:hardware",), (), 0.1, 2.0, 280, 2.4, 5),
    _Spec("p-riverside-cafe", "Riverside Cafe", ("amenity:cafe",), (), 0.1, 2.6, 235, 3.1, 6),
]

PLACES = {}
ROUTES = {}
for _s in _SPECS:
    lat, lon = _offset(ORIGIN, _s.km, _s.bearing)
    PLACES[_s.pid] = Place(_s.name, lat, lon, _s.kinds, _s.importance, _s.cuisine, _s.pid)
    ROUTES[_s.pid] = Route(round(_s.route_km * 1000.0, 1), round(_s.route_min * 60.0, 1))

_STOP = {"the", "a", "an", "of", "at", "and", "&", "near", "nearest", "closest"}


def _tokens(text):
    return {t for t in "".join(c.lower() if c.isalnum() or c in " '" else " " for c in text).split()
            if t not in _STOP}


class FixtureProvider:
    """Offline stand-in for the map services, same interface as OSMProvider.
    Records every query it is sent (the privacy tests read `sent`).

    `search` answers like a geocoder (decoys, importance order, a result
    cap); `nearby` answers like a radius list: every place of the kinds
    asked for within the radius, nothing cut off. This map is an invented
    world, complete by construction, so `coverage_verified` is True: a
    nearest-of-type answer here needs no operator coverage record (the
    real map's provider says False)."""
    name = "fixture"
    coverage_verified = True

    def __init__(self, places=None, routes=None, fail_routes=()):
        self.places = dict(PLACES if places is None else places)
        self.routes = dict(ROUTES if routes is None else routes)
        self.fail_routes = set(fail_routes)
        self.sent = []

    def search(self, query, near, radius_km, limit=10):
        self.sent.append(("search", query, tuple(near), radius_km))
        q = _tokens(query)
        hits = []
        for p in self.places.values():
            words = _tokens(p.name) | {k.split(":", 1)[1].replace("_", " ") for k in p.kinds} | set(p.cuisine)
            if q & words and haversine_m(near, (p.lat, p.lon)) <= radius_km * 1000.0:
                hits.append(p)
        # a geocoder's own order: by its importance, not by distance
        hits.sort(key=lambda p: (-p.importance, p.osm_id))
        return hits[:limit]

    def nearby(self, kinds, near, radius_km, cuisine=()):
        self.sent.append(("nearby", ",".join(sorted(set(kinds))), float(radius_km)))
        want = set(kinds)
        hits = [p for p in self.places.values()
                if set(p.kinds) & want and haversine_m(near, (p.lat, p.lon)) <= radius_km * 1000.0]
        return Nearby(tuple(sorted(hits, key=lambda p: p.osm_id)), True, "fixture")  # any order: the engine sorts

    def routes(self, origin, dests):
        return [self.route(origin, d) for d in dests]

    def route(self, origin, dest):
        self.sent.append(("route", tuple(origin), (dest.lat, dest.lon)))
        if dest.osm_id in self.fail_routes:
            return None
        return self.routes.get(dest.osm_id)
