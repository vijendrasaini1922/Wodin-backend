from django.contrib import admin

from .models import Address, CustomerProfile, User

admin.site.register(User)
admin.site.register(CustomerProfile)
admin.site.register(Address)
