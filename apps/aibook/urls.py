from django.urls import path

from . import views

urlpatterns = [
    path("aibooks/", views.BookListView.as_view()),
    path("aibooks/pdf/<str:token>/", views.local_pdf),
    path("aibooks/photos/<uuid:photo_id>/complete/", views.CompletePhotoView.as_view()),
    path("aibooks/photos/<uuid:photo_id>/", views.PhotoDeleteView.as_view()),
    path("aibooks/<uuid:pk>/", views.BookDetailView.as_view()),
    path("aibooks/<uuid:pk>/pages/", views.BookPagesView.as_view()),
    path("aibooks/<uuid:pk>/photos/", views.BookPhotosView.as_view()),
    path("aibooks/<uuid:pk>/photos/upload-urls/", views.UploadUrlsView.as_view()),
    path("aibooks/<uuid:pk>/start/", views.StartView.as_view()),
    path("aibooks/<uuid:pk>/answers/", views.AnswerView.as_view()),
    path("aibooks/<uuid:pk>/pdf/", views.PdfLinkView.as_view()),
]
