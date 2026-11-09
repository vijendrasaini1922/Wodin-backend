from django.urls import path

from . import views

urlpatterns = [
    path("design/layouts/", views.LayoutLibraryView.as_view()),
    path("design/projects/<uuid:project_id>/ai-draft/", views.StartAIDraftView.as_view()),
    path("design/projects/<uuid:project_id>/ai-draft/<uuid:job_id>/", views.AIDraftStatusView.as_view()),
]
