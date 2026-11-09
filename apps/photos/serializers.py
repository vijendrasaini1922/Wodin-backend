from rest_framework import serializers

from .models import Photo, PhotoGroup
from .storage import get_storage
from django.conf import settings


class UploadFileSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=40, allow_blank=True, default="")
    size = serializers.IntegerField(min_value=1)


class UploadRequestSerializer(serializers.Serializer):
    files = UploadFileSerializer(many=True, allow_empty=False, max_length=100)


class PhotoSerializer(serializers.ModelSerializer):
    thumb_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = Photo
        fields = ["id", "trip", "group", "original_filename", "status", "reject_reason", "width", "height",
                  "taken_at", "flags", "duplicate_of", "rotation", "crop", "is_favourite", "caption",
                  "position", "thumb_url", "preview_url"]
        read_only_fields = ["id", "trip", "original_filename", "status", "reject_reason", "width", "height",
                            "taken_at", "flags", "duplicate_of", "position"]

    def _url(self, key):
        return get_storage().download_url(key, settings.DOWNLOAD_URL_TTL_SECONDS) if key else None

    def get_thumb_url(self, obj):
        return self._url(obj.thumb_key) if obj.status == Photo.Status.READY else None

    def get_preview_url(self, obj):
        return self._url(obj.preview_key) if obj.status == Photo.Status.READY else None

    def validate_crop(self, value):
        if value is None:
            return value
        try:
            x, y, w, h = (float(value[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise serializers.ValidationError("crop must contain numeric x, y, w, h.")
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
            raise serializers.ValidationError("crop values must be fractions within the image.")
        return {"x": x, "y": y, "w": w, "h": h}

    def validate_rotation(self, value):
        if value not in (0, 90, 180, 270):
            raise serializers.ValidationError("rotation must be 0, 90, 180 or 270.")
        return value

    def validate_group(self, value):
        if value and value.trip_id != self.instance.trip_id:
            raise serializers.ValidationError("Group belongs to a different trip.")
        return value


class PhotoGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = PhotoGroup
        fields = ["id", "title", "position"]
        read_only_fields = ["id"]
