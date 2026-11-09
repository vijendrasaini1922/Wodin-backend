from django.urls import path

from .views import InvoiceView

urlpatterns = [path("orders/<uuid:order_id>/invoice/", InvoiceView.as_view())]
