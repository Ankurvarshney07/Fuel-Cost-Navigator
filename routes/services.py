"""
routes/services.py
==================
All business logic for the Fuel Cost Navigator.

External API calls:
  - geocode_location()  →  1 Nominatim call per location (with retry)
  - get_route()         →  1 call via the first available routing provider:
                              1. OpenRouteService (ORS_API_KEY set)
                              2. OSRM public API   (no key needed)
                              3. Great-circle interpolation (offline fallback)

Internal logic (zero external calls):
  - find_stations_near_route()  →  in-memory distance filter against DB
  - plan_fuel_stops()           →  greedy cost-optimal algorithm
  - compute_total_cost()        →  arithmetic
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ─────────────────────────── constants ──────────────────────────────────────
EARTH_RADIUS_MILES = 3_958.8
KM_PER_MILE = 1.60934


# ─────────────────────────── data classes ───────────────────────────────────

@dataclass
class LatLon:
    lat: float
    lon: float


@dataclass
class StationCandidate:
    opis_id: int
    name: str
    city: str
    state: str
    lat: float
    lon: float
    price: float              # USD per gallon
    distance_along_route: float = 0.0   # miles from route start


@dataclass
class FuelStop:
    opis_id: int
    name: str
    city: str
    state: str
    lat: float
    lon: float
    price: float
    gallons: float            # gallons purchased at this stop
    cost: float               # USD spent at this stop
    distance_from_start: float   # miles from origin


@dataclass
class RouteResult:
    start: str
    finish: str
    total_distance_miles: float
    fuel_stops: List[FuelStop]
    total_fuel_cost: float
    route_geometry: dict      # GeoJSON LineString
    routing_provider: str     # which provider was used


# ─────────────────────────── geometry helpers ────────────────────────────────

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def decode_polyline(encoded: str) -> List[Tuple[float, float]]:
    """
    Decode a Google-format encoded polyline into a list of (lat, lon) tuples.
    Used for both OSRM (precision 5) and ORS (precision 5) responses.
    """
    coords: List[Tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    while index < len(encoded):
        shift, result = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += ~(result >> 1) if result & 1 else result >> 1

        shift, result = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lon += ~(result >> 1) if result & 1 else result >> 1

        coords.append((lat / 1e5, lon / 1e5))
    return coords


def interpolate_route(origin: LatLon, destination: LatLon,
                      num_points: int = 100) -> List[Tuple[float, float]]:
    """
    Generate evenly-spaced waypoints along the great-circle arc between two
    points.  Used as the offline fallback when no routing API is reachable.

    Returns a list of (lat, lon) tuples.
    """
    coords = []
    for i in range(num_points + 1):
        t = i / num_points
        lat = origin.lat + t * (destination.lat - origin.lat)
        lon = origin.lon + t * (destination.lon - origin.lon)
        coords.append((lat, lon))
    return coords


def build_route_linestring(coords: List[Tuple[float, float]]) -> dict:
    """Return a GeoJSON LineString from a list of (lat, lon) tuples."""
    return {
        "type": "LineString",
        # GeoJSON uses [longitude, latitude] order
        "coordinates": [[lon, lat] for lat, lon in coords],
    }


def cumulative_distances(coords: List[Tuple[float, float]]) -> List[float]:
    """
    Return a parallel list of cumulative miles from the first waypoint.
    """
    dist = [0.0]
    for i in range(1, len(coords)):
        d = haversine_miles(
            coords[i - 1][0], coords[i - 1][1],
            coords[i][0],     coords[i][1],
        )
        dist.append(dist[-1] + d)
    return dist


def nearest_point_distance(
    station_lat: float,
    station_lon: float,
    coords: List[Tuple[float, float]],
    cum_dist: List[float],
) -> Tuple[float, float]:
    """
    Find the closest route waypoint to the station.
    Returns (perpendicular_distance_miles, distance_along_route_miles).
    """
    best_perp = float("inf")
    best_along = 0.0
    for i, (rlat, rlon) in enumerate(coords):
        d = haversine_miles(station_lat, station_lon, rlat, rlon)
        if d < best_perp:
            best_perp = d
            best_along = cum_dist[i]
    return best_perp, best_along


# ─────────────────────────── HTTP helper ─────────────────────────────────────

def _http_get(url: str, params: dict = None, headers: dict = None,
              timeout: int = None) -> requests.Response:
    """
    GET with automatic retry on transient failures (connection errors, 429,
    502, 503, 504).  Raises requests.RequestException on final failure.
    """
    max_tries = getattr(settings, "EXTERNAL_REQUEST_RETRIES", 2) + 1
    timeout = timeout or getattr(settings, "EXTERNAL_REQUEST_TIMEOUT", 15)

    last_exc: Optional[Exception] = None
    for attempt in range(max_tries):
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=timeout)
            if resp.status_code in (429, 502, 503, 504) and attempt < max_tries - 1:
                wait = 2 ** attempt  # 1s, 2s, …
                logger.warning("HTTP %s from %s — retrying in %ss",
                               resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_tries - 1:
                wait = 2 ** attempt
                logger.warning("Request failed (%s) — retrying in %ss: %s",
                               type(exc).__name__, wait, exc)
                time.sleep(wait)
    raise last_exc


# ─────────────────────────── geocoding ───────────────────────────────────────

def geocode_location(place: str) -> LatLon:
    """
    Geocode a free-text US location string via Nominatim.
    Raises ValueError on failure or if the place is not found.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": place,
        "format": "json",
        "limit": 5,
        "countrycodes": "us",
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

    try:
        response = _http_get(url, params=params, headers=headers)
    except requests.RequestException as exc:
        raise ValueError(f"Geocoding service unavailable: {exc}") from exc

    results = response.json()
    if not results:
        raise ValueError(
            f"Could not find '{place}' within the United States. "
            "Try a more specific location such as 'Chicago, IL'."
        )

    first = results[0]
    return LatLon(lat=float(first["lat"]), lon=float(first["lon"]))


