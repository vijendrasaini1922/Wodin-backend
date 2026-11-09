from rest_framework import serializers

from .models import Address, CustomerProfile, User


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["transactional_consent", "marketing_consent", "preferred_language"]


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(required=False)

    class Meta:
        model = User
        fields = ["id", "phone", "full_name", "email", "role", "is_guest", "profile"]
        read_only_fields = ["id", "phone", "role", "is_guest"]

    def update(self, instance, validated):
        profile = validated.pop("profile", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        if profile:
            for k, v in profile.items():
                setattr(instance.profile, k, v)
            instance.profile.save()
        return instance


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ["id", "label", "recipient_name", "phone", "line1", "line2", "city", "state", "pincode", "is_default"]
        read_only_fields = ["id"]

    def validate_pincode(self, value):
        if not (value.isdigit() and len(value) == 6 and value[0] != "0"):
            raise serializers.ValidationError("Enter a valid 6-digit PIN code.")
        return value


class OTPRequestSerializer(serializers.Serializer):
    phone = serializers.CharField()


class OTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField()
    code = serializers.RegexField(r"^\d{6}$")
