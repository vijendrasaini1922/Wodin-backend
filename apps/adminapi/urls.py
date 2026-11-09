from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("ops/orders", views.AdminOrderViewSet, basename="ops-order")
router.register("ops/customers", views.CustomerViewSet, basename="ops-customer")
router.register("admin/staff", views.StaffUserViewSet, basename="admin-staff")
router.register("admin/products", views.ProductAdminViewSet, basename="admin-product")
router.register("admin/variants", views.VariantAdminViewSet, basename="admin-variant")
router.register("admin/papers", views.PaperAdminViewSet, basename="admin-paper")
router.register("admin/themes", views.ThemeAdminViewSet, basename="admin-theme")
router.register("admin/addons", views.AddOnAdminViewSet, basename="admin-addon")
router.register("admin/coupons", views.CouponAdminViewSet, basename="admin-coupon")

urlpatterns = [
    path("ops/dashboard/", views.DashboardView.as_view()),
    path("ops/analytics/", views.AnalyticsView.as_view()),
    path("admin/audit-log/", views.AuditLogView.as_view()),
    path("admin/pricing-settings/", views.PricingSettingsView.as_view()),
] + router.urls
