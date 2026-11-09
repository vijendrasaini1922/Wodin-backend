from rest_framework import serializers

from .models import AddOn, Paper, Product, ProductVariant, Theme


class VariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ["id", "name", "cover", "base_page_count", "allowed_page_counts", "base_price",
                  "extra_page_price", "additional_copy_price"]


class ProductSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "tagline", "description", "variants"]

    def get_variants(self, obj):
        return VariantSerializer(obj.variants.filter(is_active=True), many=True).data


class PaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paper
        fields = ["id", "name", "surcharge"]


class ThemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Theme
        fields = ["id", "name", "slug"]


class AddOnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddOn
        fields = ["id", "name", "price"]


class QuoteRequestSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField()
    page_count = serializers.IntegerField()
    paper_id = serializers.IntegerField(required=False, allow_null=True)
    copies = serializers.IntegerField(default=1)
    addon_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    coupon_code = serializers.CharField(required=False, allow_blank=True, default="")
