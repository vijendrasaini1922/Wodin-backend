from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import IsSupport

from .models import SupportTicket, TicketMessage
from .serializers import MessageSerializer, StaffMessageSerializer, StaffTicketSerializer, TicketSerializer


class MyTicketViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = TicketSerializer
    pagination_class = None

    def get_queryset(self):
        return SupportTicket.objects.filter(user=self.request.user).prefetch_related("messages")

    @transaction.atomic
    def perform_create(self, serializer):
        message = serializer.validated_data.pop("message")
        user = self.request.user
        ticket = serializer.save(
            user=user, contact_phone=serializer.validated_data.get("contact_phone") or ("" if user.is_guest else user.phone),
            contact_name=serializer.validated_data.get("contact_name") or user.full_name)
        TicketMessage.objects.create(ticket=ticket, author=user, body=message)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        ticket = self.get_object()
        body = (request.data.get("body") or "").strip()
        if len(body) < 2:
            raise ValidationError({"body": "Write a message."})
        if ticket.status == SupportTicket.Status.CLOSED:
            raise ValidationError({"detail": "This ticket is closed. Please open a new one."})
        msg = TicketMessage.objects.create(ticket=ticket, author=request.user, body=body)
        if ticket.status == SupportTicket.Status.RESOLVED:  # customer replied: reopen
            ticket.status = SupportTicket.Status.OPEN
        ticket.save()
        return Response(MessageSerializer(msg).data, status=status.HTTP_201_CREATED)


class ContactView(APIView):
    """Public contact / help form (no login). Creates a ticket without a user."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "contact"

    def post(self, request):
        d = request.data
        name, message = (d.get("name") or "").strip(), (d.get("message") or "").strip()
        phone, email = (d.get("phone") or "").strip(), (d.get("email") or "").strip()
        errors = {}
        if not name: errors["name"] = "Enter your name."
        if len(message) < 10: errors["message"] = "Tell us a little more (at least 10 characters)."
        if not (phone or email): errors["contact"] = "Provide a phone number or email so we can reply."
        if errors:
            raise ValidationError(errors)
        with transaction.atomic():
            ticket = SupportTicket.objects.create(
                contact_name=name[:120], contact_phone=phone[:16], contact_email=email[:254],
                subject=(d.get("subject") or "Website enquiry")[:160], category=SupportTicket.Category.OTHER)
            TicketMessage.objects.create(ticket=ticket, body=message)
        return Response({"ticket_number": ticket.number}, status=status.HTTP_201_CREATED)


class StaffTicketViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsSupport]
    serializer_class = StaffTicketSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = SupportTicket.objects.prefetch_related("messages")
        for f in ("status", "category"):
            if self.request.query_params.get(f):
                qs = qs.filter(**{f: self.request.query_params[f]})
        return qs

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        ticket = self.get_object()
        body = (request.data.get("body") or "").strip()
        if not body:
            raise ValidationError({"body": "Write a message."})
        msg = TicketMessage.objects.create(ticket=ticket, author=request.user, from_staff=True, body=body,
                                           internal=bool(request.data.get("internal")))
        if ticket.status == SupportTicket.Status.OPEN and not msg.internal:
            ticket.status = SupportTicket.Status.IN_PROGRESS
        if not ticket.assignee_id:
            ticket.assignee = request.user
        ticket.save()
        return Response(StaffMessageSerializer(msg).data, status=status.HTTP_201_CREATED)
