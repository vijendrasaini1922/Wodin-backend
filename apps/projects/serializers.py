from rest_framework import serializers

from apps.photos.models import Photo

from .models import Approval, BookPage, BookProject, BookRevision, ChangeRequest, PageElement


class ProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookProject
        fields = ["id", "trip", "variant", "paper", "theme", "page_count", "copies", "addon_ids", "status"]
        read_only_fields = ["id", "status"]

    def validate_trip(self, trip):
        if trip.user_id != self.context["request"].user.id:
            raise serializers.ValidationError("Unknown trip.")
        return trip

    def validate(self, attrs):
        from apps.catalog import pricing
        # Reuse the pricing engine's validation of variant / page count / options.
        pricing.quote(variant_id=attrs["variant"].pk, page_count=attrs["page_count"],
                      paper_id=attrs["paper"].pk if attrs.get("paper") else None,
                      copies=attrs.get("copies", 1), addon_ids=attrs.get("addon_ids", []))
        return attrs


class ProjectSerializer(serializers.ModelSerializer):
    latest_revision = serializers.SerializerMethodField()
    order_id = serializers.SerializerMethodField()
    order_number = serializers.SerializerMethodField()
    aspect = serializers.SerializerMethodField()

    class Meta:
        model = BookProject
        fields = ["id", "trip", "variant", "paper", "theme", "page_count", "copies", "addon_ids", "status", "aspect",
                  "latest_revision", "order_id", "order_number", "created_at", "updated_at"]
        read_only_fields = fields

    def get_aspect(self, obj):
        """Page width / height of the printed book, so previews and layouts match its real shape."""
        v = obj.variant
        return round(float(v.trim_width_mm) / float(v.trim_height_mm), 4)

    def get_latest_revision(self, obj):
        rev = obj.revisions.order_by("-number").first()
        return {"id": str(rev.id), "number": rev.number, "status": rev.status} if rev else None

    def _item(self, obj):
        return getattr(obj, "order_item", None)

    def get_order_id(self, obj):
        item = self._item(obj)
        return str(item.order_id) if item else None

    def get_order_number(self, obj):
        item = self._item(obj)
        return item.order.number if item else None


class ElementSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageElement
        fields = ["id", "kind", "photo", "text", "x", "y", "w", "h", "z", "style"]

    def validate(self, attrs):
        for k in ("x", "y", "w", "h"):
            if k in attrs and not 0 <= attrs[k] <= 1:
                raise serializers.ValidationError({k: "Must be a fraction between 0 and 1."})
        kind = attrs.get("kind")
        if kind == PageElement.Kind.PHOTO and not attrs.get("photo"):
            raise serializers.ValidationError({"photo": "Photo elements need a photo."})
        return attrs


class PageSerializer(serializers.ModelSerializer):
    elements = ElementSerializer(many=True, required=False)

    class Meta:
        model = BookPage
        fields = ["number", "layout", "elements"]


class RevisionSerializer(serializers.ModelSerializer):
    approved = serializers.SerializerMethodField()

    class Meta:
        model = BookRevision
        fields = ["id", "number", "status", "notes", "snapshot", "created_at", "approved"]

    def get_approved(self, obj):
        a = getattr(obj, "approval", None)
        return bool(a and a.is_valid)


class ChangeRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeRequest
        fields = ["id", "page_number", "note", "created_at", "resolved"]
        read_only_fields = ["id", "created_at", "resolved"]
