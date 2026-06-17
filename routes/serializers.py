"""
routes/serializers.py
=====================
DRF serializers used ONLY for response formatting.
All validation and business logic lives in services.py.
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates the incoming POST payload."""

    start = serializers.CharField(
        max_length=300,
        help_text="Starting location within the USA (e.g. 'Los Angeles, CA')",
    )
    finish = serializers.CharField(
        max_length=300,
        help_text="Destination location within the USA (e.g. 'New York, NY')",
    )

    def validate_start(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Start location cannot be empty.")
        return value

    def validate_finish(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Finish location cannot be empty.")
        return value

    def validate(self, attrs):
        if attrs["start"].lower() == attrs["finish"].lower():
            raise serializers.ValidationError(
                "Start and finish locations must be different."
            )
        return attrs


class FuelStopSerializer(serializers.Serializer):
    """Serializes a single FuelStop dataclass for the response."""

    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    price = serializers.FloatField()
    gallons = serializers.FloatField()
    cost = serializers.FloatField()
    distance_from_start = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    """Serializes the full RouteResult dataclass."""

    start = serializers.CharField()
    finish = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
    routing_provider = serializers.CharField(
        help_text="Which routing provider was used (openrouteservice / osrm / interpolation)"
    )
    fuel_stops = FuelStopSerializer(many=True)
    route_geometry = serializers.DictField(
        help_text="GeoJSON LineString of the full driving route"
    )
