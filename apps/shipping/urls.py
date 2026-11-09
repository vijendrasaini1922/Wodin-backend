from django.urls import path

from . import views

urlpatterns = [
    path("shipping/serviceability/", views.ServiceabilityView.as_view()),
    path("orders/<uuid:order_id>/shipment/", views.MyShipmentView.as_view()),
    path("ops/shipments/", views.ShipmentListDispatchView.as_view()),
    path("ops/shipments/<int:pk>/events/", views.ShipmentEventView.as_view()),
    path("ops/shipments/<int:pk>/delivered/", views.ShipmentDeliveredView.as_view()),
]
