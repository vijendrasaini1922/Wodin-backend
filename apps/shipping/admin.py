from django.contrib import admin

from .models import NonServiceablePincode, Shipment, ShipmentEvent

for m in (Shipment, ShipmentEvent, NonServiceablePincode):
    admin.site.register(m)
