"""
Integration test: verifies the full planning pipeline using mocked external
API calls. Uses a realistic dense polyline for the LA→NYC route.

Usage:
    python3 test_integration.py
"""
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuel_navigator.settings")
import django; django.setup()

from unittest.mock import patch
from routes.services import (
    plan_route, LatLon, cumulative_distances,
    find_stations_near_route, plan_fuel_stops, compute_total_cost,
)
from routes.models import FuelStation

# Dense mock route: LA → Phoenix → Albuquerque → Amarillo → OKC →
#   Little Rock → Memphis → Nashville → Knoxville → Roanoke →
#   Harrisburg → NYC
# This simulates the waypoints OSRM would return at ~50-mile intervals
MOCK_COORDS = [
    (34.0522, -118.2437),   # Los Angeles, CA
    (33.8117, -116.4542),   # Palm Springs area
    (33.4484, -112.0740),   # Phoenix, AZ
    (34.5810, -110.7805),   # Show Low, AZ
    (35.0844, -106.6504),   # Albuquerque, NM
    (35.2206, -104.5248),   # Santa Rosa, NM
    (35.2220, -101.8313),   # Amarillo, TX
    (35.4037, -99.4040),    # Elk City, OK
    (35.4676, -97.5164),    # Oklahoma City, OK
    (35.5065, -95.0000),    # Henryetta, OK
    (35.6450, -92.1900),    # Conway, AR
    (35.1462, -90.1849),    # Memphis, TN
    (35.5651, -88.3720),    # Holladay, TN
    (36.1653, -86.7844),    # Nashville, TN
    (35.9606, -83.9207),    # Knoxville, TN
    (37.2710, -80.0553),    # Roanoke, VA
    (38.1493, -79.0722),    # Staunton, VA
    (40.2732, -76.8867),    # Harrisburg, PA
    (40.7128, -74.0060),    # New York, NY
]

TOTAL_MILES = 2796.0


def mock_geocode_location(place: str) -> LatLon:
    if "Los Angeles" in place or "LA" in place:
        return LatLon(lat=34.0522, lon=-118.2437)
    return LatLon(lat=40.7128, lon=-74.0060)


def mock_get_route(origin: LatLon, destination: LatLon):
    return MOCK_COORDS, TOTAL_MILES, "mock"


def test_pipeline():
    print("\n=== Integration Test: LA → NYC Fuel Route ===\n")

    cum_dist = cumulative_distances(MOCK_COORDS)
    total_db = FuelStation.objects.count()
    geocoded_db = FuelStation.objects.filter(latitude__isnull=False).count()
    print(f"DB: {geocoded_db} geocoded stations (of {total_db} total)")

    candidates = find_stations_near_route(MOCK_COORDS, cum_dist, corridor_miles=30.0)
    print(f"Candidates within 30 miles of route: {len(candidates)}")
    for c in candidates:
        print(f"  {c.distance_along_route:6.0f}mi: {c.name}, {c.city} {c.state} @ ${c.price:.3f}")

    with patch("routes.services.geocode_location", side_effect=mock_geocode_location), \
         patch("routes.services.get_route", side_effect=mock_get_route):

        result = plan_route("Los Angeles, CA", "New York, NY")

    print(f"\n✓ Route: {result.start} → {result.finish}")
    print(f"  Total distance: {result.total_distance_miles:.1f} miles")
    print(f"  Fuel stops: {len(result.fuel_stops)}")
    for i, stop in enumerate(result.fuel_stops, 1):
        print(f"  [{i}] {stop.name}, {stop.city} {stop.state}")
        print(f"      ${stop.price:.3f}/gal | {stop.gallons} gal | ${stop.cost:.2f} | {stop.distance_from_start} mi")
    print(f"\n  TOTAL FUEL COST: ${result.total_fuel_cost:.2f}")
    print(f"  Route geometry: {len(result.route_geometry['coordinates'])} coordinates")

    # ── Assertions ─────────────────────────────────────────────────────────────
    assert result.total_distance_miles > 2000, "Route should be > 2000 miles"
    assert result.total_fuel_cost > 0, "Cost should be positive"

    positions = [0.0] + [s.distance_from_start for s in result.fuel_stops]
    for i in range(1, len(positions)):
        gap = positions[i] - positions[i - 1]
        assert gap <= 500, f"Gap {gap:.1f} mi > 500 mi vehicle range between stops {i-1} and {i}!"

    if result.fuel_stops:
        final_gap = result.total_distance_miles - result.fuel_stops[-1].distance_from_start
        assert final_gap <= 500, f"Final leg {final_gap:.1f} mi > vehicle range!"

    print("\n✓ All assertions passed!")
    return result


if __name__ == "__main__":
    result = test_pipeline()
