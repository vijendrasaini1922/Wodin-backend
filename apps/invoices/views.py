from django.conf import settings
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Order
from apps.photos.storage import get_storage

from . import services


class InvoiceView(APIView):
    """Customer: invoice metadata plus a short-lived signed PDF download link."""

    def get(self, request, order_id):
        order = Order.objects.filter(pk=order_id, user=request.user).first()
        if not order:
            raise NotFound()
        inv = services.get_or_issue(order)
        return Response({"number": inv.number, "issued_at": inv.issued_at, "totals": inv.totals,
                         "pdf_url": get_storage().download_url(inv.pdf_key, settings.DOWNLOAD_URL_TTL_SECONDS)})
