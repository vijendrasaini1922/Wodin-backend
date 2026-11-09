from django.contrib import admin

from .models import AddOn, Coupon, Paper, PricingSettings, Product, ProductVariant, Theme

for m in (Product, ProductVariant, Paper, Theme, AddOn, PricingSettings, Coupon):
    admin.site.register(m)
