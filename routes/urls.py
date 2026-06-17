from django.urls import path
from routes.views import RouteView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
]
