"""
Django settings for fuel_navigator project.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-fuel-navigator-dev-key-change-in-prod",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "routes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "fuel_navigator.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
}

# --- App-level settings ---
# Nominatim requires a custom User-Agent; identify your app.
NOMINATIM_USER_AGENT = "fuel-cost-navigator/1.0"

# ── Routing providers (tried in this order) ───────────────────────────────
# 1. OpenRouteService — free, 2000 req/day.  Get a key at:
#    https://openrouteservice.org/dev/#/signup
#    Set via env:  export ORS_API_KEY="your-key-here"
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")

# 2. OSRM public endpoint — completely free, no key needed.
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")

# 3. Straight-line interpolation fallback — always works, no external calls.
#    Route geometry will be a great-circle arc instead of road-following.
#    Set to False to disable and raise an error instead.
ROUTING_ALLOW_FALLBACK = os.environ.get("ROUTING_ALLOW_FALLBACK", "True") == "True"

# ── Request settings ──────────────────────────────────────────────────────
# Number of retries for transient network failures (429, 503, connection reset)
EXTERNAL_REQUEST_RETRIES = int(os.environ.get("EXTERNAL_REQUEST_RETRIES", "2"))
EXTERNAL_REQUEST_TIMEOUT = int(os.environ.get("EXTERNAL_REQUEST_TIMEOUT", "15"))

# Maximum distance a fuel station is allowed to deviate from the route (miles).
STATION_CORRIDOR_MILES = float(os.environ.get("STATION_CORRIDOR_MILES", "5"))

# Vehicle parameters
VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10

# Path to the CSV file (relative to BASE_DIR)
FUEL_PRICES_CSV = BASE_DIR / "fuel-prices.csv"
