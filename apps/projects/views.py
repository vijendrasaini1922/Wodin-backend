from django.conf import settings
from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDesigner
from apps.photos.models import Photo
from apps.photos.storage import get_storage

from . import services
from .models import BookPage, BookProject, BookRevision, ChangeRequest, PageElement
from .serializers import (ChangeRequestSerializer, PageSerializer, ProjectCreateSerializer, ProjectSerializer,
                          RevisionSerializer)


def _sync_order(project):
    from apps.orders import services as order_services
    order_services.sync_from_project(project)


def _ip(request):
    return request.META.get("REMOTE_ADDR") or None


def _with_preview_urls(snapshot: dict, project: BookProject) -> dict:
    """Attach short-lived signed preview URLs to photo elements; the snapshot itself stays immutable."""
    storage = get_storage()
    ids = {e["photo_id"] for p in snapshot["pages"] for e in p["elements"] if e["photo_id"]}
    keys = dict(Photo.objects.filter(pk__in=ids, trip=project.trip).values_list("id", "preview_key"))
    out = {"page_count": snapshot["page_count"], "pages": []}
    for p in snapshot["pages"]:
        elems = []
        for e in p["elements"]:
            e = dict(e)
            key = next((k for pid, k in keys.items() if str(pid) == e["photo_id"]), "")
            e["url"] = storage.download_url(key, settings.DOWNLOAD_URL_TTL_SECONDS) if key else None
            elems.append(e)
        out["pages"].append({**p, "elements": elems})
    return out


def _revision_payload(rev: BookRevision) -> dict:
    data = RevisionSerializer(rev).data
    data["snapshot"] = _with_preview_urls(rev.snapshot, rev.project)
    return data


class ProjectViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin,
                     viewsets.GenericViewSet):
    """Customer-facing project endpoints."""

    def get_queryset(self):
        return BookProject.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        return ProjectCreateSerializer if self.action == "create" else ProjectSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Customer finished uploading and asks WoDin to start designing."""
        project = self.get_object()
        if project.status != BookProject.Status.DRAFT:
            raise ValidationError({"detail": "Project already submitted."})
        if not project.trip.photos.filter(status=Photo.Status.READY).exists():
            raise ValidationError({"detail": "Upload at least one photo first."})
        from apps.orders import services as order_services
        with transaction.atomic():
            order = order_services.create_order_for_project(project, request.data.get("coupon_code", ""))
            project.status = BookProject.Status.DESIGN
            project.save(update_fields=["status", "updated_at"])
            order_services.sync_from_project(project)
        return Response({**ProjectSerializer(project).data, "order_id": str(order.id), "order_number": order.number})

    @action(detail=True, methods=["get"])
    def revisions(self, request, pk=None):
        project = self.get_object()
        revs = project.revisions.select_related("approval")
        return Response([_revision_payload(r) for r in revs])

    @action(detail=True, methods=["post"], url_path="request-changes")
    def request_changes(self, request, pk=None):
        project = self.get_object()
        rev = project.revisions.order_by("-number").first()
        ser = ChangeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not rev or rev.status != BookRevision.Status.SENT:
            raise ValidationError({"detail": "There is no preview awaiting your review."})
        ser.save(revision=rev, user=request.user)
        project.status = BookProject.Status.CHANGES_REQUESTED
        project.save(update_fields=["status", "updated_at"])
        _sync_order(project)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        project = self.get_object()
        revision_id = request.data.get("revision_id")
        if not revision_id:
            raise ValidationError({"revision_id": "Specify the exact version you are approving."})
        approval = services.approve_revision(project, revision_id, request.user, _ip(request))
        project.refresh_from_db()
        _sync_order(project)
        return Response({"approval_id": str(approval.id), "revision_id": str(approval.revision_id),
                         "approved_at": approval.approved_at})


class DesignerProjectViewSet(viewsets.GenericViewSet):
    """Designer/admin workflow: queue, layout editing, publishing revisions."""

    permission_classes = [IsDesigner]
    serializer_class = ProjectSerializer

    def get_queryset(self):
        status_param = self.request.query_params.get("status") if hasattr(self, "request") and self.request else None
        if status_param == BookProject.Status.CANCELLED:
            return BookProject.objects.filter(status=BookProject.Status.CANCELLED)
        if status_param:
            return BookProject.objects.filter(status=status_param)
        return BookProject.objects.exclude(status__in=[BookProject.Status.DRAFT, BookProject.Status.CANCELLED])

    def list(self, request):
        qs = self.get_queryset().order_by("updated_at")
        return Response(ProjectSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        project = self.get_object()
        project.designer = request.user
        project.save(update_fields=["designer", "updated_at"])
        return Response(ProjectSerializer(project).data)

    @action(detail=True, methods=["get", "put"])
    def pages(self, request, pk=None):
        project = self.get_object()
        if request.method == "GET":
            return Response(PageSerializer(project.pages.prefetch_related("elements"), many=True).data)
        ser = PageSerializer(data=request.data, many=True)
        ser.is_valid(raise_exception=True)
        pages = ser.validated_data
        numbers = [p["number"] for p in pages]
        if len(set(numbers)) != len(numbers) or any(n < 0 or n > project.page_count for n in numbers):
            raise ValidationError({"detail": f"Page numbers must be unique and between 0 (cover) and {project.page_count}."})
        trip_photo_ids = set(project.trip.photos.values_list("id", flat=True))
        with transaction.atomic():
            project.pages.all().delete()
            for p in pages:
                page = BookPage.objects.create(project=project, number=p["number"], layout=p.get("layout", "single"))
                for e in p.get("elements", []):
                    if e.get("photo") and e["photo"].id not in trip_photo_ids:
                        raise ValidationError({"detail": "Photo does not belong to this trip."})
                    PageElement.objects.create(page=page, **e)
        return Response(PageSerializer(project.pages.prefetch_related("elements"), many=True).data)

    @action(detail=True, methods=["post"], url_path="publish-revision")
    def publish_revision(self, request, pk=None):
        project = self.get_object()
        rev = services.publish_revision(project, request.user, request.data.get("notes", ""))
        project.refresh_from_db()
        _sync_order(project)
        return Response(_revision_payload(rev), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="change-requests")
    def change_requests(self, request, pk=None):
        qs = ChangeRequest.objects.filter(revision__project=self.get_object())
        return Response(ChangeRequestSerializer(qs, many=True).data)

    @action(detail=True, methods=["get", "post"], url_path="ai-book")
    def ai_book(self, request, pk=None):
        project = self.get_object()
        from apps.aibook import services as aibook_services
        from apps.aibook.views import _detail
        if request.method == "POST":
            allow_questions = request.data.get("allow_questions", True)
            premium = request.data.get("premium", True)
            prompt = request.data.get("prompt")
            title = request.data.get("title")
            page_count = request.data.get("page_count")
            book = aibook_services.create_from_project(
                project=project,
                user=request.user,
                prompt=prompt,
                title=title,
                page_count=page_count,
                allow_questions=bool(allow_questions),
                premium=bool(premium),
                auto_start=True,
            )
            return Response(_detail(book), status=status.HTTP_201_CREATED)

        latest = project.ai_books.first()
        if not latest:
            return Response(None, status=status.HTTP_200_OK)
        return Response(_detail(latest))

    @action(detail=True, methods=["get"], url_path="ai-book-pages")
    def ai_book_pages(self, request, pk=None):
        from apps.aibook import services as aibook_services
        project = self.get_object()
        latest = project.ai_books.filter(status="ready").first()
        if not latest or not latest.pages:
            raise ValidationError({"detail": "No ready AI Book pages found for this project."})
        if latest.size != aibook_services.project_size(project):
            raise ValidationError({"detail": "This AI Book was made for a different book shape than the customer chose. Generate a new AI Book."})
        # Map AIBookPhoto id -> customer trip Photo id
        id_map = {str(p.id): str(p.trip_photo_id) for p in latest.photos.filter(trip_photo_id__isnull=False)}
        mapped_pages = []
        for p in latest.pages:
            page_copy = {**p}
            elements_copy = []
            for e in p.get("elements", []):
                el_copy = {**e}
                if el_copy.get("kind") == "photo" and el_copy.get("photo"):
                    el_copy["photo"] = id_map.get(str(el_copy["photo"]), el_copy["photo"])
                elements_copy.append(el_copy)
            page_copy["elements"] = elements_copy
            mapped_pages.append(page_copy)
        return Response(mapped_pages)
