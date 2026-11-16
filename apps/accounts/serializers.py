from rest_framework import serializers

from .models import Address, CustomerProfile, User


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["transactional_consent", "marketing_consent", "preferred_language"]


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(required=False)
    # Google-only accounts have a placeholder in User.phone; clients only ever see the real number (or "").
    phone = serializers.CharField(source="display_phone", read_only=True)
    has_google = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone", "full_name", "email", "role", "is_guest", "has_google", "has_password", "profile"]
        read_only_fields = ["id", "phone", "role", "is_guest", "has_google", "has_password"]

    def get_has_google(self, obj) -> bool:
        return bool(obj.google_sub)

    def get_has_password(self, obj) -> bool:
        return obj.has_usable_password()

    def update(self, instance, validated):
        profile = validated.pop("profile", None)
        if instance.google_sub:
            validated.pop("email", None)  # Google-verified email is the login identity; it cannot be edited
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


class GoogleLoginSerializer(serializers.Serializer):
    credential = serializers.CharField(max_length=4096)


class PasswordLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class SetPasswordSerializer(serializers.Serializer):
    # Required only when the account already has a password (checked in the view).
    current_password = serializers.CharField(max_length=128, trim_whitespace=False, required=False, allow_blank=True)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)