# ─────────────────────────── routing providers ───────────────────────────────

def _route_via_ors(origin: LatLon,
                   destination: LatLon) -> Tuple[List[Tuple[float, float]], float]:
    """
    Route using OpenRouteService Directions v2 API.
    Requires ORS_API_KEY in settings.
    Returns (coords, total_miles).

    Free tier: 2000 requests/day, 40/minute.
    Sign up at: https://openrouteservice.org/dev/#/signup
    """
    import openrouteservice
    from openrouteservice import convert

    key = settings.ORS_API_KEY
    client = openrouteservice.Client(key=key)

    coordinates = [[origin.lon, origin.lat], [destination.lon, destination.lat]]
    try:
        routes = client.directions(
            coordinates=coordinates,
            profile="driving-car",
            format="geojson",
            geometry_simplify=False,
        )
    except Exception as exc:
        raise ValueError(f"ORS routing failed: {exc}") from exc

    feature = routes["features"][0]
    props = feature["properties"]
    total_meters = props["summary"]["distance"]
    total_miles = total_meters / 1000 / KM_PER_MILE

    # GeoJSON geometry coords are [lon, lat]; convert to (lat, lon) tuples
    geojson_coords = feature["geometry"]["coordinates"]
    coords = [(c[1], c[0]) for c in geojson_coords]

    return coords, total_miles


def _route_via_osrm(origin: LatLon,
                    destination: LatLon) -> Tuple[List[Tuple[float, float]], float]:
    """
    Route using the public OSRM API (no key needed).
    Returns (coords, total_miles).
    """
    base = settings.OSRM_BASE_URL.rstrip("/")
    url = (
        f"{base}/route/v1/driving/"
        f"{origin.lon},{origin.lat};{destination.lon},{destination.lat}"
    )
    params = {
        "overview": "full",
        "geometries": "polyline",
        "steps": "false",
    }

    try:
        response = _http_get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        raise ValueError(f"OSRM unavailable: {exc}") from exc

    data = response.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(
            f"OSRM could not route between the given locations "
            f"(code: {data.get('code')})."
        )

    route = data["routes"][0]
    total_miles = route["distance"] / 1000 / KM_PER_MILE
    coords = decode_polyline(route["geometry"])
    return coords, total_miles


