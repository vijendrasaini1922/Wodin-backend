from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ContactView, MyTicketViewSet, StaffTicketViewSet

router = DefaultRouter()
router.register("support/tickets", MyTicketViewSet, basename="ticket")
router.register("ops/tickets", StaffTicketViewSet, basename="staff-ticket")
urlpatterns = [path("support/contact/", ContactView.as_view())] + router.urls
