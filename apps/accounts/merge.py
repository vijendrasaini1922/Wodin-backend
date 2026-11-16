"""Account linking: move a guest's data to the signed-in account once the same person verifies (Google or phone OTP)."""
from django.apps import apps


def merge_guest_into(guest, user) -> None:
    if guest.pk == user.pk or not guest.is_guest:
        return
    for label, field in [("trips.Trip", "user"), ("photos.Photo", "user"), ("projects.BookProject", "user"),
                         ("orders.Order", "user"), ("accounts.Address", "user")]:
        apps.get_model(label).objects.filter(**{field: guest}).update(**{field: user})
    for order in apps.get_model("orders.Order").objects.filter(user=user, contact_phone__startswith="guest:"):
        order.contact_phone = user.display_phone
        order.save(update_fields=["contact_phone"])
    if not user.full_name and guest.full_name:
        user.full_name = guest.full_name
        user.save(update_fields=["full_name"])
    guest.is_active = False
    guest.save(update_fields=["is_active"])
