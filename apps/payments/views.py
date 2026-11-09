import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsOperations
from apps.orders.models import Order

from . import services
from .models import Payment
from .serializers import ConfirmSerializer, PaymentSerializer, RefundRequestSerializer, RefundSerializer


def _my_order(request, order_id) -> Order:
    order = Order.objects.filter(pk=order_id, user=request.user).first()
    if not order:
        raise NotFound()
    return order


class StartPaymentView(APIView):
    def post(self, request, order_id):
        order = _my_order(request, order_id)
        payment, provider = services.start_payment(order)
        user = request.user
        return Response({
            "payment_id": str(payment.id), "provider": provider.name, "key_id": provider.public_key(),
            "gateway_order_id": payment.gateway_order_id, "amount": str(payment.amount),
            "amount_paise": services._paise(payment.amount), "currency": payment.currency,
            "name": "WoDin", "description": f"Order {order.number}",
            "prefill": {"name": order.contact_name or user.full_name, "contact": order.contact_phone,
                        "email": order.contact_email},
        })


class ConfirmPaymentView(APIView):
    def post(self, request, order_id):
        order = _my_order(request, order_id)
        ser = ConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        payment = services.confirm_checkout(order, d["gateway_order_id"], d["gateway_payment_id"], d["signature"])
        order.refresh_from_db()
        return Response({"status": payment.status, "order_status": order.status, "payment_status": order.payment_status})


class MockPayView(APIView):
    """DEV ONLY: lets the browser complete a payment against the mock provider.

    The mock provider itself refuses to load unless DEBUG is on, and this view additionally 404s for any
    other provider, so it cannot be used against Razorpay or in production.
    """

    def post(self, request, order_id):
        from .providers import FakeProvider, get_provider

        provider = get_provider()
        if provider.name != "mock":
            raise NotFound()
        order = _my_order(request, order_id)
        gateway_order_id = str(request.data.get("gateway_order_id", ""))
        if not order.payments.filter(gateway_order_id=gateway_order_id).exists():
            raise ValidationError({"detail": "Unknown payment."})
        payment_id = f"pay_mock_{gateway_order_id[-10:]}"
        return Response({"gateway_order_id": gateway_order_id, "gateway_payment_id": payment_id,
                         "signature": FakeProvider.sign(gateway_order_id, payment_id)})


class OrderPaymentsView(APIView):
    def get(self, request, order_id):
        order = _my_order(request, order_id)
        return Response(PaymentSerializer(order.payments.prefetch_related("refunds"), many=True).data)


class RefundView(APIView):
    permission_classes = [IsOperations]

    def post(self, request, payment_id):
        payment = Payment.objects.filter(pk=payment_id).first()
        if not payment:
            raise NotFound()
        ser = RefundRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        refund = services.refund_payment(payment, ser.validated_data["amount"], ser.validated_data["reason"], request.user)
        return Response(RefundSerializer(refund).data, status=201)


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    body = request.body
    try:
        payload = json.loads(body)
    except ValueError:
        return JsonResponse({"detail": "bad payload"}, status=400)
    try:
        result = services.handle_webhook(body, request.headers.get("X-Razorpay-Signature", ""),
                                         request.headers.get("X-Razorpay-Event-Id", ""), payload)
    except PermissionError:
        return JsonResponse({"detail": "invalid signature"}, status=400)
    except ValidationError as exc:  # e.g. amount mismatch: reject (400) so the provider retries / ops investigates
        return JsonResponse({"detail": exc.detail}, status=400)
    return JsonResponse({"result": result})
