from rest_framework import serializers

from .models import Review


class PublicReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["id", "rating", "title", "body", "display_name", "destination", "is_verified", "is_featured",
                  "response", "created_at"]


class MyReviewSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True, default=None)

    class Meta:
        model = Review
        fields = ["id", "order", "order_number", "rating", "title", "body", "display_name", "destination",
                  "consent_publish_review", "consent_publish_images", "status", "is_verified", "response",
                  "removal_requested", "created_at"]
        read_only_fields = ["id", "order_number", "status", "is_verified", "response", "removal_requested", "created_at"]

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value

    def validate_body(self, value):
        if len(value.strip()) < 10:
            raise serializers.ValidationError("Please write at least a few words.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("consent_publish_review"):
            raise serializers.ValidationError({"consent_publish_review": "We need your permission to publish this review."})
        return attrs


class AdminReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["id", "user", "order", "is_verified", "is_demo", "rating", "title", "body", "display_name",
                  "destination", "consent_publish_review", "consent_publish_images", "status", "is_featured",
                  "response", "removal_requested", "created_at"]
        read_only_fields = ["id", "user", "order", "is_verified", "is_demo", "rating", "title", "body", "display_name",
                            "destination", "consent_publish_review", "consent_publish_images", "removal_requested",
                            "created_at"]
