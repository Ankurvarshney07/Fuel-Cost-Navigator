"""
Management command: load_stations
==================================
Usage:
    python manage.py load_stations

Reads the fuel-prices CSV, deduplicates stations (lowest price per OPIS ID),
filters to US-only stations, and geocodes each station's city/state using
Nominatim.  Results are persisted in the FuelStation table so the hot API
path requires zero geocoding calls for stations.

This command is idempotent — run it multiple times safely.  Already-geocoded
stations are skipped unless --force is passed.

Geocoding is rate-limited to respect Nominatim's 1 req/s policy.
"""

import csv
import time
import logging
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import requests

logger = logging.getLogger(__name__)

# US state abbreviations (2-letter postal codes)
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_DELAY = 1.1   # seconds between requests (Nominatim ToS: 1 req/s)


class Command(BaseCommand):
    help = "Load fuel stations from the CSV and geocode their coordinates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Re-geocode stations that already have coordinates.",
        )
        parser.add_argument(
            "--csv",
            type=str,
            default=None,
            help="Path to the CSV file (defaults to settings.FUEL_PRICES_CSV).",
        )

    def handle(self, *args, **options):
        from routes.models import FuelStation

        csv_path = Path(options["csv"] or settings.FUEL_PRICES_CSV)
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        self.stdout.write(f"Reading CSV: {csv_path}")
        stations_raw = self._parse_csv(csv_path)
        self.stdout.write(f"  → {len(stations_raw)} unique US stations after deduplication.")

        force = options["force"]
        created = 0
        updated = 0
        geocoded = 0
        skipped = 0

        for opis_id, data in stations_raw.items():
            obj, was_created = FuelStation.objects.update_or_create(
                opis_id=opis_id,
                defaults={
                    "name": data["name"],
                    "address": data["address"],
                    "city": data["city"],
                    "state": data["state"],
                    "retail_price": data["price"],
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

            # Geocode if coordinates are missing (or --force)
            needs_geocode = force or (obj.latitude is None or obj.longitude is None)
            if needs_geocode:
                coords = self._geocode(data["city"], data["state"])
                if coords:
                    obj.latitude, obj.longitude = coords
                    obj.save(update_fields=["latitude", "longitude"])
                    geocoded += 1
                else:
                    skipped += 1
                    self.stderr.write(
                        f"  ⚠ Could not geocode: {data['city']}, {data['state']}"
                    )
                time.sleep(GEOCODE_DELAY)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Created: {created}  Updated: {updated}  "
                f"Geocoded: {geocoded}  Failed: {skipped}"
            )
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _parse_csv(self, path: Path) -> dict:
        """
        Parse the CSV and return a dict keyed by OPIS Truckstop ID.
        For each station, keep the row with the LOWEST retail price.
        Canadian stations (non-US state codes) are excluded.
        """
        best: dict = {}

        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                state = row.get("State", "").strip().upper()
                if state not in US_STATES:
                    continue

                try:
                    opis_id = int(row["OPIS Truckstop ID"].strip())
                    price = float(row["Retail Price"].strip())
                except (ValueError, KeyError):
                    continue

                if opis_id not in best or price < best[opis_id]["price"]:
                    best[opis_id] = {
                        "name": row.get("Truckstop Name", "").strip()[:200],
                        "address": row.get("Address", "").strip()[:300],
                        "city": row.get("City", "").strip()[:100],
                        "state": state,
                        "price": price,
                    }

        return best

    def _geocode(self, city: str, state: str):
        """
        Geocode a city/state pair via Nominatim.
        Returns (lat, lon) floats or None on failure.
        """
        query = f"{city.strip()}, {state}, USA"
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",
        }
        headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

        try:
            resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception as exc:
            logger.warning("Geocoding failed for '%s, %s': %s", city, state, exc)

        return None
