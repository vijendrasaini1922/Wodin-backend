from django.contrib import admin

from .models import Approval, BookPage, BookProject, BookRevision, ChangeRequest, PageElement

for m in (BookProject, BookPage, PageElement, BookRevision, Approval, ChangeRequest):
    admin.site.register(m)
