from django.db import models


class FuelStation(models.Model):
    """
    Represents a single truck-stop / fuel station loaded from the CSV.

    One CSV row per station is kept (lowest price when duplicates exist for
    the same OPIS Truckstop ID).  Coordinates are resolved once during the
    `load_stations` management command and persisted here so the hot API
    path never re-geocodes.
    """

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    retail_price = models.DecimalField(max_digits=8, decimal_places=5)

    # Resolved by Nominatim during load_stations
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state"]),
            models.Index(fields=["retail_price"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) @ ${self.retail_price}"
