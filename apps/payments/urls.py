from django.urls import path

from . import views

urlpatterns = [
    path("orders/<uuid:order_id>/payment/", views.StartPaymentView.as_view()),
    path("orders/<uuid:order_id>/payment/confirm/", views.ConfirmPaymentView.as_view()),
    path("orders/<uuid:order_id>/payment/mock-pay/", views.MockPayView.as_view()),
    path("orders/<uuid:order_id>/payments/", views.OrderPaymentsView.as_view()),
    path("ops/payments/<uuid:payment_id>/refund/", views.RefundView.as_view()),
    path("payments/webhook/razorpay/", views.razorpay_webhook),
]
