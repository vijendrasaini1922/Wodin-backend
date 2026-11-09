"""Pricing engine. Pure function of catalogue state; its output is stored on orders as a snapshot."""
from decimal import ROUND_HALF_UP, Decimal

from rest_framework.exceptions import ValidationError

from .models import AddOn, Coupon, Paper, PricingSettings, ProductVariant

TWO = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    return f"{_q(value):.2f}"


def quote(*, variant_id, page_count, paper_id=None, copies=1, addon_ids=(), coupon_code="") -> dict:
    """Return a JSON-serialisable price breakdown. Raises ValidationError for invalid input."""
    try:
        variant = ProductVariant.objects.select_related("product").get(
            pk=variant_id, is_active=True, product__is_active=True
        )
    except ProductVariant.DoesNotExist:
        raise ValidationError({"variant_id": "Unknown or unavailable product variant."})
    if page_count not in (variant.allowed_page_counts or [variant.base_page_count]):
        raise ValidationError({"page_count": "Unsupported page count for this variant."})
    if not 1 <= copies <= 50:
        raise ValidationError({"copies": "Copies must be between 1 and 50."})

    paper = None
    if paper_id:
        try:
            paper = Paper.objects.get(pk=paper_id, is_active=True)
        except Paper.DoesNotExist:
            raise ValidationError({"paper_id": "Unknown paper option."})
    addons = list(AddOn.objects.filter(pk__in=addon_ids, is_active=True))
    if len(addons) != len(set(addon_ids)):
        raise ValidationError({"addon_ids": "Unknown add-on."})

    cfg = PricingSettings.load()
    extra_pages = max(0, page_count - variant.base_page_count)
    first_copy_lines = [("Photobook", variant.base_price)]
    if extra_pages:
        first_copy_lines.append((f"Extra pages ({extra_pages})", variant.extra_page_price * extra_pages))
    paper_charge = paper.surcharge if paper else Decimal("0")
    if paper_charge:
        first_copy_lines.append((f"{paper.name} paper", paper_charge))
    per_copy = sum((amt for _, amt in first_copy_lines), Decimal("0"))

    lines = [{"label": label, "amount": _s(amt)} for label, amt in first_copy_lines]
    subtotal = per_copy
    if copies > 1:
        # Additional copies use the (usually cheaper) additional-copy price plus the same page/paper extras.
        extras = per_copy - variant.base_price
        extra_copy_each = variant.additional_copy_price + extras
        extra_total = extra_copy_each * (copies - 1)
        lines.append({"label": f"Additional copies ({copies - 1})", "amount": _s(extra_total)})
        subtotal += extra_total
    for addon in addons:
        lines.append({"label": addon.name, "amount": _s(addon.price)})
        subtotal += addon.price

    discount = Decimal("0")
    coupon_applied = ""
    if coupon_code:
        try:
            coupon = Coupon.objects.get(code=coupon_code.strip().upper())
        except Coupon.DoesNotExist:
            raise ValidationError({"coupon_code": "Invalid coupon code."})
        error = coupon.validate_for(subtotal)
        if error:
            raise ValidationError({"coupon_code": error})
        discount = coupon.discount_for(subtotal)
        coupon_applied = coupon.code

    taxable = subtotal - discount
    shipping = cfg.shipping_fee
    if cfg.free_shipping_above is not None and taxable >= cfg.free_shipping_above:
        shipping = Decimal("0")

    rate = cfg.tax_rate_percent
    if cfg.prices_include_tax:
        tax = taxable - taxable / (1 + rate / 100) if rate else Decimal("0")
        total = taxable + shipping
    else:
        tax = taxable * rate / 100
        total = taxable + tax + shipping

    return {
        "currency": "INR",
        "variant_id": variant.pk,
        "variant_name": str(variant),
        "page_count": page_count,
        "paper": paper.name if paper else None,
        "copies": copies,
        "lines": lines,
        "subtotal": _s(subtotal),
        "discount": _s(discount),
        "coupon": coupon_applied,
        "shipping": _s(shipping),
        "tax": _s(tax),
        "prices_include_tax": cfg.prices_include_tax,
        "total": _s(total),
    }
