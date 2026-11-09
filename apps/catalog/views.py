from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from . import pricing
from .models import AddOn, Paper, Product, Theme
from .serializers import (AddOnSerializer, PaperSerializer, ProductSerializer, QuoteRequestSerializer,
                          ThemeSerializer)


class ProductViewSet(ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = ProductSerializer
    queryset = Product.objects.filter(is_active=True).prefetch_related("variants")
    lookup_field = "slug"
    pagination_class = None


class OptionsView(APIView):
    """Papers, themes and add-ons shown in the configurator."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "papers": PaperSerializer(Paper.objects.filter(is_active=True), many=True).data,
            "themes": ThemeSerializer(Theme.objects.filter(is_active=True), many=True).data,
            "addons": AddOnSerializer(AddOn.objects.filter(is_active=True), many=True).data,
        })


class QuoteView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = QuoteRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(pricing.quote(**ser.validated_data))


class CouponValidateView(APIView):
    """Preview a coupon against the current configuration (does not consume it)."""

    permission_classes = [AllowAny]

    def post(self, request):
        ser = QuoteRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not ser.validated_data.get("coupon_code"):
            return Response({"coupon_code": "Enter a coupon code."}, status=400)
        quote = pricing.quote(**ser.validated_data)
        return Response({"valid": True, "code": quote["coupon"], "discount": quote["discount"], "total": quote["total"]})
