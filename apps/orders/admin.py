from django.contrib import admin

from .models import Order, OrderItem, OrderStatusHistory

for m in (Order, OrderItem, OrderStatusHistory):
    admin.site.register(m)
