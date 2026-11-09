from django.urls import path

from . import views

urlpatterns = [
    path("ops/production/", views.JobListCreateView.as_view()),
    path("ops/production/<int:pk>/", views.JobDetailView.as_view()),
    path("ops/production/<int:pk>/generate-pdf/", views.GeneratePdfView.as_view()),
    path("ops/production/<int:pk>/pdf/", views.PdfDownloadView.as_view()),
    path("ops/production/<int:pk>/start-printing/", views.StartPrintingView.as_view()),
    path("ops/production/<int:pk>/quality-check/", views.ToQualityCheckView.as_view()),
    path("ops/production/<int:pk>/qc/", views.QcView.as_view()),
    path("ops/production/<int:pk>/qc-fail/", views.QcFailView.as_view()),
]
