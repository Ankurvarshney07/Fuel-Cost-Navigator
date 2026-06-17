# Fuel Cost Navigator

A Django REST API that calculates optimal fuel stops along a US driving route, minimising total fuel cost given a 500-mile vehicle range and 10 mpg consumption.

## Stack

| Layer | Technology |
|---|---|
| Framework | Django 4.2 (latest stable LTS) + Django REST Framework |
| Database | SQLite (dev) — swap to PostgreSQL for production |
| Geocoding | Nominatim (free, OSM-backed, no API key needed) |
| Routing | OSRM Public API (free, no API key needed) |

## Project Structure

```
fuel_navigator/          ← Django project package
  settings.py            ← All configuration
  urls.py                ← Root URL routing

routes/                  ← Main app
  models.py              ← FuelStation model
  services.py            ← ALL business logic (geocoding, routing, greedy planner)
  serializers.py         ← DRF serializers (input validation + output formatting)
  views.py               ← Single APIView (POST /api/route/)
  urls.py                ← App URL routing
  management/
    commands/
      load_stations.py   ← Production data loader (uses Nominatim, respects 1 req/s)

seed_stations.py         ← Full seeder with checkpointing (run once)
quick_seed.py            ← Instant demo seeder (~50 key stations, hardcoded coords)
quick_seed_extended.py   ← Extended demo seeder (~88 stations, all major corridors)
test_integration.py      ← Integration test (runs offline, mocks external APIs)
requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
python manage.py migrate
```

## Data Loading

The repository comes with a pre-populated `db.sqlite3` database containing ~6,103 geocoded fuel stations. This allows you to run and test the API immediately without hitting external rate limits.

If you wish to re-load and geocode the raw CSV data from scratch (which takes ~1-2 hours due to Nominatim's strict 1 request/second rate limit), you can run the production management command:

```bash
python manage.py load_stations
```

## Running

```bash
python manage.py runserver
```

## API Reference

### `POST /api/route/`

**Request:**
```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

**Response:**
```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY",
  "total_distance_miles": 2796.0,
  "total_fuel_cost": 1331.44,
  "fuel_stops": [
    {
      "opis_id": 970,
      "name": "CIRCLE K #2702885",
      "city": "Phoenix",
      "state": "AZ",
      "lat": 33.4484,
      "lon": -112.0740,
      "price": 3.392,
      "gallons": 35.714,
      "cost": 121.14,
      "distance_from_start": 357.1
    }
  ],
  "route_geometry": {
    "type": "LineString",
    "coordinates": [[-118.2437, 34.0522], ...]
  }
}
```

**Error responses:**
| Status | When |
|---|---|
| `400` | Missing/empty start or finish |
| `422` | Location not found, no route, no reachable stations |
| `500` | Unexpected server error |

## External API Call Budget

| Call | Count | Service |
|---|---|---|
| Geocode `start` | 1 | Nominatim |
| Geocode `finish` | 1 | Nominatim |
| Get driving route + polyline | 1 | OSRM |
| **Total per request** | **3** | — |

Station geocoding is a one-time offline operation (`seed_stations.py`) — **zero** geocoding calls at request time.

## Algorithm

The fuel-stop planner uses a **greedy cost-optimisation** strategy:

1. Start at origin with a full tank (500 miles range).
2. Find all stations reachable within the current tank range.
3. Among reachable stations, select the **cheapest** one that still guarantees we can reach the next station (or destination).
4. Refuel to a full tank. Advance to that station.
5. Repeat until the destination is reachable on remaining fuel.
6. Compute total cost = Σ(gallons_purchased × price_per_gallon) at each stop (where gallons_purchased exactly matches the fuel consumed since the last stop/origin) + the cost of fuel consumed on the final leg (priced at the last stop's rate).

## Configuration

Override via environment variables or `settings.py`:

| Setting | Default | Description |
|---|---|---|
| `STATION_CORRIDOR_MILES` | `5` | Max deviation from route to consider a station |
| `VEHICLE_RANGE_MILES` | `500` | Vehicle max range on a full tank |
| `VEHICLE_MPG` | `10` | Fuel efficiency |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | OSRM endpoint |

## Testing

```bash
python test_integration.py
```

Runs the full pipeline with mocked external APIs — no network access needed.