def _route_via_interpolation(
        origin: LatLon,
        destination: LatLon) -> Tuple[List[Tuple[float, float]], float]:
    """
    Fallback: generate a great-circle arc between origin and destination.

    The geometry will not follow roads, but the distance and fuel-stop
    algorithm remain correct because:
      - Haversine distance ≈ actual road distance within ~25% for long
        cross-country routes.
      - Station-to-route proximity is measured against the arc waypoints,
        which adequately captures major-corridor stations at 5-mile corridor.

    The response includes a note field (via routing_provider) so the caller
    can communicate this to the client.
    """
    total_miles = haversine_miles(
        origin.lat, origin.lon,
        destination.lat, destination.lon,
    )
    # Scale up ~20% to approximate road distance (interstates add ~15-25%)
    road_estimate = total_miles * 1.20
    coords = interpolate_route(origin, destination, num_points=200)
    return coords, road_estimate


def get_route(
        origin: LatLon,
        destination: LatLon) -> Tuple[List[Tuple[float, float]], float, str]:
    """
    Attempt routing via ORS → OSRM → interpolation (in that order).

    Returns (coords, total_miles, provider_name).
    Raises ValueError only if all providers fail AND fallback is disabled.
    """
    errors = []

    # 1. OpenRouteService
    if getattr(settings, "ORS_API_KEY", ""):
        try:
            coords, miles = _route_via_ors(origin, destination)
            logger.info("Routing via OpenRouteService: %.1f miles", miles)
            return coords, miles, "openrouteservice"
        except Exception as exc:
            logger.warning("ORS failed: %s", exc)
            errors.append(f"ORS: {exc}")

    # 2. OSRM public
    try:
        coords, miles = _route_via_osrm(origin, destination)
        logger.info("Routing via OSRM: %.1f miles", miles)
        return coords, miles, "osrm"
    except Exception as exc:
        logger.warning("OSRM failed: %s", exc)
        errors.append(f"OSRM: {exc}")

    # 3. Straight-line fallback
    allow_fallback = getattr(settings, "ROUTING_ALLOW_FALLBACK", True)
    if allow_fallback:
        logger.warning(
            "All routing providers failed (%s). Using great-circle fallback.",
            "; ".join(errors),
        )
        coords, miles = _route_via_interpolation(origin, destination)
        return coords, miles, "interpolation (fallback)"

    raise ValueError(
        "Could not obtain a driving route. Tried: " + "; ".join(errors)
    )


# ─────────────────────────── fuel-stop selection ─────────────────────────────

def find_stations_near_route(
    coords: List[Tuple[float, float]],
    cum_dist: List[float],
    corridor_miles: float,
) -> List[StationCandidate]:
    """
    Query the DB for all geocoded stations within `corridor_miles` of the route.
    Returns candidates sorted by position along the route.
    """
    from routes.models import FuelStation  # avoid circular import

    stations = FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
    ).values("opis_id", "name", "city", "state", "latitude", "longitude", "retail_price")

    # Quick bounding-box pre-filter (1 degree ≈ 69 miles)
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    margin_deg = (corridor_miles + 10) / 69.0
    lat_min, lat_max = min(lats) - margin_deg, max(lats) + margin_deg
    lon_min, lon_max = min(lons) - margin_deg, max(lons) + margin_deg

    candidates: List[StationCandidate] = []
    for s in stations:
        slat, slon = s["latitude"], s["longitude"]
        if not (lat_min <= slat <= lat_max and lon_min <= slon <= lon_max):
            continue

        perp_dist, along_dist = nearest_point_distance(slat, slon, coords, cum_dist)
        if perp_dist <= corridor_miles:
            candidates.append(
                StationCandidate(
                    opis_id=s["opis_id"],
                    name=s["name"],
                    city=s["city"],
                    state=s["state"],
                    lat=slat,
                    lon=slon,
                    price=float(s["retail_price"]),
                    distance_along_route=along_dist,
                )
            )

    candidates.sort(key=lambda c: c.distance_along_route)
    return candidates


