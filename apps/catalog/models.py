from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

Money = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class Product(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    """A sellable size/cover configuration. All prices are admin-editable."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=80, help_text="e.g. 8x8 inch, A4 portrait")
    cover = models.CharField(max_length=40, default="Hardcover")
    base_page_count = models.PositiveSmallIntegerField(default=20)
    allowed_page_counts = models.JSONField(default=list, help_text="e.g. [20, 24, 32, 40]")
    base_price = models.DecimalField(**Money)
    extra_page_price = models.DecimalField(default=Decimal("0"), **{k: v for k, v in Money.items() if k != "default"})
    additional_copy_price = models.DecimalField(**Money)
    # Print specification (confirm with the printer after physical tests)
    trim_width_mm = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("203.2"))
    trim_height_mm = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("203.2"))
    bleed_mm = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("3.0"))
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["product", "sort_order", "name"]

    def __str__(self):
        return f"{self.product.name} / {self.name}"


class Paper(models.Model):
    name = models.CharField(max_length=60, unique=True)
    surcharge = models.DecimalField(default=Decimal("0"), **{k: v for k, v in Money.items() if k != "default"})
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Theme(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AddOn(models.Model):
    name = models.CharField(max_length=80, unique=True)
    price = models.DecimalField(**Money)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PricingSettings(models.Model):
    """Singleton (pk=1): shipping and tax rules."""

    shipping_fee = models.DecimalField(default=Decimal("80"), **{k: v for k, v in Money.items() if k != "default"})
    free_shipping_above = models.DecimalField(null=True, blank=True, max_digits=10, decimal_places=2)
    tax_rate_percent = models.DecimalField(default=Decimal("0"), max_digits=5, decimal_places=2)
    prices_include_tax = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "pricing settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Coupon(models.Model):
    class Kind(models.TextChoices):
        PERCENT = "percent", "Percent"
        FLAT = "flat", "Flat amount"

    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_subtotal = models.DecimalField(default=Decimal("0"), max_digits=10, decimal_places=2)
    max_discount = models.DecimalField(null=True, blank=True, max_digits=10, decimal_places=2)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code

    def validate_for(self, subtotal: Decimal) -> str | None:
        """Return an error message if unusable, else None."""
        now = timezone.now()
        if not self.is_active:
            return "This coupon is not active."
        if self.valid_from and now < self.valid_from:
            return "This coupon is not valid yet."
        if self.valid_until and now > self.valid_until:
            return "This coupon has expired."
        if self.usage_limit is not None and self.used_count >= self.usage_limit:
            return "This coupon has reached its usage limit."
        if subtotal < self.min_subtotal:
            return f"Minimum order of ₹{self.min_subtotal} required for this coupon."
        return None

    def discount_for(self, subtotal: Decimal) -> Decimal:
        amount = subtotal * self.value / 100 if self.kind == self.Kind.PERCENT else self.value
        if self.max_discount is not None:
            amount = min(amount, self.max_discount)
        return min(amount, subtotal).quantize(Decimal("0.01"))
