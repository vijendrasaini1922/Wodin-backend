from rest_framework import serializers

from .models import Trip


class TripSerializer(serializers.ModelSerializer):
    destinations = serializers.ListField(child=serializers.CharField(max_length=80), min_length=1, max_length=10)
    companions = serializers.ListField(child=serializers.CharField(max_length=80), required=False, max_length=20)

    class Meta:
        model = Trip
        fields = ["id", "title", "destinations", "start_date", "end_date", "companions", "description",
                  "special_message", "cover_title", "dedication", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "End date cannot be before start date."})
        return attrs
