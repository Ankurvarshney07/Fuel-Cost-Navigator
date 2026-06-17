"""
routes/views.py
===============
Single API endpoint: POST /api/route/
"""

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from routes.serializers import RouteRequestSerializer, RouteResponseSerializer
from routes.services import plan_route

logger = logging.getLogger(__name__)


class RouteView(APIView):
    """
    POST /api/route/

    Accepts a start and finish location within the USA and returns:
      - A GeoJSON LineString of the driving route
      - Optimal fuel stops along the route (cost-effective, 500-mile range)
      - Total fuel cost (assuming 10 mpg)

    Request body (JSON):
        {
            "start": "Los Angeles, CA",
            "finish": "New York, NY"
        }
    """

    def post(self, request: Request) -> Response:
        # --- 1. Validate input ---
        req_serializer = RouteRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(
                {"error": "Invalid request", "details": req_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start = req_serializer.validated_data["start"]
        finish = req_serializer.validated_data["finish"]

        # --- 2. Run service ---
        try:
            result = plan_route(start, finish)
        except ValueError as exc:
            # Business-logic errors (geocoding failed, no route, no stations)
            logger.warning("Route planning failed for '%s' → '%s': %s", start, finish, exc)
            return Response(
                {"error": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as exc:
            # Unexpected errors
            logger.exception("Unexpected error while planning route")
            return Response(
                {"error": "An internal error occurred. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 3. Serialize and return ---
        resp_serializer = RouteResponseSerializer(result)
        return Response(resp_serializer.data, status=status.HTTP_200_OK)
