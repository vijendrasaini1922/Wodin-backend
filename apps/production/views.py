from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsOperations
from apps.orders.models import Order
from apps.photos.storage import get_storage

from . import services
from .models import ProductionJob
from .serializers import JobSerializer


class _Base(APIView):
    permission_classes = [IsOperations]

    def job(self, pk) -> ProductionJob:
        job = ProductionJob.objects.select_related("order", "revision").filter(pk=pk).first()
        if not job:
            raise NotFound()
        return job


class JobListCreateView(_Base):
    def get(self, request):
        qs = ProductionJob.objects.select_related("order", "revision")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(JobSerializer(qs, many=True).data)

    def post(self, request):
        order = Order.objects.filter(pk=request.data.get("order_id")).first() if request.data.get("order_id") else None
        if not order:
            raise ValidationError({"order_id": "Unknown order."})
        job = services.create_job(order)
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)


class JobDetailView(_Base):
    def get(self, request, pk):
        return Response(JobSerializer(self.job(pk)).data)


class GeneratePdfView(_Base):
    def post(self, request, pk):
        return Response(JobSerializer(services.generate_pdf(self.job(pk))).data)


class PdfDownloadView(_Base):
    def get(self, request, pk):
        job = self.job(pk)
        if not job.print_pdf_key:
            raise NotFound()
        return Response({"url": get_storage().download_url(job.print_pdf_key, settings.DOWNLOAD_URL_TTL_SECONDS)})


class StartPrintingView(_Base):
    def post(self, request, pk):
        return Response(JobSerializer(services.start_printing(self.job(pk), request.user)).data)


class ToQualityCheckView(_Base):
    def post(self, request, pk):
        return Response(JobSerializer(services.move_to_quality_check(self.job(pk), request.user)).data)


class QcView(_Base):
    def post(self, request, pk):
        job = services.record_qc(self.job(pk), request.data.get("checklist") or {}, request.data.get("notes", ""), request.user)
        return Response(JobSerializer(job).data)


class QcFailView(_Base):
    def post(self, request, pk):
        notes = (request.data.get("notes") or "").strip()
        if not notes:
            raise ValidationError({"notes": "Describe what failed."})
        return Response(JobSerializer(services.fail_qc(self.job(pk), notes, request.user)).data)
