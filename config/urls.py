from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.catalog.urls")),
    path("api/", include("apps.trips.urls")),
    path("api/", include("apps.photos.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.payments.urls")),
    path("api/", include("apps.production.urls")),
    path("api/", include("apps.shipping.urls")),
    path("api/", include("apps.reviews.urls")),
    path("api/", include("apps.support.urls")),
    path("api/", include("apps.adminapi.urls")),
    path("api/", include("apps.invoices.urls")),
    path("api/", include("apps.aidraft.urls")),
    path("api/", include("apps.aibook.urls")),
]