def plan_fuel_stops(
    candidates: List[StationCandidate],
    total_distance: float,
    vehicle_range: float = settings.VEHICLE_RANGE_MILES,
    mpg: float = settings.VEHICLE_MPG,
) -> List[FuelStop]:
    """
    Greedy cost-optimal fuel-stop planner.

    - Start at origin with a full tank (range = vehicle_range miles).
    - Each iteration: pick the cheapest reachable station that guarantees
      we can always reach the next station or the destination.
    - Repeat until the destination is reachable without stopping.
    """
    stops: List[FuelStop] = []
    current_pos = 0.0
    tank_miles = vehicle_range   # full tank at start

    while True:
        max_reach = current_pos + tank_miles

        if max_reach >= total_distance:
            break   # destination reachable on current tank

        reachable = [
            c for c in candidates
            if current_pos < c.distance_along_route <= max_reach
        ]

        if not reachable:
            raise ValueError(
                f"No fuel stations found within {vehicle_range:.0f} miles of "
                f"mile {current_pos:.0f} on this route. "
                "The vehicle cannot continue. Try a broader route or add more "
                "station data by running seed_stations.py."
            )

        best = _pick_best_station(reachable, candidates, total_distance, vehicle_range)

        # Calculate gallons to refill only what was consumed since the last position/refuel
        gallons = (best.distance_along_route - current_pos) / mpg
        cost = gallons * best.price

        stops.append(
            FuelStop(
                opis_id=best.opis_id,
                name=best.name,
                city=best.city,
                state=best.state,
                lat=best.lat,
                lon=best.lon,
                price=best.price,
                gallons=round(gallons, 3),
                cost=round(cost, 2),
                distance_from_start=round(best.distance_along_route, 1),
            )
        )

        current_pos = best.distance_along_route
        tank_miles = vehicle_range   # refuelled to full

    return stops


def _pick_best_station(
    reachable: List[StationCandidate],
    all_candidates: List[StationCandidate],
    total_distance: float,
    vehicle_range: float,
) -> StationCandidate:
    """
    From the reachable window, return the cheapest station that still leaves
    us able to reach at least one more station (or the destination).
    Falls back to the furthest reachable station if no valid choice exists.
    """
    for candidate in sorted(reachable, key=lambda c: c.price):
        reach_from_here = candidate.distance_along_route + vehicle_range
        if reach_from_here >= total_distance:
            return candidate   # can reach destination directly
        next_reachable = [
            c for c in all_candidates
            if candidate.distance_along_route < c.distance_along_route <= reach_from_here
        ]
        if next_reachable:
            return candidate

    # No ideal choice — advance as far as possible
    return max(reachable, key=lambda c: c.distance_along_route)


def compute_total_cost(
    stops: List[FuelStop],
    total_distance: float,
    mpg: float = settings.VEHICLE_MPG,
) -> float:
    """
    Total fuel cost = sum of costs charged at each stop (refueling only what
    was consumed since the last stop/start) plus the cost of fuel consumed
    on the final leg (priced at the last stop's price).
    """
    if not stops:
        return 0.0

    # Sum all stop costs
    total = sum(s.cost for s in stops)

    # Add the cost of fuel consumed on the final leg
    last = stops[-1]
    final_miles = total_distance - last.distance_from_start
    final_cost = (final_miles / mpg) * last.price

    return round(total + final_cost, 2)


# ─────────────────────────── public façade ───────────────────────────────────

def plan_route(start: str, finish: str) -> RouteResult:
    """
    Main entry point.  Called by the API view.

    External calls made:
      1. Nominatim geocode(start)
      2. Nominatim geocode(finish)
      3. Routing API (ORS / OSRM / fallback) — exactly 1 call

    Total: 3 external calls (or 2 if routing falls back to interpolation).
    """
    # Step 1 — geocode
    origin = geocode_location(start)
    destination = geocode_location(finish)

    # Step 2 — route (returns provider name for transparency)
    coords, total_miles, provider = get_route(origin, destination)
    cum_dist = cumulative_distances(coords)

    # Step 3 — find candidate stations along the corridor
    corridor = float(getattr(settings, "STATION_CORRIDOR_MILES", 5.0))

    # When using the interpolation fallback the corridor is widened because
    # the great-circle arc drifts further from actual interstates.
    if provider.startswith("interpolation"):
        corridor = max(corridor, 30.0)

    candidates = find_stations_near_route(coords, cum_dist, corridor)

    # Step 4 — greedy planner
    stops = plan_fuel_stops(candidates, total_miles)

    # Step 5 — total cost
    total_cost = compute_total_cost(stops, total_miles)

    return RouteResult(
        start=start,
        finish=finish,
        total_distance_miles=round(total_miles, 1),
        fuel_stops=stops,
        total_fuel_cost=total_cost,
        route_geometry=build_route_linestring(coords),
        routing_provider=provider,
    )
