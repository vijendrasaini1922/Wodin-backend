import re

from rest_framework.exceptions import ValidationError

_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def normalise_phone(raw: str) -> str:
    """Return E.164 (+91XXXXXXXXXX) for an Indian mobile number or raise ValidationError."""
    digits = re.sub(r"[\s\-()]", "", str(raw or ""))
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _INDIAN_MOBILE.match(digits):
        raise ValidationError({"phone": "Enter a valid 10-digit Indian mobile number."})
    return f"+91{digits}"
